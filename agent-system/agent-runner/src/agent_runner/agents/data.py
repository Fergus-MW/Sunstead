"""data-agent — quantitative analysis over the KG: aggregate SQL → chart + insight.

The git/KG agent answers questions with *text*; the data-agent answers `analyze` /
`summarize_metrics` / `query_data` with a *chart*. It runs one structured LLM turn to turn
the question into (SQL, chart spec), executes the SQL programmatically via Aiven MCP
(`aiven_pg_read` — no LLM round-trip on retrieval, §3.5), renders a matplotlib PNG into the
served sites dir, and returns an image artifact + a short summary.

The SQL must return exactly two columns — a text label and a numeric value — so any
`GROUP BY … count/sum/avg … ORDER BY … LIMIT` question becomes a bar/line/pie chart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
from concurrent.futures import ThreadPoolExecutor

from shared.config import KG_DB, KG_PROJECT, KG_SERVICE
from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg

DATA_SYSTEM = f"""You are the data-agent for Sunstead. You answer a quantitative question about the \
knowledge graph (Aiven PostgreSQL) by defining ONE aggregate SQL query and a chart to visualise it.

Target — project "{KG_PROJECT}", service_name "{KG_SERVICE}", database "{KG_DB}".

Schema (a generic property graph):
  nodes(id uuid, type text, name text, properties jsonb, source_id, created_at, updated_at)
  edges(id uuid, source_node_id uuid, target_node_id uuid, type text, properties jsonb, weight)
  -- join edges to nodes via source_node_id / target_node_id; extra fields in properties (->>'key').
Node types: code_module, commit, package (code) · person, meeting, utterance, topic, decision,
  source_document, policy (knowledge). Edge types: touches, imports, calls, part_of, authored,
  attended, said, mentions, relates_to, rationale_for, derived_from.

Rules for the SQL:
- It MUST be a single SELECT returning EXACTLY TWO columns: a short text label, then a numeric value.
- Use an aggregate (count/sum/avg), GROUP BY the label, ORDER BY the value DESC, and a sensible LIMIT (<=15).
- Examples: "commits per author" -> person.name, count(commit) via authored edges; "biggest node types"
  -> nodes.type, count(*); "most-touched modules" -> code_module.name, count(touches edges).

Pick chart_type: "bar" (categories), "barh" (long labels / rankings), "line" (ordered/time), "pie" (shares)."""

# strict tool use is GA on Haiku 4.5 (no beta header). A closed schema —
# strict + additionalProperties:false + every field required — guarantees the
# model's tool_use.input validates exactly, so run() never has to defend against
# a missing/extra key. (Strict needs all properties in `required`, so x_label /
# y_label are required here even though _render falls back to "" for them.)
PLOT_TOOL = {
    "name": "make_chart",
    "description": "Define the aggregate SQL and the chart that answers the question.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "Single SELECT returning [text label, numeric value]."},
            "chart_type": {"type": "string", "enum": ["bar", "barh", "line", "pie"]},
            "title": {"type": "string", "description": "Concise chart title."},
            "x_label": {"type": "string"},
            "y_label": {"type": "string"},
        },
        "required": ["sql", "chart_type", "title", "x_label", "y_label"],
        "additionalProperties": False,
    },
}

# Product palette (matches the FE: dark teal canvas, cream ink, aurora accent).
_BG = "#0b1a17"
_INK = "#f3ead3"
_ACCENT = "#4ade80"

# A dedicated 2-worker pool for the synchronous matplotlib render: asyncio.to_thread()
# uses the shared default executor (also serving every other to_thread + the runner's
# blocking calls), so a burst of charts could starve it. An owned pool caps render
# concurrency at 2 and keeps render load off the shared threads.
_RENDER_POOL = ThreadPoolExecutor(max_workers=2)

# Content-hash cache: the same question often re-runs (refreshes, retries). Keyed on the
# normalized SQL (the only input that drives the rows/PNG), it skips query+render+summarize
# and re-serves the chart already on disk. Bounded to ~128 entries, oldest evicted first
# (dict preserves insertion order, so next(iter(...)) is the oldest key).
_CACHE: dict[str, tuple[str, str]] = {}
_CACHE_MAX = 128


def _cache_key(sql: str) -> str:
    """sha256 of the normalized SQL (collapsed whitespace, lowercased) — stable across reruns."""
    normalized = " ".join(sql.split()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _extract_rows(raw: str) -> tuple[list[str], list[dict]]:
    """Parse aiven_pg_read output (JSON wrapped in an untrusted-data guard) → (fields, rows)."""
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j == -1:
        raise ValueError(f"no JSON in pg_read response: {raw[:120]!r}")
    data = json.loads(raw[i : j + 1])
    fields = (data.get("meta") or {}).get("fields") or []
    return fields, data.get("rows") or []


async def _plan_chart(ctx: TaskCtx, question: str, prior_error: str | None = None) -> dict:
    content = question
    if prior_error:  # one repair turn: feed the failure back, hold the 2-column contract
        content = (f"{question}\n\nThe previous SQL failed: {prior_error}\n"
                   "Fix it, keep the exact 2-column [label, value] contract.")
    resp = await ctx.anthropic.messages.create(
        model=ctx.settings.model_fast,
        max_tokens=1024,
        system=DATA_SYSTEM,
        tools=[PLOT_TOOL],
        tool_choice={"type": "tool", "name": "make_chart"},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "make_chart":
            return block.input if isinstance(block.input, dict) else {}
    raise RuntimeError("model did not return a chart spec")


def _wrap_sql(model_sql: str) -> str:
    """Shape-guard: force any model SELECT into the [label::text, value::float8] contract.

    Wrapping (vs. trusting the model) makes the 2-column [label, value] contract structural —
    a misnamed/extra column or a non-numeric value column surfaces as a Postgres error we can
    repair, not a silently mis-rendered chart. LIMIT 50 caps the row count defensively."""
    return (
        "SELECT label::text AS label, value::float8 AS value "
        f"FROM ({model_sql.rstrip().rstrip(';')}) t(label, value) LIMIT 50"
    )


def _is_pg_error(raw: str) -> bool:
    """The MCP returns plain text; a failed query has no JSON rows block (so _extract_rows raises)
    or carries an explicit error marker. Treat either as a query failure worth one repair turn."""
    try:
        _extract_rows(raw)
    except Exception:
        return True
    return '"error"' in raw or "ERROR" in raw


def _looks_temporal(labels: list[str]) -> bool:
    """Ordered/temporal labels → a line chart reads better than bars. True if labels parse as
    dates (ISO-ish) or are a monotonic numeric sequence (e.g. years, week numbers)."""
    from datetime import date  # stdlib only

    def _as_date(s: str) -> bool:
        s = s.strip()[:10]
        try:
            date.fromisoformat(s)
            return True
        except ValueError:
            return False

    if labels and all(_as_date(s) for s in labels):
        return True
    try:
        nums = [float(s) for s in labels]
    except (TypeError, ValueError):
        return False
    return len(nums) >= 2 and (all(b >= a for a, b in zip(nums, nums[1:]))
                               or all(b <= a for a, b in zip(nums, nums[1:])))


def _choose_chart(labels: list[str], values: list[float], hint: str) -> str:
    """Rule-based chart-type chosen AFTER retrieval, from the actual data shape; the model's
    chart_type is only a hint (it picks before seeing the rows). Rules, in order:
      pie  — 2..6 slices, all values non-negative (shares of a whole)
      line — labels look ordered/temporal (dates or a monotonic numeric run)
      barh — many bars (>8) or any long label (>16 chars), which read badly horizontally
      bar  — default."""
    n = len(labels)
    if 2 <= n <= 6 and all(v >= 0 for v in values):
        return "pie"
    if _looks_temporal(labels):
        return "line"
    if n > 8 or any(len(s) > 16 for s in labels):
        return "barh"
    return "bar"


def _render(spec: dict, labels: list[str], values: list[float], out_path: pathlib.Path) -> None:
    # Object-oriented Figure API (no pyplot): pyplot keeps a process-global figure registry
    # that isn't thread-safe, and this runs in a worker thread (see run()) where two charts may
    # render at once. A standalone Figure has no global state — nothing to use("Agg"), nothing to
    # close, no leak. https://matplotlib.org/stable/users/explain/figure/api_interfaces.html
    from matplotlib.figure import Figure

    kind = spec.get("chart_type", "bar")
    fig = Figure(figsize=(8, 4.6), dpi=130)
    ax = fig.subplots()
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    if kind == "pie":
        ax.pie(values, labels=labels, autopct="%1.0f%%",
               textprops={"color": _INK, "fontsize": 9},
               wedgeprops={"edgecolor": _BG, "linewidth": 1.5})
        ax.axis("equal")
    elif kind == "barh":
        ypos = range(len(labels))
        ax.barh(list(ypos), values, color=_ACCENT)
        ax.set_yticks(list(ypos), labels=labels, color=_INK, fontsize=9)
        ax.invert_yaxis()  # highest at top
    elif kind == "line":
        ax.plot(labels, values, color=_ACCENT, marker="o", linewidth=2)
        ax.tick_params(axis="x", labelrotation=30)
    else:  # bar
        ax.bar(labels, values, color=_ACCENT)
        ax.tick_params(axis="x", labelrotation=30)

    for spine in ax.spines.values():
        spine.set_color(_INK + "40")
    ax.tick_params(colors=_INK, labelsize=9)
    ax.set_title(spec.get("title", ""), color=_INK, fontsize=13, pad=12)
    if kind != "pie":
        ax.set_xlabel(spec.get("x_label", ""), color=_INK, fontsize=10)
        ax.set_ylabel(spec.get("y_label", ""), color=_INK, fontsize=10)
        ax.grid(axis="y" if kind != "barh" else "x", color=_INK + "20", linewidth=0.6)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=_BG, bbox_inches="tight")


async def _summarize(ctx: TaskCtx, question: str, rows: list[dict]) -> str:
    """One short, non-streaming Haiku turn for a natural-language insight; falls back on error."""
    try:
        resp = await ctx.anthropic.messages.create(
            model=ctx.settings.model_fast,
            max_tokens=300,
            system="You are a concise data analyst. Given a question and the resulting rows, "
                   "state the key insight in 1-2 sentences. Only use the numbers shown.",
            messages=[{"role": "user", "content": f"Question: {question}\nRows: {json.dumps(rows)[:2000]}"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if text:
            return text
    except Exception:
        pass
    if rows:  # deterministic fallback
        f = list(rows[0].keys())
        return f"Top result: {rows[0][f[0]]} ({rows[0][f[-1]]}); {len(rows)} categories."
    return "No rows returned."


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.mcp is None or ctx.anthropic is None:
        raise RuntimeError("data-agent requires AIVEN_TOKEN + ANTHROPIC_API_KEY")

    question = first_arg(task.args, "question", "q") or task.intent
    await ctx.activity("planning query", question)
    spec = await _plan_chart(ctx, question)

    async def _run_sql(sql: str) -> str:
        return await ctx.mcp.pg_read(
            _wrap_sql(sql), project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB,
            reasoning=f"data-agent chart for: {question}",
        )

    # Cache hit: the wrapped SQL fully determines rows → PNG, and the PNG is still on disk
    # under its task_id, so re-serve the stored chart_url + summary and skip query/render/summarize.
    cache_key = _cache_key(_wrap_sql(spec["sql"]))
    cached = _CACHE.get(cache_key)
    if cached:
        chart_url, summary = cached
        await ctx.activity("served from cache", chart_url)
        await ctx.trace("text", summary)
        return {
            "question": question, "summary": summary, "chart_url": chart_url,
            "sql": spec.get("sql"), "cached": True,
            "artifacts": [{"kind": "image", "value": chart_url}],
        }

    await ctx.activity("querying the knowledge graph")
    raw = await _run_sql(spec["sql"])
    if _is_pg_error(raw):  # one repair turn: re-plan with the error, re-run the wrapped SQL once
        err = raw[:300]
        await ctx.activity("query failed; repairing", err)
        spec = await _plan_chart(ctx, question, prior_error=err)
        cache_key = _cache_key(_wrap_sql(spec["sql"]))
        raw = await _run_sql(spec["sql"])
        if _is_pg_error(raw):  # still broken — bail gracefully (no chart), like the empty-rows path
            return {"question": question, "summary": "The query could not be run.", "sql": spec.get("sql")}

    fields, rows = _extract_rows(raw)
    if not rows:
        return {"question": question, "summary": "The query returned no rows.", "sql": spec.get("sql")}

    # fields come from meta, rows from data — independently. If meta omitted the
    # field list, fall back to the first row's own columns (insertion order).
    keys = fields or list(rows[0].keys())
    if not keys:
        return {"question": question, "summary": "The query returned rows with no columns.", "sql": spec.get("sql")}
    label_key, value_key = keys[0], keys[-1]
    labels: list[str] = []
    values: list[float] = []
    for r in rows:  # values come back as strings; coerce + skip unusable rows
        try:
            values.append(float(r[value_key]))
            labels.append(str(r[label_key]))
        except (TypeError, ValueError, KeyError):
            continue
    if not values:
        raise RuntimeError(f"no numeric values in column {value_key!r}")

    # Pick the chart type from the actual data shape (the model's chart_type was a pre-retrieval
    # guess) and overwrite the spec so _render uses it.
    spec["chart_type"] = _choose_chart(labels, values, spec.get("chart_type", "bar"))

    await ctx.activity("rendering chart")
    sites_dir = pathlib.Path(os.environ.get("SITES_DIR", "./.sites"))
    out = sites_dir / task.task_id
    out.mkdir(parents=True, exist_ok=True)
    # Render in a worker thread: matplotlib is CPU-bound and synchronous, and the runner shares
    # one event loop — rendering inline would freeze every other in-flight task (and the control
    # loop) until it finished. The await also makes the task cancellable here; an already-running
    # render can't be interrupted (threads aren't killable), but it no longer blocks the loop.
    # A dedicated pool (not the shared default executor) caps render concurrency at 2.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_RENDER_POOL, _render, spec, labels, values, out / "chart.png")

    summary = await _summarize(ctx, question, rows)
    await ctx.trace("text", summary)

    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    url = f"{base}/{task.task_id}/chart.png"
    await ctx.activity("published", url)

    # Cache (bounded, oldest-first eviction) so an identical re-run skips query/render/summarize.
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[cache_key] = (url, summary)

    return {
        "question": question,
        "summary": summary,
        "chart_url": url,
        "rows": len(values),
        "sql": spec.get("sql"),
        "artifacts": [{"kind": "image", "value": url}],
        # Ground the LLM insight against the rows it summarized — same guard the git/KG agent
        # uses (docs/DESIGN.md §7). The harness pops _verify and rides the verdict on the result.
        "_verify": {"claim": summary, "evidence": [json.dumps(r) for r in rows]},
    }

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
import json
import os
import pathlib

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx

KG_PROJECT = os.getenv("KG_PROJECT", "jq01")
KG_SERVICE = os.getenv("KG_SERVICE", "central-kg-pg")
KG_DB = os.getenv("KG_DB", "defaultdb")

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

PLOT_TOOL = {
    "name": "make_chart",
    "description": "Define the aggregate SQL and the chart that answers the question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "Single SELECT returning [text label, numeric value]."},
            "chart_type": {"type": "string", "enum": ["bar", "barh", "line", "pie"]},
            "title": {"type": "string", "description": "Concise chart title."},
            "x_label": {"type": "string"},
            "y_label": {"type": "string"},
        },
        "required": ["sql", "chart_type", "title"],
    },
}

# Product palette (matches the FE: dark teal canvas, cream ink, aurora accent).
_BG = "#0b1a17"
_INK = "#f3ead3"
_ACCENT = "#4ade80"


def _extract_rows(raw: str) -> tuple[list[str], list[dict]]:
    """Parse aiven_pg_read output (JSON wrapped in an untrusted-data guard) → (fields, rows)."""
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j == -1:
        raise ValueError(f"no JSON in pg_read response: {raw[:120]!r}")
    data = json.loads(raw[i : j + 1])
    fields = (data.get("meta") or {}).get("fields") or []
    return fields, data.get("rows") or []


async def _plan_chart(ctx: TaskCtx, question: str) -> dict:
    resp = await ctx.anthropic.messages.create(
        model=ctx.settings.model_fast,
        max_tokens=1024,
        system=DATA_SYSTEM,
        tools=[PLOT_TOOL],
        tool_choice={"type": "tool", "name": "make_chart"},
        messages=[{"role": "user", "content": question}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "make_chart":
            return block.input if isinstance(block.input, dict) else {}
    raise RuntimeError("model did not return a chart spec")


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

    question = task.args.get("question") or task.args.get("q") or task.intent
    await ctx.activity("planning query", question)
    spec = await _plan_chart(ctx, question)

    await ctx.activity("querying the knowledge graph")
    raw = await ctx.mcp.pg_read(
        spec["sql"], project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB,
        reasoning=f"data-agent chart for: {question}",
    )
    fields, rows = _extract_rows(raw)
    if not rows:
        return {"question": question, "summary": "The query returned no rows.", "sql": spec.get("sql")}

    label_key, value_key = fields[0], fields[-1]
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

    await ctx.activity("rendering chart")
    sites_dir = pathlib.Path(os.environ.get("SITES_DIR", "./.sites"))
    out = sites_dir / task.task_id
    out.mkdir(parents=True, exist_ok=True)
    # Render in a worker thread: matplotlib is CPU-bound and synchronous, and the runner shares
    # one event loop — rendering inline would freeze every other in-flight task (and the control
    # loop) until it finished. The await also makes the task cancellable here; an already-running
    # render can't be interrupted (threads aren't killable), but it no longer blocks the loop.
    await asyncio.to_thread(_render, spec, labels, values, out / "chart.png")

    summary = await _summarize(ctx, question, rows)
    await ctx.trace("text", summary)

    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    url = f"{base}/{task.task_id}/chart.png"
    await ctx.activity("published", url)

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

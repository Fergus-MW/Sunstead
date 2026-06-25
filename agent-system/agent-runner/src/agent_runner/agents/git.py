"""Knowledge-graph agent — answers questions from the Sunstead KG via Aiven MCP (docs/AGENT_SYSTEM.md §9).

Despite the filename (it's registered for the git/* intents), this is a **general** graph-query agent: the
graph holds code *and* meeting/decision/people knowledge, and the same `aiven_pg_read` loop answers both.
Target project/service/db are env-driven (KG_PROJECT / KG_SERVICE / KG_DB) so it isn't pinned to one repo's graph.

Two retrieval paths:
- **Canned** (`who_changed`, `blame`, `recent_changes`): templated SQL run directly via `aiven_pg_read`,
  then ONE Haiku turn to phrase the rows — no agentic SQL-gen loop, no tool round-trips (§3.5). A small
  TTL'd module cache short-circuits repeat asks. Any failure falls back to the LLM path below.
- **LLM** (`ask`, `read_git` — and the canned-path fallback): one streamed model turn that writes SQL and
  calls `aiven_pg_read`, deciding for itself what to query.

Both paths feed the same grounding verifier the same way: the fetched rows are returned as `_verify.evidence`.
"""

from __future__ import annotations

import json
import re
import time

from shared.config import KG_DB, KG_PROJECT, KG_SERVICE
from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg

KG_SYSTEM = f"""You answer questions from the Sunstead knowledge graph — a property graph in Aiven PostgreSQL — \
using the `aiven_pg_read` tool. It holds both a seeded codebase AND meeting/decision knowledge; answer either.

aiven_pg_read target: project "{KG_PROJECT}", service_name "{KG_SERVICE}", database "{KG_DB}".

Schema (a generic property graph):
  nodes(id uuid, type text, name text, properties jsonb, embedding vector, source_id, created_at, updated_at)
  edges(id uuid, source_node_id uuid, target_node_id uuid, type text, properties jsonb, weight)
  -- nodes UNIQUE(type, lower(name)); join edges to nodes via source_node_id / target_node_id for names.
  -- extra fields live in properties (JSONB) — read with properties->>'key'.

Node types present: code_module, commit, package (code) · person, meeting, utterance, topic, decision,
source_document (knowledge). Edge types: touches, imports, calls, part_of, re_exports, authored (code) ·
attended, in_meeting, said, mentions, relates_to, rationale_for, derived_from (knowledge).

Examples:
  - "who last touched X"   : person -authored-> commit -touches-> code_module (filter name ILIKE '%X%')
  - "what was decided about Y" : decision nodes + relates_to/mentions/rationale_for; read properties for text
  - "who attended meeting Z"   : person -attended-> meeting
If unsure of exact types, discover first: `select distinct type from nodes` / `select distinct type from edges`.

Write one SQL SELECT (recursive CTEs allowed), call `aiven_pg_read`, then answer in 1-3 sentences.
Only state what the rows actually show — never invent people, files, decisions, or meetings."""

# Intents we can answer with a fixed query shape (no agentic SQL-gen). Everything else
# (`ask`, `read_git`) stays on the LLM tool_runner below.
_CANNED_INTENTS = {"who_changed", "blame", "recent_changes"}

# Tiny TTL'd result cache for the canned path only — the questions ("who owns auth?",
# "recent changes?") repeat across a meeting and the KG is slow-moving. Keyed by
# (intent, normalized-entity); value is (answer, evidence, monotonic-ts). Bounded so a
# long-lived runner can't grow it without limit. `ask` is never cached (open-ended).
_CACHE: dict[tuple[str, str], tuple[str, list[str], float]] = {}
_CACHE_TTL = 60.0   # seconds — fresh enough for a live meeting, cheap enough to keep
_CACHE_MAX = 256


def _lit(s: str) -> str:
    """Inline a user term into SQL as a single-quoted literal, escaping embedded quotes.

    The canned templates interpolate an entity name from user args, so this is the only
    untrusted->SQL hop. We double single quotes (standard SQL escaping) and strip NULs;
    combined with the single-SELECT + LIMIT guard in `_one_select`, that keeps a name like
    `o'brien` or `'; drop …` inert. Not a substitute for params, but these queries are
    fixed-shape reads against a read-only role."""
    return "'" + s.replace("\x00", "").replace("'", "''") + "'"


def _one_select(sql: str, limit: int) -> str:
    """Guard a templated query: exactly one statement, a SELECT, with a hard LIMIT appended.

    Defense in depth over `_lit` — even if interpolation went wrong, this rejects multi-
    statement payloads and anything that isn't a plain SELECT before it reaches Aiven."""
    one = sql.strip().rstrip(";").strip()
    if ";" in one:
        raise ValueError("canned SQL must be a single statement")
    if not re.match(r"(?is)^\s*(with|select)\b", one):
        raise ValueError("canned SQL must be a SELECT")
    if not re.search(r"(?is)\blimit\s+\d+\s*$", one):
        one = f"{one}\nLIMIT {int(limit)}"
    return one


def _entity_term(task: TaskCreatePayload) -> str:
    """Best-effort target for who_changed/blame: an explicit entity arg, else strip the
    question down to the file/module hint (drop the boilerplate 'who changed/last touched')."""
    term = first_arg(task.args, "entity", "module", "file", "path").strip()
    if term:
        return term
    q = first_arg(task.args, "question", "q").strip()
    q = re.sub(r"(?is)\b(who|what|which|has|have|the|most|last|recently|changed|change|"
               r"touched|touch|authored|author|edited|edit|modified|commits?|to|in|on|"
               r"for|of|a|an)\b", " ", q)
    return re.sub(r"[^\w./\-]+", " ", q).strip()


def _canned_sql(intent: str, term: str) -> str:
    """Templated SQL for a canned intent. `term` is already known non-empty for who_changed/blame."""
    if intent in ("who_changed", "blame"):
        # person -authored-> commit -touches-> code_module, ranked by how many of that
        # module's commits each person authored.
        return f"""
            SELECT p.name AS person, count(*) AS commits
            FROM nodes p
            JOIN edges a ON a.source_node_id = p.id AND a.type = 'authored'
            JOIN nodes c ON c.id = a.target_node_id AND c.type = 'commit'
            JOIN edges t ON t.source_node_id = c.id AND t.type = 'touches'
            JOIN nodes m ON m.id = t.target_node_id AND m.type = 'code_module'
            WHERE p.type = 'person' AND m.name ILIKE '%' || {_lit(term)} || '%'
            GROUP BY p.name
            ORDER BY commits DESC
            LIMIT 10"""
    # recent_changes: latest commits + the modules each touched (cheap left-join + agg).
    return """
        SELECT c.name AS commit,
               c.created_at AS at,
               coalesce(string_agg(DISTINCT m.name, ', '), '') AS modules
        FROM nodes c
        LEFT JOIN edges t ON t.source_node_id = c.id AND t.type = 'touches'
        LEFT JOIN nodes m ON m.id = t.target_node_id AND m.type = 'code_module'
        WHERE c.type = 'commit'
        GROUP BY c.id, c.name, c.created_at
        ORDER BY c.created_at DESC
        LIMIT 10"""


def _extract_rows(raw: str) -> list[dict]:
    """Parse aiven_pg_read output (JSON in an untrusted-data guard) → rows (cf. agents/data.py)."""
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j == -1:
        raise ValueError(f"no JSON in pg_read response: {raw[:120]!r}")
    return json.loads(raw[i : j + 1]).get("rows") or []


def _cache_get(key: tuple[str, str]) -> tuple[str, list[str]] | None:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    answer, evidence, ts = hit
    if time.monotonic() - ts > _CACHE_TTL:   # stale → drop and miss
        _CACHE.pop(key, None)
        return None
    return answer, evidence


def _cache_put(key: tuple[str, str], answer: str, evidence: list[str]) -> None:
    if len(_CACHE) >= _CACHE_MAX:            # bound memory — evict the oldest by stored ts
        oldest = min(_CACHE, key=lambda k: _CACHE[k][2])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (answer, evidence, time.monotonic())


async def _phrase(ctx: TaskCtx, question: str, rows: list[dict]) -> str:
    """One non-streaming Haiku turn (no tools) to turn rows into 1-2 grounded sentences."""
    resp = await ctx.anthropic.messages.create(
        model=ctx.settings.model_fast,
        max_tokens=300,
        system="You answer a question from the Sunstead knowledge graph using ONLY the rows given. "
               "State the answer in 1-2 sentences. Never invent names, files, or numbers not in the rows.",
        messages=[{"role": "user", "content": f"Question: {question}\nRows: {json.dumps(rows)[:2000]}"}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


async def _canned(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    """Fast path for who_changed/blame/recent_changes: one templated read + one phrasing turn.

    Returns the same shape (question/answer/_verify) as the LLM path, with the fetched rows as
    grounding evidence. Raises on any problem so `run()` can fall back to the agentic loop."""
    question = first_arg(task.args, "question", "q") or task.intent
    intent = task.intent

    if intent in ("who_changed", "blame"):
        term = _entity_term(task)
        if not term:                          # no target to filter on → let the LLM path handle it
            raise ValueError("no entity term for who_changed/blame")
    else:
        term = ""                             # recent_changes is global

    key = (intent, term.lower())
    cached = _cache_get(key)
    if cached is not None:
        answer, evidence = cached
        await ctx.activity("answered from cache")
        await ctx.trace("text", answer)
        return {"question": question, "answer": answer, "_verify": {"claim": answer, "evidence": evidence}}

    await ctx.activity("querying the knowledge graph", term or intent)
    sql = _one_select(_canned_sql(intent, term), limit=10)
    raw = await ctx.mcp.pg_read(
        sql, project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB,
        reasoning=f"kg-agent canned {intent}: {question}",
    )
    rows = _extract_rows(raw)
    # Same evidence shape the LLM path records — head + the rows the answer must be grounded in.
    evidence = [f"[aiven_pg_read: {intent} {term}]\n{raw[:2000]}"]

    if not rows:
        answer = "No matching records were found in the knowledge graph."
    else:
        answer = await _phrase(ctx, question, rows) or "No matching records were found."
    await ctx.trace("text", answer)

    _cache_put(key, answer, evidence)
    return {"question": question, "answer": answer, "_verify": {"claim": answer, "evidence": evidence}}


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.mcp is None or ctx.anthropic is None:
        raise RuntimeError("kg-agent requires AIVEN_TOKEN + ANTHROPIC_API_KEY")

    # Fast path: fixed-shape intents get a templated read + one phrasing turn (no agentic
    # SQL-gen). Any failure falls through to the agentic LLM loop below — never a hard fail.
    if task.intent in _CANNED_INTENTS:
        try:
            return await _canned(task, ctx)
        except Exception as e:
            await ctx.activity("canned path fell back", type(e).__name__)

    question = first_arg(task.args, "question", "q") or task.intent
    await ctx.activity("querying the knowledge graph")

    # Capture the rows each aiven_pg_read returns — these are the *evidence* the grounding
    # verifier checks the answer against (docs/DESIGN.md §7), otherwise lost inside the runner.
    evidence: list[str] = []

    def _record(name: str, args: dict, text: str) -> None:
        q = first_arg(args, "query", "sql").strip()
        head = f"{name}: {q[:160]}" if q else name
        evidence.append(f"[{head}]\n{text[:2000]}")

    runner = ctx.anthropic.beta.messages.tool_runner(
        model=ctx.settings.model_fast,
        max_tokens=2048,
        system=KG_SYSTEM,
        tools=ctx.mcp.llm_tools(on_result=_record),
        messages=[{"role": "user", "content": question}],
    )

    # Forward each turn's blocks to agent.trace so the dashboard shows the agent working.
    # This is per-turn (coarse), not token-by-token: the Python tool runner yields complete
    # messages per turn, and model_fast (Haiku 4.5) doesn't take adaptive thinking/effort.
    # For true token-streamed reasoning here, swap to a manual stream() tool loop on a
    # thinking-capable model (docs/AGENT_SYSTEM.md §3.5).
    answer = ""
    async for message in runner:
        for block in message.content:
            if block.type == "thinking" and getattr(block, "thinking", "").strip():
                await ctx.trace("thinking", block.thinking)
            elif block.type == "text" and block.text.strip():
                await ctx.trace("text", block.text)
                answer = block.text

    # `_verify` is consumed by the harness pre-emit gate (docs/DESIGN.md §7) and stripped
    # from the emitted result — the grounding check runs against the rows we captured above.
    return {
        "question": question,
        "answer": answer,
        "_verify": {"claim": answer, "evidence": evidence},
    }

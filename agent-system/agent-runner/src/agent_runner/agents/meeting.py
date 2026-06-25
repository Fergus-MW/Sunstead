"""meeting-ops agent — turn a meeting transcript into a recap, action items, and decisions,
and write the action items + decisions back into the KG so the graph captures the meeting's outcomes.

Intents: `recap` (a short summary), `action_items` (who owns what), `decisions` (what was decided +
why). All three are produced from one structured LLM turn; the intent just sets what we emphasise.

Transcript source: the planner tails `meeting.transcript` and injects a rolling window into
`task.args["transcript"]` (so this agent stays a stateless `run()`). For isolated testing, pass
`transcript` directly. Write-back is via `aiven_pg_write` (the suite's MCP data mandate, §3) — best
effort: the recap is still returned even if the write fails. `kg.updates` is intentionally NOT used
(central-kg-api has no consumer for it).
"""

from __future__ import annotations

import json
import re

from shared.config import KG_DB, KG_PROJECT, KG_SERVICE
from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg

OPS_SYSTEM = """You are the meeting-ops agent for Sunstead. Given a meeting transcript, extract its \
outcomes precisely. Only use what the transcript actually says — never invent owners, decisions, or \
tasks. Write a concise recap (2-4 sentences). List concrete action items (what must be done, and the \
owner if one was named). List decisions that were actually made (with the stated rationale if given). \
If a category has nothing, return an empty list. For every action item and decision, quote the exact \
transcript line it came from in evidence_quote, and the speaker who said it in speaker if identifiable. \
If no owner is explicitly named, leave owner empty — never infer one."""

# strict structured output (GA on Opus 4.8, no beta header): `strict: True` is a top-level field on
# the tool (sibling of input_schema), and every object needs additionalProperties:False + required.
OPS_TOOL = {
    "name": "meeting_outcomes",
    "description": "The structured outcomes extracted from the meeting transcript.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "recap": {"type": "string", "description": "A concise 2-4 sentence summary of the meeting."},
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "description": {"type": "string"},
                        "owner": {"type": "string", "description": "Name of the owner, or empty if none named."},
                        "evidence_quote": {"type": "string", "description": "Verbatim transcript line this came from."},
                        "speaker": {"type": "string", "description": "Who said it, or empty if not identifiable."},
                    },
                    "required": ["description", "owner", "evidence_quote", "speaker"],
                },
            },
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "decision": {"type": "string"},
                        "rationale": {"type": "string", "description": "Why, if stated; else empty."},
                        "evidence_quote": {"type": "string", "description": "Verbatim transcript line this came from."},
                        "speaker": {"type": "string", "description": "Who said it, or empty if not identifiable."},
                    },
                    "required": ["decision", "rationale", "evidence_quote", "speaker"],
                },
            },
        },
        "required": ["recap", "action_items", "decisions"],
    },
}


def _lit(s: str) -> str:
    """A safe single-quoted SQL string literal (LLM text → SQL; double the quotes)."""
    return "'" + str(s).replace("'", "''") + "'"


def _jsonb(d: dict) -> str:
    return f"CAST({_lit(json.dumps(d))} AS jsonb)"


def _slug(text: str) -> str:
    """Normalize free text → a stable, URL-ish slug (lowercase, alnum, dash-joined, ~80 chars).

    Used to build a deterministic node name `{meeting_id}::{slug}` so re-running recap within a
    meeting collapses onto the same node via ON CONFLICT(type, lower(name)), while items in other
    meetings stay distinct."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "item"


def _insert_edge(s_type: str, s_name: str, t_type: str, t_name: str, etype: str, props: dict) -> str:
    """Build one idempotent edge upsert that resolves endpoints by (type, lower(name)).

    SELECT-from-nodes form so we don't need the freshly-minted uuids; ON CONFLICT keeps re-runs flat."""
    return (
        "INSERT INTO edges (source_node_id, target_node_id, type, properties) "
        f"SELECT s.id, t.id, {_lit(etype)}, {_jsonb(props)} FROM nodes s, nodes t "
        f"WHERE s.type = {_lit(s_type)} AND lower(s.name) = lower({_lit(s_name)}) "
        f"AND t.type = {_lit(t_type)} AND lower(t.name) = lower({_lit(t_name)}) "
        "ON CONFLICT (source_node_id, target_node_id, type) "
        "DO UPDATE SET properties = edges.properties || EXCLUDED.properties;"
    )


def _insert_nodes(node_type: str, items: list[tuple[str, dict]]) -> str | None:
    """Build one multi-row upsert for nodes of a type. items = [(name, properties), ...]."""
    rows = []
    for name, props in items:
        name = (name or "").strip()[:200]
        if not name:
            continue
        rows.append(f"({_lit(node_type)}, {_lit(name)}, {_jsonb(props)})")
    if not rows:
        return None
    return (
        "INSERT INTO nodes (type, name, properties) VALUES\n"
        + ",\n".join(rows)
        + "\nON CONFLICT (type, lower(name)) DO UPDATE SET "
        "properties = nodes.properties || EXCLUDED.properties, updated_at = now();"
    )


async def _exec(ctx: TaskCtx, sql: str | None, reasoning: str, label: str) -> bool:
    """Run one best-effort write; True on success. A write hiccup never fails the task — the
    recap still stands (the suite's MCP data mandate is best-effort, §3)."""
    if not sql:
        return False
    try:
        await ctx.mcp.pg_write(
            sql, project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB, reasoning=reasoning,
        )
        return True
    except Exception as e:
        await ctx.activity("kg write skipped", f"{label}: {type(e).__name__}")
        return False


async def _write_back(ctx: TaskCtx, meeting_id: str, outcomes: dict) -> tuple[int, int]:
    """Best-effort: persist the meeting's outcomes into the KG as NODES + EDGES via MCP.

    Writes (idempotently): a `meeting` node; one `action_item`/`decision` node per outcome, each
    named `{meeting_id}::{slug}` so recap re-runs collapse; `in_meeting` edges from each item to the
    meeting; and for every named owner a `person` node + an `owns` edge. Returns (nodes, edges)."""
    nodes_written = edges_written = 0
    base = {"meeting_id": meeting_id, "source": "meeting-ops"}

    # action_item / decision nodes: deterministic name, human-readable text in properties.title.
    ai, dec = [], []  # each: (name, properties, title) — title is the slug source + display text
    for it in outcomes.get("action_items", []):
        title = (it.get("description") or "").strip()
        if not title:
            continue
        props = {
            **base, "title": title, "status": "open",
            "owner": (it.get("owner") or "").strip() or None,
            "evidence_quote": it.get("evidence_quote") or None,
            "speaker": it.get("speaker") or None,
        }
        ai.append((f"{meeting_id}::{_slug(title)}", props, title))
    for it in outcomes.get("decisions", []):
        title = (it.get("decision") or "").strip()
        if not title:
            continue
        props = {
            **base, "title": title,
            "rationale": it.get("rationale") or None,
            "evidence_quote": it.get("evidence_quote") or None,
            "speaker": it.get("speaker") or None,
        }
        dec.append((f"{meeting_id}::{_slug(title)}", props, title))

    # 1) meeting node (idempotent on its name == meeting_id).
    if await _exec(
        ctx, _insert_nodes("meeting", [(meeting_id, {**base, "title": meeting_id})]),
        f"meeting-ops upserting meeting node {meeting_id}", "meeting",
    ):
        nodes_written += 1

    # 2) action_item / decision nodes.
    for node_type, items in (("action_item", ai), ("decision", dec)):
        node_rows = [(name, props) for name, props, _ in items]
        if await _exec(
            ctx, _insert_nodes(node_type, node_rows),
            f"meeting-ops persisting {node_type}s from meeting {meeting_id}", node_type,
        ):
            nodes_written += len(node_rows)

    # 3) item -in_meeting-> meeting edges.
    for node_type, items in (("action_item", ai), ("decision", dec)):
        for name, _, _ in items:
            if await _exec(
                ctx, _insert_edge(node_type, name, "meeting", meeting_id, "in_meeting", {**base}),
                f"meeting-ops linking {node_type} to meeting {meeting_id}", f"{node_type}->in_meeting",
            ):
                edges_written += 1

    # 4) person nodes + person -owns-> action_item edges (only for explicitly-named owners).
    for name, props, _ in ai:
        owner = props.get("owner")
        if not owner:
            continue
        if await _exec(
            ctx, _insert_nodes("person", [(owner, {"source": "meeting-ops"})]),
            f"meeting-ops upserting owner {owner}", "person",
        ):
            nodes_written += 1
        if await _exec(
            ctx, _insert_edge("person", owner, "action_item", name, "owns", {**base}),
            f"meeting-ops linking owner {owner} to action_item", "person->owns",
        ):
            edges_written += 1

    return nodes_written, edges_written


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("meeting-ops requires ANTHROPIC_API_KEY")

    transcript = first_arg(task.args, "transcript", "text").strip()
    if not transcript:
        return {"summary": "No transcript was available to summarise.", "intent": task.intent,
                "action_items": [], "decisions": []}

    await ctx.activity("reading the meeting", f"{len(transcript)} chars")
    resp = await ctx.anthropic.messages.create(
        model=ctx.settings.model_smart,
        max_tokens=1500,
        system=OPS_SYSTEM,
        tools=[OPS_TOOL],
        tool_choice={"type": "tool", "name": "meeting_outcomes"},
        messages=[{"role": "user", "content": transcript}],
    )
    outcomes: dict = {"recap": "", "action_items": [], "decisions": []}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "meeting_outcomes":
            outcomes = block.input if isinstance(block.input, dict) else outcomes
            break

    recap = outcomes.get("recap", "")
    if recap:
        await ctx.trace("text", recap)

    nodes_written = edges_written = 0
    if ctx.mcp is not None:
        await ctx.activity("writing outcomes to the graph")
        nodes_written, edges_written = await _write_back(ctx, ctx.meeting_id, outcomes)
        if nodes_written or edges_written:
            await ctx.activity("graph updated", f"{nodes_written} node(s), {edges_written} edge(s)")

    # The result emphasises the requested intent but always carries the full set.
    return {
        "intent": task.intent,
        "recap": recap,
        "action_items": outcomes.get("action_items", []),
        "decisions": outcomes.get("decisions", []),
        "nodes_written": nodes_written,
        "edges_written": edges_written,
        "artifacts": [{"kind": "text", "value": recap or "(no recap)"}],
    }

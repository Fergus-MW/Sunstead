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
import os

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx

KG_PROJECT = os.getenv("KG_PROJECT", "jq01")
KG_SERVICE = os.getenv("KG_SERVICE", "central-kg-pg")
KG_DB = os.getenv("KG_DB", "defaultdb")

OPS_SYSTEM = """You are the meeting-ops agent for Sunstead. Given a meeting transcript, extract its \
outcomes precisely. Only use what the transcript actually says — never invent owners, decisions, or \
tasks. Write a concise recap (2-4 sentences). List concrete action items (what must be done, and the \
owner if one was named). List decisions that were actually made (with the stated rationale if given). \
If a category has nothing, return an empty list."""

OPS_TOOL = {
    "name": "meeting_outcomes",
    "description": "The structured outcomes extracted from the meeting transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recap": {"type": "string", "description": "A concise 2-4 sentence summary of the meeting."},
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "owner": {"type": "string", "description": "Name of the owner, or empty if none named."},
                    },
                    "required": ["description"],
                },
            },
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string"},
                        "rationale": {"type": "string", "description": "Why, if stated; else empty."},
                    },
                    "required": ["decision"],
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


async def _write_back(ctx: TaskCtx, meeting_id: str, outcomes: dict) -> int:
    """Best-effort: persist action_item + decision nodes to the KG via MCP. Returns count written."""
    written = 0
    base = {"meeting_id": meeting_id, "source": "meeting-ops"}
    ai = [
        (it["description"], {**base, "owner": it.get("owner") or None, "status": "open"})
        for it in outcomes.get("action_items", []) if it.get("description")
    ]
    dec = [
        (it["decision"], {**base, "rationale": it.get("rationale") or None})
        for it in outcomes.get("decisions", []) if it.get("decision")
    ]
    for node_type, items in (("action_item", ai), ("decision", dec)):
        sql = _insert_nodes(node_type, items)
        if not sql:
            continue
        try:
            await ctx.mcp.pg_write(
                sql, project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB,
                reasoning=f"meeting-ops persisting {node_type}s from meeting {meeting_id}",
            )
            written += len(items)
        except Exception as e:  # never fail the task on a write hiccup — the recap still stands
            await ctx.activity("kg write skipped", f"{node_type}: {type(e).__name__}")
    return written


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("meeting-ops requires ANTHROPIC_API_KEY")

    transcript = (task.args.get("transcript") or task.args.get("text") or "").strip()
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

    written = 0
    if ctx.mcp is not None:
        await ctx.activity("writing outcomes to the graph")
        written = await _write_back(ctx, ctx.meeting_id, outcomes)
        if written:
            await ctx.activity("graph updated", f"{written} node(s)")

    # The result emphasises the requested intent but always carries the full set.
    return {
        "intent": task.intent,
        "recap": recap,
        "action_items": outcomes.get("action_items", []),
        "decisions": outcomes.get("decisions", []),
        "nodes_written": written,
        "artifacts": [{"kind": "text", "value": recap or "(no recap)"}],
    }

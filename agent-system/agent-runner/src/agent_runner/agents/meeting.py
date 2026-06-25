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

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg
from shared.ingest import Source, persist

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


async def _write_back(ctx: TaskCtx, meeting_id: str, outcomes: dict) -> tuple[int, int]:
    """Best-effort: persist the meeting's outcomes into the KG via the universal ingestor's
    `persist()`, so episode keying + owner entity-resolution live in ONE place. Builds a `meeting`
    node; one `action_item`/`decision` per outcome (episodes → scope-keyed `{meeting_id}::{slug}`,
    so the same text across two meetings stays distinct — the silent-merge bug DESIGN §6 records);
    `in_meeting` edges; and, for every explicitly-named owner, a `person` node + an `owns` edge
    (the owner name is canonicalised on write so "Sam"/"Sam P." collapse). Returns (nodes, edges)."""
    def _clean(d: dict) -> dict:
        return {k: v for k, v in d.items() if v}  # drop empties → tidy JSONB

    nodes: list[dict] = [{"type": "meeting", "name": meeting_id, "properties": {"title": meeting_id}}]
    edges: list[dict] = []

    for it in outcomes.get("action_items", []):
        title = (it.get("description") or "").strip()
        if not title:
            continue
        nodes.append({"type": "action_item", "name": title, "properties": _clean({
            "title": title, "status": "open",
            "evidence_quote": it.get("evidence_quote"), "speaker": it.get("speaker")})})
        edges.append({"source": ("action_item", title), "target": ("meeting", meeting_id),
                      "type": "in_meeting", "properties": {}})
        owner = (it.get("owner") or "").strip()
        if owner:  # only link owners the transcript actually named (never infer one)
            nodes.append({"type": "person", "name": owner, "properties": {}})
            edges.append({"source": ("person", owner), "target": ("action_item", title),
                          "type": "owns", "properties": {}})

    for it in outcomes.get("decisions", []):
        title = (it.get("decision") or "").strip()
        if not title:
            continue
        nodes.append({"type": "decision", "name": title, "properties": _clean({
            "title": title, "rationale": it.get("rationale"),
            "evidence_quote": it.get("evidence_quote"), "speaker": it.get("speaker")})})
        edges.append({"source": ("decision", title), "target": ("meeting", meeting_id),
                      "type": "in_meeting", "properties": {}})

    res = await persist(ctx, Source(kind="meeting-ops", scope=meeting_id), nodes, edges)
    return res["nodes"], res["edges"]


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

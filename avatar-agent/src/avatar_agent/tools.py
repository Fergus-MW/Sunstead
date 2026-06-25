"""Custom tools — the LLM-callable functions whose handlers call the backend API.

Design rules (PRD §5.2):
  * Each tool is a standalone `@function_tool`; its docstring IS the schema the
    model sees, so it must read as instructions to the model.
  * Adding a capability = add a function and list it in `BACKEND_TOOLS`. The core
    conversation loop in `agent.py` never changes (user story CT-3).
  * Every handler times itself via the tool-call log and degrades gracefully on a
    backend error, returning a sentence the avatar can speak (CT-4) rather than
    raising into the pipeline.

The handlers reach the backend + tool log through `context.userdata`
(an `AgentRuntime`, set when the AgentSession is created).
"""

from __future__ import annotations

from typing import Any

from livekit.agents import RunContext, StopResponse
from livekit.agents.llm import function_tool

from .backend import BackendError
from .runtime import AgentRuntime


@function_tool
async def skip_turn(context: RunContext[AgentRuntime]) -> None:
    """Stay silent this turn and say nothing at all. Call this whenever the latest
    thing said was NOT addressed to you — for example the participants are talking
    to each other, or your name was not mentioned and no question or request was
    directed at you. Do not reply, interject, or narrate in those cases — call this
    instead. When you are unsure whether you were addressed, prefer calling this.
    """
    # Record the skip for the post-call timeline, then halt response generation so
    # the avatar produces no speech for this turn (LiveKit's StopResponse).
    rt = context.userdata
    async with rt.tools_log.span("skip_turn", {}) as call:
        call.ok({"skipped": True})
    raise StopResponse


@function_tool
async def lookup_context(context: RunContext[AgentRuntime], query: str) -> str:
    """Search the company knowledge graph for people, meetings, tasks, code, or
    documents relevant to a natural-language query, and return a short summary.
    Use this whenever a participant asks about something that might be in the
    company's data ("what's the status of the billing project", "who owns auth").

    Args:
        query: A natural-language description of what to look up.
    """
    rt = context.userdata
    async with rt.tools_log.span("lookup_context", {"query": query}) as call:
        try:
            data = await rt.backend.query(query)
        except BackendError as exc:
            call.fail(exc.reason)
            return f"I couldn't reach the knowledge base just now — {exc.reason}."
        call.ok(data)
        return _summarize_nodes(data)


@function_tool
async def get_entity(context: RunContext[AgentRuntime], entity_id: str, hops: int = 1) -> str:
    """Fetch one specific entity by its id plus its immediate neighborhood — its
    related people, tasks, code, or documents. Use this to drill into an entity
    you already found via `lookup_context` and need more detail on.

    Args:
        entity_id: The id of the node to expand.
        hops: How many relationship hops to include (1 or 2). Keep it small.
    """
    rt = context.userdata
    async with rt.tools_log.span("get_entity", {"entity_id": entity_id, "hops": hops}) as call:
        try:
            data = await rt.backend.entity(entity_id, hops=max(1, min(hops, 2)))
        except BackendError as exc:
            call.fail(exc.reason)
            return f"I couldn't look that entity up — {exc.reason}."
        call.ok(data)
        return _summarize_nodes(data)


@function_tool
async def recent_activity(context: RunContext[AgentRuntime], since: str | None = None) -> str:
    """List recent activity / updates across the company — what changed lately.
    Use this for questions like "what's happened on the project this week" or
    "any recent updates on the migration".

    Args:
        since: Optional ISO-8601 timestamp lower bound (e.g. 2026-06-18T00:00:00Z).
    """
    rt = context.userdata
    async with rt.tools_log.span("recent_activity", {"since": since}) as call:
        try:
            data = await rt.backend.timeline(since=since)
        except BackendError as exc:
            call.fail(exc.reason)
            return f"I couldn't pull the recent activity — {exc.reason}."
        call.ok(data)
        events = _as_list(data, "events")
        if not events:
            return "I didn't find any recent activity in that window."
        lines = [
            f"- {e.get('kind', 'update')}: {e.get('body') or e.get('summary') or ''}".strip()
            for e in events[:8]
        ]
        return "Here's the recent activity:\n" + "\n".join(lines)


@function_tool
async def record_action_item(
    context: RunContext[AgentRuntime], description: str, owner: str | None = None
) -> str:
    """Record an action item or follow-up from the meeting into the company's
    system. This is a WRITE — only call it when a participant clearly asks you to
    capture a task, decision, or follow-up.

    Args:
        description: What needs to be done.
        owner: Optional name of the person responsible.
    """
    rt = context.userdata
    body = description if not owner else f"{description} (owner: {owner})"
    async with rt.tools_log.span(
        "record_action_item", {"description": description, "owner": owner}
    ) as call:
        try:
            # The tool-call correlation id doubles as the idempotency key so an
            # at-least-once retry of the same call can't create a duplicate.
            data = await rt.backend.append_event(
                node="meeting",
                kind="action_item",
                body=body,
                idempotency_key=call.correlation_id,
            )
        except BackendError as exc:
            call.fail(exc.reason)
            return f"I wasn't able to save that action item — {exc.reason}. Could you try again?"
        call.ok(data)
        who = f" for {owner}" if owner else ""
        return f"Done — I've recorded the action item{who}."


# Maps each delegatable intent to the arg key the receiving worker reads from the
# task payload (web-agent reads `brief`, git/data read `question`, echo is dev).
# Must stay a subset of the controlled `TaskIntent` vocab in
# agent-system/shared/contracts.py — the gateway re-validates and rejects others.
DELEGATE_ARG_KEY = {
    "build_website": "brief",
    "update_website": "brief",
    "read_git": "question",
    "who_changed": "question",
    "recent_changes": "question",
    "blame": "question",
    "analyze": "question",
    "summarize_metrics": "question",
    "query_data": "question",
    "echo": "text",
}


@function_tool
async def delegate(context: RunContext[AgentRuntime], intent: str, brief: str) -> str:
    """Hand a piece of real work off to the specialist agent team, who do it in
    the background while the meeting continues. Use this when a participant asks
    you to BUILD or DO something that takes real work, not just answer a question
    you can look up. The result appears on the team's dashboard shortly after.

    Choose the closest `intent`:
      - build_website: create a new web page / landing site from a description.
      - update_website: change a site that was already built.
      - read_git / who_changed / recent_changes / blame: deeper questions about
        the codebase's history than a quick lookup (who last touched a module, what
        changed recently). Prefer `lookup_context` for simple facts.
      - analyze / summarize_metrics / query_data: crunch or summarize data.

    This is fire-and-forget: confirm out loud that you're on it (the team will put
    the result on screen) — do not promise to read the result back yourself.

    Args:
        intent: One of the intents listed above.
        brief: A clear, self-contained description of what to build or do, in your
            own words, capturing what the participant asked for.
    """
    rt = context.userdata
    arg_key = DELEGATE_ARG_KEY.get(intent)
    async with rt.tools_log.span("delegate", {"intent": intent, "brief": brief}) as call:
        if arg_key is None:
            call.fail(f"unknown intent {intent!r}")
            return (
                "I can't delegate that kind of task — I can build or update a site, "
                "dig into the code history, or analyze data."
            )
        if rt.gateway is None or not rt.gateway.enabled:
            call.fail("delegation not configured")
            return "I can't hand work to the team right now — the task service isn't reachable."
        try:
            data = await rt.gateway.delegate(
                intent=intent,
                args={arg_key: brief},
                meeting_id=rt.meeting_id,
            )
        except BackendError as exc:
            call.fail(exc.reason)
            return f"I couldn't hand that off to the team just now — {exc.reason}. Want me to try again?"
        call.ok(data)
        return "On it — I've handed that to the team and it'll show up on the dashboard shortly."


# Tools the avatar ALWAYS has — stay silent when not addressed, read the KG, and
# capture action items. Append here to expose a new always-on tool; nothing else
# in the pipeline changes (CT-3).
BASE_TOOLS = [
    skip_turn,
    lookup_context,
    get_entity,
    recent_activity,
    record_action_item,
]

# `delegate` is opt-in via AVATAR_DELEGATES (DESIGN §6). By default the avatar emits
# `meeting.transcript` and the planner does the routing, so delegate() is excluded to
# avoid two brains delegating the same utterance. agent.py composes the active set.
BACKEND_TOOLS = [*BASE_TOOLS, delegate]  # full set (AVATAR_DELEGATES mode / back-compat)


# ── helpers ──────────────────────────────────────────────────────────────────


def _as_list(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(data.get("results"), list):
            return data["results"]
    if isinstance(data, list):
        return data
    return []


def _summarize_nodes(data: Any) -> str:
    """Turn a knowledge-graph response into a compact, speakable summary.

    The graph payload can be large; we hand the model a short, structured digest
    (names + types + a snippet) and let it phrase the spoken answer.
    """
    nodes = _as_list(data, "nodes")
    if not nodes:
        # Some endpoints nest the node under `entity`.
        if isinstance(data, dict) and isinstance(data.get("entity"), dict):
            nodes = [data["entity"]]
    if not nodes:
        return "I didn't find anything relevant for that."

    lines: list[str] = []
    for n in nodes[:6]:
        name = n.get("name") or n.get("key") or n.get("id", "unknown")
        ntype = n.get("type") or n.get("kind", "entity")
        props = n.get("properties") or n.get("props") or {}
        snippet = ""
        if isinstance(props, dict):
            for field in ("summary", "description", "status", "body"):
                if isinstance(props.get(field), str):
                    snippet = f" — {props[field]}"
                    break
        lines.append(f"- {name} ({ntype}){snippet}")
    return "Here's what I found:\n" + "\n".join(lines)

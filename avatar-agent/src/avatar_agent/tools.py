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

from livekit.agents import RunContext
from livekit.agents.llm import function_tool

from .backend import BackendError
from .runtime import AgentRuntime


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


# The registry the Agent is built from. Append a function here to expose a new
# tool; nothing else in the pipeline changes (CT-3).
BACKEND_TOOLS = [
    lookup_context,
    get_entity,
    recent_activity,
    record_action_item,
]


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

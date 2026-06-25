"""intent -> specialist `run()` (docs/AGENT_SYSTEM.md §9)."""

from __future__ import annotations

from shared.harness import AgentFn

from .agents import data, echo, git, meeting, research, web

REGISTRY: dict[str, AgentFn] = {
    "echo": echo.run,
    "read_git": git.run, "blame": git.run, "who_changed": git.run, "recent_changes": git.run,
    "ask": git.run,  # general knowledge-graph question (git.py is now a general KG agent)
    "build_website": web.run, "update_website": web.run,
    "analyze": data.run, "summarize_metrics": data.run, "query_data": data.run,
    "recap": meeting.run, "action_items": meeting.run, "decisions": meeting.run,
    "research": research.run,
}


def resolve(intent: str) -> AgentFn:
    fn = REGISTRY.get(intent)
    if fn is None:
        raise KeyError(f"no agent registered for intent {intent!r}")
    return fn

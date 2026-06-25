"""intent -> specialist `run()` (docs/AGENT_SYSTEM.md §9)."""

from __future__ import annotations

from shared.harness import AgentFn

from .agents import data, echo, git, web

REGISTRY: dict[str, AgentFn] = {
    "echo": echo.run,
    "read_git": git.run, "blame": git.run, "who_changed": git.run, "recent_changes": git.run,
    "build_website": web.run, "update_website": web.run,
    "analyze": data.run, "summarize_metrics": data.run, "query_data": data.run,
}


def resolve(intent: str) -> AgentFn:
    fn = REGISTRY.get(intent)
    if fn is None:
        raise KeyError(f"no agent registered for intent {intent!r}")
    return fn

"""Effort tiers — turn the planner's depth signal into concrete per-agent budgets.

The planner picks `effort` from the speaker's wording ("just check the price" → quick; "do a
thorough competitive deep-dive" → deep) and stamps it on the task (`contracts.Effort`). Each
agent maps that one signal to real knobs — web-search budget, turn limit, thinking effort — so
the *same* intent runs cheap by default and only spends more when the user actually asked for
depth (DESIGN §6 "quick vs deep"; reviewmd 2026-06-25T04-26Z Phase 1).

Model choice stays **per-agent**, not in this table: the budgets are universal, but the right
model tier differs by agent (e.g. the research agent's web tools require Sonnet/Opus and can
never run on Haiku). So an agent reads the numeric budgets + `thinking_effort` here and resolves
its own model from `settings.model_{fast,mid,smart}`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EffortPolicy:
    web_max_searches: int   # research: web_search `max_uses`
    web_max_fetches: int    # research: web_fetch `max_uses`
    max_turns: int          # bound on agentic continuation (pause_turn / tool rounds)
    thinking_effort: str    # output_config effort: "low" | "medium" | "high"


_POLICIES: dict[str, EffortPolicy] = {
    "quick":    EffortPolicy(web_max_searches=2, web_max_fetches=1, max_turns=2, thinking_effort="low"),
    "standard": EffortPolicy(web_max_searches=4, web_max_fetches=3, max_turns=4, thinking_effort="medium"),
    "deep":     EffortPolicy(web_max_searches=8, web_max_fetches=5, max_turns=6, thinking_effort="high"),
}


def policy_for(effort: str | None) -> EffortPolicy:
    """The budget table for an effort tier; unknown/None → 'standard' (the safe default)."""
    return _POLICIES.get(effort or "standard", _POLICIES["standard"])

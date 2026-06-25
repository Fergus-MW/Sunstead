"""Run ONE agent in isolation — no Kafka, no Docker. The innermost test rung.

This is the generic sibling of `ask.py` (which only covers the KG/git agent): it
resolves ANY registered intent and calls the specialist's `run()` directly, with a
real per-task context (warm Aiven MCP + Anthropic when creds are present) but a
Kafka-free `ctx` that just prints `activity()` + streamed `trace()` deltas.

    uv run python scripts/try_agent.py --intent echo --args '{"text":"hi"}'
    uv run python scripts/try_agent.py --intent build_website --args '{"brief":"landing for Sunstead","style":"dark"}'
    uv run python scripts/try_agent.py --intent who_changed --args '{"question":"who last touched auth?"}'
    make try I=build_website A='{"brief":"a one-page landing for Sunstead","style":"dark, aurora"}'

Creds: echo needs none; web/data/git need ANTHROPIC_API_KEY (+ AIVEN_TOKEN for the
KG/data path). Missing creds surface as the agent's own RuntimeError, not a crash here.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from shared import config
from shared.contracts import TaskCreatePayload
from shared.sessions import SessionStore

from agent_runner.registry import resolve


class TryCtx:
    """Duck-types shared.harness.TaskCtx for a one-shot, Kafka-free run."""

    def __init__(self, settings, mcp, anthropic):
        self.settings = settings
        self.mcp = mcp
        self.anthropic = anthropic
        self.store = SessionStore(settings.sessions_dir)
        self.meeting_id = "mtg_cli"
        self.task_id = "cli"
        self._last_phase: str | None = None

    async def activity(self, status: str, detail: str | None = None) -> None:
        print(f"  · {status}" + (f": {detail}" if detail else ""), flush=True)

    async def trace(self, phase: str, delta: str) -> None:
        # Stream the model's reasoning/output inline; tag each phase change so
        # "thinking" and "text" don't blur together (terminal-safe, no ANSI).
        if not delta:
            return
        if phase != self._last_phase:
            print(f"\n[{phase}] ", end="", flush=True)
            self._last_phase = phase
        print(delta, end="", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Run one agent in isolation (no Kafka, no Docker).")
    ap.add_argument("--intent", required=True, help=f"one of: {sorted(config.TASK_TOPIC_BY_INTENT)}")
    ap.add_argument("--args", default="{}", help="JSON object passed as task.args")
    a = ap.parse_args()

    try:
        agent = resolve(a.intent)
    except KeyError as e:
        raise SystemExit(str(e))

    s = config.load()
    mcp = anthropic = None
    if s.anthropic_api_key:
        from anthropic import AsyncAnthropic
        kwargs = {"api_key": s.anthropic_api_key}
        if s.anthropic_base_url:
            kwargs["base_url"] = s.anthropic_base_url
        anthropic = AsyncAnthropic(**kwargs)
    if s.mcp.configured:
        from shared.mcp import AivenMCP
        mcp = await AivenMCP(s.mcp).start()

    ctx = TryCtx(s, mcp, anthropic)
    task = TaskCreatePayload(
        task_id="cli", intent=a.intent, args=json.loads(a.args), idempotency_key="cli",
    )

    print(f"intent: {a.intent}   args: {task.args}")
    try:
        result = await agent(task, ctx)
        print("\n\nresult:")
        print(json.dumps(result, indent=2, default=str))
    finally:
        if mcp:
            await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

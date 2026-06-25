"""Thin vertical slice — a question → git-agent → Aiven MCP → the live KG → an answer.

Exercises the agent core + the MCP data path directly, with **no Kafka and no Docker**.
The Kafka transport (gateway → tasks → results) is the outer ring; this is the core.

    uv run python scripts/ask.py --question "who last touched the auth module?"
    make ask Q="what files import the messages client?"

Needs: AIVEN_TOKEN (MCP) + ANTHROPIC_API_KEY in .env, AND the Aiven org's
"Allow MCP connection" enabled (Console → Admin settings → Authentication).
"""

from __future__ import annotations

import argparse
import asyncio

from shared import config
from shared.contracts import TaskCreatePayload
from shared.mcp import AivenMCP


class CliCtx:
    """Minimal per-task context for a one-shot run (duck-types shared.harness.TaskCtx)."""

    def __init__(self, settings, mcp, anthropic):
        self.settings = settings
        self.mcp = mcp
        self.anthropic = anthropic

    async def activity(self, status: str, detail: str | None = None) -> None:
        print(f"  · {status}" + (f": {detail}" if detail else ""))

    async def trace(self, phase: str, delta: str) -> None:
        # The agent streams reasoning/output deltas to agent.trace via ctx.trace();
        # this one-shot slice prints the final answer below, so swallow live deltas
        # (without this no-op the agent's ctx.trace(...) call AttributeErrors here).
        pass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", default="who_changed")
    ap.add_argument("--question", required=True)
    a = ap.parse_args()

    s = config.load()
    if not s.mcp.configured:
        raise SystemExit("AIVEN_TOKEN not set (.env)")
    if not s.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (.env)")

    from anthropic import AsyncAnthropic

    from agent_runner.agents import git

    mcp = await AivenMCP(s.mcp).start()
    anth = AsyncAnthropic(api_key=s.anthropic_api_key, base_url=s.anthropic_base_url or None)
    ctx = CliCtx(s, mcp, anth)
    task = TaskCreatePayload(task_id="cli", intent=a.intent, args={"question": a.question})

    print(f"Q: {a.question}")
    try:
        res = await git.run(task, ctx)
        print(f"\nA: {res.get('answer')}")
    finally:
        await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

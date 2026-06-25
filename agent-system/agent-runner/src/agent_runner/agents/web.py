"""web-agent (stub) — persistent workspace + Vercel deploy (docs/AGENT_SYSTEM.md §5, §9).

TODO: codegen → build/smoke-check → vercel_deploy → store deployment metadata in the
session workspace. Returns a live URL.
"""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    await ctx.activity("not implemented")
    return {"status": "stub", "intent": task.intent, "note": "web-agent not implemented yet"}

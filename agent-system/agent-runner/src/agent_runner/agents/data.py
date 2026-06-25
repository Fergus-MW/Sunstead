"""data-agent (stub) — pandas/matplotlib analysis (docs/AGENT_SYSTEM.md §9).

TODO: pull rows via aiven_pg_read, run pandas/matplotlib, return answer + chart artifact.
"""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    await ctx.activity("not implemented")
    return {"status": "stub", "intent": task.intent, "note": "data-agent not implemented yet"}

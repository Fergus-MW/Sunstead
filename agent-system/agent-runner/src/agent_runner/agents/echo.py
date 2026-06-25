"""Dev/smoke agent — proves the consume→harness→emit loop with zero credentials."""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    await ctx.activity("echoing")
    return {"echo": task.args, "note": "echo agent (no-creds path)"}

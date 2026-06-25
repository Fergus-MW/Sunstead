"""Shared agent harness — every task runs the same lifecycle (docs/AGENT_SYSTEM.md §4):

    validate → claim/dedupe → fetch context → plan → act → verify → persist → emit

Specialists implement only:  async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict
The returned dict is the `result`; an optional "artifacts" key (list of {kind, value}) is
lifted into TaskResultPayload.artifacts. Raise to fail the task.

Emits (`agent.activity`, `agent.results`) are **direct Kafka produces** — no LLM round-trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from . import config
from .contracts import (
    ActivityPayload, Artifact, Envelope, TaskCreatePayload, TaskResultPayload, TracePayload,
)
from .kafka import publish
from .sessions import SessionStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentContext:
    """Long-lived, shared across tasks: warm clients + the session store."""

    def __init__(self, settings: config.Settings, producer, mcp=None, anthropic=None):
        self.settings = settings
        self.producer = producer
        self.mcp = mcp                      # shared.mcp.AivenMCP | None
        self.anthropic = anthropic          # AsyncAnthropic | None
        self.store = SessionStore(settings.sessions_dir)
        self._seen: set[str] = set()        # idempotency (in-memory; durable later)

    async def _activity(self, meeting_id: str, task_id: str, status: str, detail: str | None) -> None:
        env = Envelope[ActivityPayload](
            type="activity", meeting_id=meeting_id, ts=now_iso(),
            payload=ActivityPayload(task_id=task_id, status=status, detail=detail),
        )
        await publish(self.producer, config.ACTIVITY, env, key=task_id)

    async def _trace(self, meeting_id: str, task_id: str, seq: int, phase: str, delta: str) -> None:
        env = Envelope[TracePayload](
            type="trace", meeting_id=meeting_id, ts=now_iso(),
            payload=TracePayload(task_id=task_id, seq=seq, phase=phase, delta=delta),
        )
        await publish(self.producer, config.TRACE, env, key=task_id)  # key=task_id → per-task order

    async def _emit_result(self, meeting_id: str, reply_to: str, payload: TaskResultPayload) -> None:
        env = Envelope[TaskResultPayload](
            type="task.completed" if payload.status == "completed" else "task.failed",
            meeting_id=meeting_id, ts=now_iso(), payload=payload,
        )
        await publish(self.producer, reply_to, env, key=payload.task_id)


class TaskCtx:
    """Per-task view handed to a specialist: warm clients + a scoped activity() feed."""

    def __init__(self, base: AgentContext, meeting_id: str, task_id: str):
        self._base = base
        self.meeting_id = meeting_id
        self.task_id = task_id
        self.settings = base.settings
        self.mcp = base.mcp
        self.anthropic = base.anthropic
        self.store = base.store
        self._trace_seq = 0                     # monotonic per task → FE applies deltas in order

    async def activity(self, status: str, detail: str | None = None) -> None:
        await self._base._activity(self.meeting_id, self.task_id, status, detail)

    async def trace(self, phase: str, delta: str) -> None:
        """Emit one reasoning ('thinking') or output ('text') delta to agent.trace."""
        if not delta:
            return
        self._trace_seq += 1
        await self._base._trace(self.meeting_id, self.task_id, self._trace_seq, phase, delta)


AgentFn = Callable[[TaskCreatePayload, TaskCtx], Awaitable[dict]]


async def run_task(env: Envelope[TaskCreatePayload], agent: AgentFn, ctx: AgentContext) -> None:
    task = env.payload
    idem = task.idempotency_key or task.task_id
    if idem in ctx._seen:                                   # claim / dedupe (at-least-once)
        return
    ctx._seen.add(idem)

    tctx = TaskCtx(ctx, env.meeting_id, task.task_id)
    await tctx.activity("received", task.intent)
    try:
        result: dict = await agent(task, tctx)              # plan → act → verify (specialist)
        artifacts = (
            [Artifact(**a) for a in (result.pop("artifacts", []) or [])]
            if isinstance(result, dict) else []
        )
        await ctx._emit_result(env.meeting_id, task.reply_to, TaskResultPayload(
            task_id=task.task_id, status="completed", result=result, artifacts=artifacts,
        ))
        await tctx.activity("completed")
    except Exception as e:                                  # surface failures, never crash the loop
        await ctx._emit_result(env.meeting_id, task.reply_to, TaskResultPayload(
            task_id=task.task_id, status="failed", error=f"{type(e).__name__}: {e}",
        ))
        await tctx.activity("failed", str(e))

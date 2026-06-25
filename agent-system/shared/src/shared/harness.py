"""Shared agent harness — every task runs the same lifecycle (docs/AGENT_SYSTEM.md §4):

    validate → claim/dedupe → fetch context → plan → act → verify → persist → emit

Specialists implement only:  async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict
The returned dict is the `result`; an optional "artifacts" key (list of {kind, value}) is
lifted into TaskResultPayload.artifacts. Raise to fail the task.

Emits (`agent.activity`, `agent.results`) are **direct Kafka produces** — no LLM round-trip.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from . import config
from .contracts import (
    ActivityPayload, Artifact, Envelope, TaskCreatePayload, TaskResultPayload,
    TracePayload, VerdictPayload,
)
from .kafka import publish
from .sessions import SessionStore
from .verify import verify_grounding


log = logging.getLogger("shared.harness")

SEEN_MAX = 4096  # idempotency-key dedupe window (FIFO) — bounds memory in a long-lived runner
SEEN_LOG = "seen.log"  # durable dedupe journal under sessions_dir — survives a runner restart


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_arg(args: dict, *keys: str, default: str = "") -> str:
    """First non-empty string among task.args[keys], else `default`.

    One place for the `args.get("question") or args.get("q") or …` chains every
    agent grew. Each caller still names its own keys (so a producer template that
    sends `{path}` against an agent reading `question` stays visible), but the
    fallback mechanics — and the empty-string coercion — live here.
    """
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return default


class AgentContext:
    """Long-lived, shared across tasks: warm clients + the session store."""

    def __init__(self, settings: config.Settings, producer, mcp=None, anthropic=None):
        self.settings = settings
        self.producer = producer
        self.mcp = mcp                      # shared.mcp.AivenMCP | None
        self.anthropic = anthropic          # AsyncAnthropic | None
        self.store = SessionStore(settings.sessions_dir)
        self._seen: set[str] = set()              # idempotency-key dedupe window
        self._seen_order: deque[str] = deque()    # insertion order, to FIFO-evict `_seen` past SEEN_MAX
        # Durable dedupe: a newline-delimited journal so a runner RESTART won't re-run delivered
        # tasks (e.g. a duplicate site build / duplicate KG write) under at-least-once redelivery.
        self._seen_path = Path(settings.sessions_dir) / SEEN_LOG
        self._seen_appends = 0                    # appends since last compaction (→ rewrite at SEEN_MAX)
        self._load_seen()
        self._bg: set[asyncio.Task] = set()  # detached post-emit work (async verifier), kept referenced

    def claim(self, idem: str) -> bool:
        """Idempotent claim under at-least-once delivery: returns False if `idem` was already
        seen (caller skips), True if newly claimed. The claim is journaled to disk so a runner
        restart resumes with the same dedupe window — a redelivered task stays a no-op."""
        if idem in self._seen:
            return False
        self._seen.add(idem)
        self._seen_order.append(idem)
        self._persist_seen(idem)
        if len(self._seen_order) > SEEN_MAX:      # FIFO-evict to bound memory (cf. planner)
            self._seen.discard(self._seen_order.popleft())
        return True

    def _load_seen(self) -> None:
        """Reload the dedupe window from the journal at boot (last SEEN_MAX keys). Best-effort:
        a missing/unreadable journal just means we start cold — never blocks the runner."""
        try:
            if self._seen_path.exists():
                for k in self._seen_path.read_text(encoding="utf-8").splitlines()[-SEEN_MAX:]:
                    k = k.strip()
                    if k and k not in self._seen:
                        self._seen.add(k)
                        self._seen_order.append(k)
                if self._seen:
                    log.info("durable dedupe: reloaded %d key(s) from %s", len(self._seen), self._seen_path)
        except Exception:
            log.debug("could not load dedupe journal %s", self._seen_path, exc_info=True)

    def _persist_seen(self, idem: str) -> None:
        """Append one claimed key (best-effort — a write hiccup never blocks task processing;
        the in-memory window still dedupes within this process). Compact when the journal has
        grown a full window of appends so it can't grow unbounded."""
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            with self._seen_path.open("a", encoding="utf-8") as f:
                f.write(idem + "\n")
            self._seen_appends += 1
            if self._seen_appends >= SEEN_MAX:
                self._compact_seen()
        except Exception:
            log.debug("could not persist dedupe key", exc_info=True)

    def _compact_seen(self) -> None:
        """Rewrite the journal to just the live window (atomic via tmp+replace)."""
        try:
            tmp = self._seen_path.with_suffix(".log.tmp")
            tmp.write_text("\n".join(self._seen_order) + "\n", encoding="utf-8")
            tmp.replace(self._seen_path)
            self._seen_appends = 0
        except Exception:
            log.debug("could not compact dedupe journal", exc_info=True)

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

    async def _emit_verdict(self, meeting_id: str, reply_to: str, task_id: str, verdict) -> None:
        env = Envelope[VerdictPayload](
            type="verdict", meeting_id=meeting_id, ts=now_iso(),
            payload=VerdictPayload(task_id=task_id, verdict=verdict),
        )
        await publish(self.producer, reply_to, env, key=task_id)

    def schedule_verify(self, meeting_id: str, reply_to: str, task_id: str, verify: dict) -> None:
        """Run the grounding check OFF the critical path (docs/DESIGN.md §7) and emit the
        verdict as a follow-up — the answer is already out, so the check never delays it."""
        async def _run() -> None:
            try:
                verdict = await verify_grounding(
                    self.anthropic, model=self.settings.model_fast,
                    claim=verify.get("claim", ""), evidence=verify.get("evidence", []) or [],
                )
                await self._emit_verdict(meeting_id, reply_to, task_id, verdict)
            except Exception:
                pass  # post-hoc and fail-open — a broken verifier never disturbs the answer
        t = asyncio.create_task(_run())
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)


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
    if not ctx.claim(idem):                                 # claim / dedupe (at-least-once, durable)
        return

    tctx = TaskCtx(ctx, env.meeting_id, task.task_id)
    await tctx.activity("received", task.intent)
    try:
        result: dict = await agent(task, tctx)              # plan → act → verify (specialist)
        # A specialist that wants its answer grounding-checked returns `_verify={claim, evidence}`.
        verify = result.pop("_verify", None) if isinstance(result, dict) else None
        artifacts = (
            [Artifact(**a) for a in (result.pop("artifacts", []) or [])]
            if isinstance(result, dict) else []
        )
        # Emit the answer NOW. The grounding check (docs/DESIGN.md §7) runs off the critical
        # path and its verdict follows as a separate `verdict` event — so verification never
        # delays the answer (the §2 tempo rule: no slow verifier in front of the result).
        await ctx._emit_result(env.meeting_id, task.reply_to, TaskResultPayload(
            task_id=task.task_id, status="completed", result=result, artifacts=artifacts,
        ))
        await tctx.activity("completed")
        if isinstance(verify, dict) and ctx.anthropic is not None:
            ctx.schedule_verify(env.meeting_id, task.reply_to, task.task_id, verify)
    except asyncio.CancelledError:                          # operator/conductor stop (docs/DESIGN.md §7)
        # Emit a terminal result so the FE stops showing the task in-flight, then honour the cancel.
        await ctx._emit_result(env.meeting_id, task.reply_to, TaskResultPayload(
            task_id=task.task_id, status="failed", error="cancelled by operator",
        ))
        await tctx.activity("cancelled")
        raise
    except Exception as e:                                  # surface failures, never crash the loop
        await ctx._emit_result(env.meeting_id, task.reply_to, TaskResultPayload(
            task_id=task.task_id, status="failed", error=f"{type(e).__name__}: {e}",
        ))
        await tctx.activity("failed", str(e))

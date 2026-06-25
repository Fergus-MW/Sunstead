"""Observability: per-leg latency, a structured tool-call log, and a post-call
artifact (transcript + tool-call timeline). Implements user stories OB-1/OB-2.

Nothing here is on the hot path of a turn beyond a `monotonic()` read and an
append, so it's safe to leave on in production.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("avatar-agent.obs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCall:
    """One invocation of a custom tool, with timing and outcome (OB-1)."""

    name: str
    correlation_id: str
    args: dict[str, Any]
    started_at: str
    duration_ms: float | None = None
    status: str = "running"  # running | ok | error
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "correlation_id": self.correlation_id,
            "args": self.args,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }


class ToolCallLog:
    """Collects every tool call for a session; dumps a timeline on shutdown."""

    def __init__(self) -> None:
        self._calls: list[ToolCall] = []

    @asynccontextmanager
    async def span(self, name: str, args: dict[str, Any]):
        """Time a tool call. Use `.ok(output)` / `.fail(reason)` inside the block.

            async with tools_log.span("lookup_context", {"query": q}) as call:
                try:
                    data = await backend.query(q)
                except BackendError as e:
                    call.fail(e.reason); return fallback
                call.ok(data); return summary
        """
        call = ToolCall(
            name=name,
            correlation_id=str(uuid.uuid4()),
            args=args,
            started_at=_now_iso(),
        )
        self._calls.append(call)
        start = time.monotonic()

        class _Handle:
            #: correlation id of this call — usable as an idempotency key by writes.
            correlation_id = call.correlation_id

            def ok(self, output: Any = None) -> None:
                call.status = "ok"
                call.output = _truncate(output)

            def fail(self, reason: str) -> None:
                call.status = "error"
                call.error = reason

        handle = _Handle()
        logger.info("tool %s [%s] args=%s", name, call.correlation_id, args)
        try:
            yield handle
        finally:
            call.duration_ms = round((time.monotonic() - start) * 1000, 1)
            if call.status == "running":  # block exited without ok/fail → treat as ok
                call.status = "ok"
            logger.info(
                "tool %s [%s] %s in %sms",
                name,
                call.correlation_id,
                call.status,
                call.duration_ms,
            )

    @property
    def calls(self) -> list[ToolCall]:
        return self._calls


@dataclass
class CallArtifact:
    """The post-call audit artifact: transcript + tool-call timeline + metrics."""

    room: str
    tools_log: ToolCallLog
    started_at: str = field(default_factory=_now_iso)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def set_transcript(self, history: list[dict[str, Any]]) -> None:
        self.transcript = history

    def write(self, artifact_dir: str) -> str | None:
        """Persist the artifact as JSON; returns the path (or None on failure)."""
        os.makedirs(artifact_dir, exist_ok=True)
        path = os.path.join(artifact_dir, f"{self.room}-{int(time.time())}.json")
        payload = {
            "room": self.room,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "tool_calls": [c.to_dict() for c in self.tools_log.calls],
            "transcript": self.transcript,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=str)
        except OSError:
            logger.exception("failed to write call artifact")
            return None
        logger.info("wrote call artifact: %s", path)
        return path


def _truncate(value: Any, limit: int = 4000) -> Any:
    """Keep artifacts bounded — backend payloads can be large."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > limit:
        return text[:limit] + f"… (+{len(text) - limit} chars)"
    return value

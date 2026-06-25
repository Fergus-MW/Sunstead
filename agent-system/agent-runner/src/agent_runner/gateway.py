"""FE / delegation gateway (docs/DESIGN.md §3) — the single authenticated task front door + the FE feed.

Two jobs, one small FastAPI app:
  • POST /tasks   — produce an `agent.tasks.*` message. Used by the avatar's `delegate()` tool (seam option a)
                    AND the FE "ask box". HTTP at the edge → Kafka in the core.
  • WS /stream    — tail `agent.results` + `agent.activity` and push them to the browser (the deliverable + the
                    visible status feed).

**One shared broadcast consumer, not one-per-connection.** A single long-lived consumer (started at boot) tails
results+activity into a bounded ring buffer and fans out to every connected socket. This fixes three problems the
per-connection design had: (1) a fast task could complete in the gap before a brand-new consumer group finished
joining → the result was produced past `latest` and never seen ("I clicked ask and nothing appeared"); the ring
buffer is replayed on connect so a late-joining browser still sees recent results. (2) consumer-group sprawl from
the FE's auto-reconnect loop. (3) one malformed frame tearing down the socket — each message is now isolated.

Run it (alongside the agent-runner, same image):  uv run python -m agent_runner.gateway
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from shared import config
from shared.contracts import (
    ControlPayload, Envelope, Speaker, TaskCreatePayload, TaskIntent, TranscriptPayload,
)
from shared.harness import now_iso
from shared.kafka import consume, make_producer, publish

log = logging.getLogger("gateway")

RING_SIZE = 200          # recent envelopes replayed to a newly-connected socket
TRACE_RING_SIZE = 400    # trace deltas kept separate so a build burst can't evict results/activity
CLIENT_QUEUE_MAX = 500   # per-socket backlog before we drop (a stalled browser can't back up Kafka)


class TaskRequest(BaseModel):
    intent: TaskIntent
    args: dict[str, Any] = Field(default_factory=dict)
    meeting_id: str = "mtg_dev"
    requested_by: str = "gateway"


class TranscriptRequest(BaseModel):
    """One spoken utterance, posted by the avatar (HTTP edge) → published to
    `meeting.transcript` so the planner can route it and the FE can show it."""
    text: str
    meeting_id: str = "mtg_dev"
    speaker: str | None = None
    is_final: bool = True


class ControlRequest(BaseModel):
    """An operator/conductor command (FE "stop" button) → `agent.control` (docs/DESIGN.md §7)."""
    task_id: str
    action: str = "cancel"
    meeting_id: str = "mtg_dev"
    reason: str | None = None


# A fanned-out stream item: (meeting_id, envelope_id, raw_json_text). The id lets a socket dedupe the small
# overlap between its replay snapshot and the live feed; the meeting_id drives the per-socket filter.
Item = tuple[str, str, str]


class Hub:
    """Fan-out of the results+activity stream to all connected WS clients, with a replay buffer.

    One background task owns the only Kafka consumer; each socket gets an asyncio.Queue fed from it.
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.ring: deque[Item] = deque(maxlen=RING_SIZE)
        self.trace_ring: deque[Item] = deque(maxlen=TRACE_RING_SIZE)
        self.clients: set[asyncio.Queue[Item]] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gateway-broadcast")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def register(self) -> asyncio.Queue[Item]:
        q: asyncio.Queue[Item] = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.clients.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def _fanout(self, item: Item, *, is_trace: bool = False) -> None:
        # trace is high-volume; its own ring so a build burst can't evict result/activity replay
        (self.trace_ring if is_trace else self.ring).append(item)
        for q in self.clients:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass  # a stalled browser drops messages rather than stalling the consumer

    async def stream_for(self, meeting_id: str | None):
        """Yield raw envelope JSON for one subscriber: replay the recent ring, then stream live.

        Registering the queue *before* snapshotting the ring closes the gap (no message is missed between
        snapshot and live); the envelope-id dedupe drops the small replay/live overlap exactly once.
        """
        q = self.register()
        try:
            replayed: set[str] = set()
            # results/activity first so task rows fold even if trace replay is large; the FE
            # folds trace by seq and re-sorts tasks by ts, so this ordering is presentation-only.
            for mid, eid, text in [*self.ring, *self.trace_ring]:
                if meeting_id is None or mid == meeting_id:
                    replayed.add(eid)
                    yield text
            while True:
                mid, eid, text = await q.get()
                if eid in replayed:                  # snapshot/live overlap — drop the dup once
                    replayed.discard(eid)
                    continue
                if meeting_id is None or mid == meeting_id:
                    yield text
        finally:
            self.unregister(q)

    async def _run(self) -> None:
        # Unique group per process so every gateway instance sees the full live stream (broadcast, not work-sharing).
        group = f"gateway-broadcast-{uuid.uuid4().hex[:8]}"
        while True:  # stay up across a flaky/late Kafka — the FE degrades gracefully meanwhile
            try:
                # Also tail the task topics so `task.create` (the dispatch moment) reaches the FE:
                # the board shows a task the instant it's dispatched — not only once a runner emits
                # its first activity — so one sent to a down/slow runner still appears (then stalls).
                async for msg in consume(config.RESULTS, config.ACTIVITY, config.TRACE,
                                         config.CONTROL, config.TRANSCRIPT,
                                         *config.TASK_TOPICS,
                                         group_id=group, settings=self.settings,
                                         auto_offset_reset="latest"):
                    try:
                        env = Envelope.model_validate_json(msg.value)   # validate, but never let one bad frame...
                        self._fanout((env.meeting_id, env.id, msg.value.decode()),
                                     is_trace=env.type == "trace")
                    except Exception as e:  # ...kill the feed for everyone else
                        log.warning("skipping unparseable %s message: %s", msg.topic, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("broadcast consumer error (%s) — retrying", e)
                await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = config.load()
    app.state.producer = await make_producer(app.state.settings)  # one warm producer, reused
    app.state.hub = Hub(app.state.settings)
    app.state.hub.start()
    log.info("gateway up (bootstrap=%s)", app.state.settings.kafka.bootstrap)
    try:
        yield
    finally:
        await app.state.hub.stop()
        await app.state.producer.stop()


app = FastAPI(title="Sunstead gateway", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/tasks")
async def create_task(req: TaskRequest) -> dict:
    """Enqueue a delegated task. Fire-and-forget — the result arrives later on WS /stream."""
    topic = config.TASK_TOPIC_BY_INTENT[req.intent]  # TaskIntent is validated by pydantic
    task_id = "tsk_" + uuid.uuid4().hex[:8]
    env = Envelope[TaskCreatePayload](
        type="task.create", meeting_id=req.meeting_id, ts=now_iso(),
        payload=TaskCreatePayload(
            task_id=task_id, intent=req.intent, args=req.args,
            idempotency_key=task_id, requested_by=req.requested_by,
        ),
    )
    await publish(app.state.producer, topic, env, key=task_id)
    return {"task_id": task_id, "status": "accepted", "topic": topic}


@app.post("/transcript")
async def post_transcript(req: TranscriptRequest) -> dict:
    """Publish one spoken utterance to `meeting.transcript`. The avatar posts here
    (staying a pure HTTP client) so the planner routes it and the FE feed shows it."""
    env = Envelope[TranscriptPayload](
        type="transcript.final" if req.is_final else "transcript.partial",
        meeting_id=req.meeting_id, ts=now_iso(),
        payload=TranscriptPayload(
            speaker=Speaker(name=req.speaker), text=req.text, is_final=req.is_final
        ),
    )
    await publish(app.state.producer, config.TRANSCRIPT, env, key=req.meeting_id)
    return {"status": "accepted", "topic": config.TRANSCRIPT}


@app.post("/control")
async def control(req: ControlRequest) -> dict:
    """Send a command to a running task (the FE "stop" button) → `agent.control`."""
    env = Envelope[ControlPayload](
        type="control", meeting_id=req.meeting_id, ts=now_iso(),
        payload=ControlPayload(task_id=req.task_id, action="cancel", reason=req.reason),
    )
    await publish(app.state.producer, config.CONTROL, env, key=req.task_id)
    return {"status": "accepted", "task_id": req.task_id, "action": "cancel"}


@app.websocket("/stream")
async def stream(ws: WebSocket, meeting_id: str | None = None) -> None:
    """Tail results + activity to the browser; optionally filter to one meeting.

    On connect we replay the recent ring buffer (so a browser that opens *after* asking still sees the result),
    then stream live from a per-socket queue fed by the shared broadcast consumer.
    """
    await ws.accept()
    hub: Hub = ws.app.state.hub
    try:
        async for text in hub.stream_for(meeting_id):
            await ws.send_text(text)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: keep a socket failure off the shared consumer
        log.warning("ws error: %s", e)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8800)


if __name__ == "__main__":
    main()

"""FE / delegation gateway (docs/DESIGN.md §3) — the single authenticated task front door + the FE feed.

Two jobs, one small FastAPI app:
  • POST /tasks   — produce an `agent.tasks.*` message. Used by the avatar's `delegate()` tool (seam option a)
                    AND the FE "ask box". HTTP at the edge → Kafka in the core.
  • WS /stream    — tail `agent.results` + `agent.activity` and push them to the browser (the deliverable + the
                    visible status feed). Each connection gets its own consumer group.

Run it (alongside the agent-runner, same image):  uv run python -m agent_runner.gateway
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from shared import config
from shared.contracts import Envelope, TaskCreatePayload, TaskIntent
from shared.harness import now_iso
from shared.kafka import consume, make_producer, publish

log = logging.getLogger("gateway")


class TaskRequest(BaseModel):
    intent: TaskIntent
    args: dict[str, Any] = Field(default_factory=dict)
    meeting_id: str = "mtg_dev"
    requested_by: str = "gateway"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = config.load()
    app.state.producer = await make_producer(app.state.settings)  # one warm producer, reused
    log.info("gateway up (bootstrap=%s)", app.state.settings.kafka.bootstrap)
    try:
        yield
    finally:
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


@app.websocket("/stream")
async def stream(ws: WebSocket, meeting_id: str | None = None) -> None:
    """Tail results + activity to the browser; optionally filter to one meeting."""
    await ws.accept()
    group = f"gateway-ws-{uuid.uuid4().hex[:8]}"
    try:
        async for msg in consume(config.RESULTS, config.ACTIVITY,
                                 group_id=group, settings=ws.app.state.settings,
                                 auto_offset_reset="latest"):
            env = Envelope.model_validate_json(msg.value)
            if meeting_id and env.meeting_id != meeting_id:
                continue
            await ws.send_text(msg.value.decode())
    except WebSocketDisconnect:
        log.info("ws %s disconnected", group)
    except Exception as e:  # noqa: keep the socket failure off the consumer loop
        log.warning("ws %s error: %s", group, e)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8800)


if __name__ == "__main__":
    main()

"""Publish a task.create onto the right agent.tasks.* topic (stands in for the listener).

    uv run python scripts/publish_task.py --intent echo --args '{"text":"hi"}'
    uv run python scripts/publish_task.py --intent who_changed --args '{"question":"who last touched the auth module?"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from shared import config
from shared.contracts import Envelope, TaskCreatePayload
from shared.harness import now_iso
from shared.kafka import make_producer, publish


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", default="echo")
    ap.add_argument("--args", default="{}", help="JSON object")
    ap.add_argument("--meeting", default="mtg_dev")
    a = ap.parse_args()

    topic = config.TASK_TOPIC_BY_INTENT.get(a.intent)
    if not topic:
        raise SystemExit(f"unknown intent {a.intent!r}; known: {sorted(config.TASK_TOPIC_BY_INTENT)}")

    task_id = "tsk_" + uuid.uuid4().hex[:8]
    payload = TaskCreatePayload(
        task_id=task_id, intent=a.intent, args=json.loads(a.args), idempotency_key=task_id,
    )
    env = Envelope[TaskCreatePayload](type="task.create", meeting_id=a.meeting, ts=now_iso(), payload=payload)

    s = config.load()
    producer = await make_producer(s)
    await publish(producer, topic, env, key=task_id)
    await producer.stop()
    print(f"published {a.intent} task {task_id} -> {topic}")


if __name__ == "__main__":
    asyncio.run(main())

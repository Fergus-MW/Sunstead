"""Local Kafka smoke — no credentials. Produces one message and reads it back.

    uv run python scripts/kafka_smoke.py     (needs `make up` + `make topics` first)
"""

from __future__ import annotations

import asyncio

from shared import config
from shared.contracts import Envelope, TaskResultPayload
from shared.harness import now_iso
from shared.kafka import consume, make_producer, publish


async def main() -> None:
    s = config.load()
    producer = await make_producer(s)
    env = Envelope[TaskResultPayload](
        type="task.completed", meeting_id="smoke", ts=now_iso(),
        payload=TaskResultPayload(task_id="smoke-1", status="completed", result={"hello": "kafka"}),
    )
    await publish(producer, config.RESULTS, env, key="smoke-1")
    await producer.stop()
    print(f"produced -> {config.RESULTS}")

    async def read_one() -> None:
        async for msg in consume(config.RESULTS, group_id="smoke-reader",
                                 settings=s, auto_offset_reset="earliest"):
            print(f"consumed <- {msg.value.decode()}")
            return

    await asyncio.wait_for(read_one(), timeout=15)
    print("OK: Kafka round-trip works")


if __name__ == "__main__":
    asyncio.run(main())

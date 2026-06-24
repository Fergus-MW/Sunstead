"""Create our Kafka topics idempotently.

Aiven has auto-create OFF, so topics must be declared. Run once after provisioning:

    uv run python infra/kafka_admin.py

Topic list + partition/RF config lives in shared.config.ALL_TOPICS.
"""

from __future__ import annotations

import asyncio

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from shared import config
from shared.kafka import _common  # reuse the same TLS/SASL wiring


async def main() -> None:
    k = config.load().kafka
    admin = AIOKafkaAdminClient(**_common(k))
    await admin.start()
    try:
        new = [
            NewTopic(name=name, num_partitions=parts, replication_factor=rf)
            for (name, parts, rf) in config.ALL_TOPICS
        ]
        for topic in new:
            try:
                await admin.create_topics([topic])
                print(f"created  {topic.name} (p={topic.num_partitions}, rf={topic.replication_factor})")
            except TopicAlreadyExistsError:
                print(f"exists   {topic.name}")
    finally:
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Create our Kafka topics idempotently (Aiven auto-create is OFF; redpanda is fine too).

    uv run python infra/kafka_admin.py

Partition/RF come from config (local defaults 1/1; set KAFKA_PARTITIONS=3 KAFKA_RF=3 for Aiven).
"""

from __future__ import annotations

import asyncio

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from shared import config
from shared.kafka import _common  # reuse the same PLAINTEXT/SASL/SSL wiring


async def main() -> None:
    s = config.load()
    k = s.kafka
    admin = AIOKafkaAdminClient(**_common(k))
    await admin.start()
    try:
        for name in config.ALL_TOPICS:
            topic = NewTopic(name=name, num_partitions=k.partitions, replication_factor=k.replication_factor)
            try:
                await admin.create_topics([topic])
                print(f"created  {name} (p={k.partitions}, rf={k.replication_factor})")
            except TopicAlreadyExistsError:
                print(f"exists   {name}")
    finally:
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())

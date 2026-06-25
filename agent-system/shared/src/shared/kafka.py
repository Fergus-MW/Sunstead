"""Thin async Kafka helpers — local PLAINTEXT (redpanda) or Aiven (SASL_SSL / mTLS).

The agent-runner consumes with a long-lived consumer and publishes results/activity
with a direct producer (no LLM round-trip on the publish — see docs/AGENT_SYSTEM.md §3.5).

    producer = await make_producer()
    await publish(producer, config.RESULTS, env, key=task_id)

    async for msg in consume(*config.TASK_TOPICS, group_id="agent-runner"):
        env = Envelope[TaskCreatePayload].model_validate_json(msg.value)
"""

from __future__ import annotations

from typing import AsyncIterator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from . import config
from .contracts import Envelope


def _common(k: config.KafkaSettings) -> dict:
    if k.security == "PLAINTEXT":  # local redpanda
        return {"bootstrap_servers": k.bootstrap, "security_protocol": "PLAINTEXT"}

    # Aiven: TLS always; SASL or mTLS
    from aiokafka.helpers import create_ssl_context
    if k.security == "SSL":  # mTLS (cert + key)
        ssl_context = create_ssl_context(cafile=k.ca_path, certfile=k.cert_path, keyfile=k.key_path)
        return {"bootstrap_servers": k.bootstrap, "security_protocol": "SSL", "ssl_context": ssl_context}

    # SASL_SSL: CA for TLS, creds via SASL
    ssl_context = create_ssl_context(cafile=k.ca_path)
    return {
        "bootstrap_servers": k.bootstrap,
        "security_protocol": "SASL_SSL",
        "ssl_context": ssl_context,
        "sasl_mechanism": k.sasl_mechanism,
        "sasl_plain_username": k.username,
        "sasl_plain_password": k.password,
    }


async def make_producer(settings: config.Settings | None = None) -> AIOKafkaProducer:
    k = (settings or config.load()).kafka
    producer = AIOKafkaProducer(linger_ms=0, acks=1, **_common(k))  # linger 0 = low latency
    await producer.start()
    return producer


async def publish(producer: AIOKafkaProducer, topic: str, env: Envelope, key: str | None = None) -> None:
    await producer.send_and_wait(topic, env.to_json(), key=(key.encode() if key else None))


async def consume(
    *topics: str,
    group_id: str,
    settings: config.Settings | None = None,
    auto_offset_reset: str = "latest",
) -> AsyncIterator:
    """Async-iterate raw messages; caller decodes with Envelope[...].model_validate_json."""
    k = (settings or config.load()).kafka
    consumer = AIOKafkaConsumer(
        *topics, group_id=group_id, auto_offset_reset=auto_offset_reset,
        enable_auto_commit=True, **_common(k),
    )
    await consumer.start()
    try:
        async for msg in consumer:
            yield msg
    finally:
        await consumer.stop()

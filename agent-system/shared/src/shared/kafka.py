"""Thin async Kafka helpers wired for Aiven (TLS + SASL or mTLS).

Usage:
    producer = await make_producer()
    await producer.send_and_wait(config.TRANSCRIPT, env.to_json(), key=meeting_id.encode())

    async for msg in consume(config.TRANSCRIPT, group_id="listener"):
        env = Envelope.model_validate_json(msg.value)
"""

from __future__ import annotations

import ssl
from typing import AsyncIterator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.helpers import create_ssl_context

from . import config


def _ssl_context(k: config.KafkaSettings) -> ssl.SSLContext:
    if k.security == "SSL":  # mTLS
        return create_ssl_context(cafile=k.ca_path, certfile=k.cert_path, keyfile=k.key_path)
    return create_ssl_context(cafile=k.ca_path)  # SASL_SSL: CA only, creds via SASL


def _common(k: config.KafkaSettings) -> dict:
    opts: dict = {"bootstrap_servers": k.bootstrap, "ssl_context": _ssl_context(k)}
    if k.security == "SASL_SSL":
        opts |= {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": k.sasl_mechanism,
            "sasl_plain_username": k.username,
            "sasl_plain_password": k.password,
        }
    else:
        opts |= {"security_protocol": "SSL"}
    return opts


async def make_producer(settings: config.Settings | None = None) -> AIOKafkaProducer:
    k = (settings or config.load()).kafka
    producer = AIOKafkaProducer(**_common(k))
    await producer.start()
    return producer


async def consume(
    *topics: str,
    group_id: str,
    settings: config.Settings | None = None,
    auto_offset_reset: str = "latest",
) -> AsyncIterator:
    """Async-iterate messages; caller decodes with Envelope.model_validate_json."""
    k = (settings or config.load()).kafka
    consumer = AIOKafkaConsumer(
        *topics, group_id=group_id, auto_offset_reset=auto_offset_reset, **_common(k)
    )
    await consumer.start()
    try:
        async for msg in consumer:
            yield msg
    finally:
        await consumer.stop()

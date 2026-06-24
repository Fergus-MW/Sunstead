from __future__ import annotations

import logging

from .config import get_settings

logger = logging.getLogger(__name__)


async def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """Return one embedding per input. Returns None entries if no key configured."""
    settings = get_settings()
    if not settings.openai_api_key or not texts:
        return [None] * len(texts)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    cleaned = [t.replace("\n", " ").strip()[:8000] or " " for t in texts]
    try:
        resp = await client.embeddings.create(model=settings.embedding_model, input=cleaned)
    except Exception as e:  # noqa: BLE001
        logger.warning("embedding call failed: %s", e)
        return [None] * len(texts)
    return [d.embedding for d in resp.data]


async def embed_one(text: str) -> list[float] | None:
    res = await embed_texts([text])
    return res[0] if res else None

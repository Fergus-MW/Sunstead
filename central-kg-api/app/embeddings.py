"""Embedding hook.

Anthropic does not ship an embeddings API, so today both functions return None
and the rest of the app degrades to trigram + keyword search. A Voyage AI
(or sentence-transformers) adapter would slot in here without touching callers.
"""

from __future__ import annotations


async def embed_texts(texts: list[str]) -> list[list[float] | None]:
    return [None] * len(texts)


async def embed_one(text: str) -> list[float] | None:
    return None

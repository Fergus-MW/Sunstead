"""OpenSearch BM25 over the mirrored `kg-nodes` index — the full-text recall half of hybrid retrieval.

The live `/query` path is trigram-only (fuzzy *string* match on node names). BM25 over the mirrored text gives
real relevance ranking; combined with the graph traversal (`graph.subgraph_bfs`) it's the "OpenSearch → ids →
recursive-CTE neighborhood" flow from PLAN §3.5 — the same path the retrieval benchmark measured at ~125 ms.

The OpenSearch `_id` equals `nodes.id` (UUID) by construction (see `seed/mirror_opensearch.py`), so a hit list is
directly a list of node ids — no join table. This module returns only ids; the router resolves them to nodes +
neighborhood via the existing graph functions.

Degrades safely: if `OPENSEARCH_URL` is unset or OpenSearch is unreachable, `bm25_node_ids` returns `None` so the
caller can fall back to trigram search — `/search` is then never *worse* than `/query`.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from .config import get_settings

log = logging.getLogger("search")

# Field boosts mirror the benchmark (bench/retrieval.py): name dominates, then the source file, then label/body.
# `text` is the utterance/commit body when the demo-meeting layer is mirrored; absent fields are simply ignored.
_BM25_FIELDS = ["name^3", "source_file^2", "label", "text"]


async def bm25_node_ids(q: str, size: int = 12) -> list[UUID] | None:
    """Return node ids for the top BM25 hits, in rank order. `None` if OpenSearch is unavailable."""
    s = get_settings()
    base = (s.opensearch_url or "").rstrip("/")
    if not base:
        return None
    body = {
        "size": size,
        "query": {"multi_match": {"query": q, "fields": _BM25_FIELDS}},
        "_source": False,  # we only need the _id (== nodes.id)
    }
    try:
        # verify=False: Aiven OpenSearch presents a cert the container may not have in its trust store; the URL
        # already carries credentials over TLS. Acceptable for the hackathon (matches seed/mirror_opensearch.py).
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            r = await client.post(f"{base}/{s.opensearch_index}/_search", json=body)
            r.raise_for_status()
            hits = r.json().get("hits", {}).get("hits", [])
    except Exception as e:  # unreachable / index missing / bad creds → let the caller fall back to trigram
        log.warning("BM25 search unavailable (%s); falling back to trigram", e)
        return None

    ids: list[UUID] = []
    for h in hits:
        try:
            ids.append(UUID(h["_id"]))
        except (KeyError, ValueError):
            continue
    return ids

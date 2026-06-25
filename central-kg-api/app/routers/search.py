"""GET /search — BM25 full-text recall blended with graph neighborhood.

A strict upgrade of `/query`: same `QueryResult` shape (drop-in for the avatar's `lookup_context` and the FE), but
the top hits come from **OpenSearch BM25** (real relevance) instead of trigram name-matching. The neighborhood is
the same recursive-CTE traversal `/query` uses. If OpenSearch is unavailable it transparently falls back to the
trigram `hybrid_search`, so this endpoint is never *worse* than `/query`.

    OpenSearch BM25(q) -> node ids (rank order) -> subgraph_bfs(ids, hops) -> ranked nodes + neighborhood
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..graph import hybrid_search, subgraph_bfs
from ..models import QueryResult, Subgraph
from ..search import bm25_node_ids

router = APIRouter()


@router.get("/search", response_model=QueryResult)
async def search(
    q: str = Query(..., description="Natural language query (BM25 over the knowledge graph)"),
    limit: int | None = Query(None, ge=1, le=50),
    hops: int | None = Query(None, ge=0, le=4),
    session: AsyncSession = Depends(get_session),
) -> QueryResult:
    settings = get_settings()
    limit = limit or settings.default_query_limit
    hops = hops if hops is not None else settings.default_subgraph_hops

    ids = await bm25_node_ids(q, size=limit)
    if ids:
        # Resolve the BM25 hits to Node objects (hops=0 returns just the seeds), preserving rank order.
        seeds, _ = await subgraph_bfs(session, ids, hops=0)
        by_id = {n.id: n for n in seeds}
        nodes = [by_id[i] for i in ids if i in by_id]
    else:
        # OpenSearch unavailable or no hits → behave exactly like /query (trigram), never worse.
        nodes = await hybrid_search(session, q, limit=limit)

    sub_nodes, sub_edges = await subgraph_bfs(session, [n.id for n in nodes], hops=hops)
    return QueryResult(
        query=q,
        nodes=nodes,
        subgraph=Subgraph(nodes=sub_nodes, edges=sub_edges),
    )

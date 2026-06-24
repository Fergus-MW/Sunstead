from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..graph import hybrid_search, subgraph_bfs
from ..models import QueryResult, Subgraph

router = APIRouter()


@router.get("/query", response_model=QueryResult)
async def query(
    q: str = Query(..., description="Natural language query"),
    limit: int | None = Query(None, ge=1, le=50),
    hops: int | None = Query(None, ge=0, le=4),
    session: AsyncSession = Depends(get_session),
) -> QueryResult:
    settings = get_settings()
    limit = limit or settings.default_query_limit
    hops = hops if hops is not None else settings.default_subgraph_hops

    nodes = await hybrid_search(session, q, limit=limit)
    sub_nodes, sub_edges = await subgraph_bfs(session, [n.id for n in nodes], hops=hops)
    return QueryResult(
        query=q,
        nodes=nodes,
        subgraph=Subgraph(nodes=sub_nodes, edges=sub_edges),
    )

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..graph import hybrid_search, subgraph_bfs
from ..models import Subgraph

router = APIRouter()


@router.get("/subgraph", response_model=Subgraph)
async def subgraph(
    center: list[UUID] | None = Query(None, description="One or more center node ids"),
    q: str | None = Query(None, description="Or: search query to seed centers"),
    hops: int = Query(2, ge=0, le=4),
    node_limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Subgraph:
    if not center and q:
        seed_nodes = await hybrid_search(session, q, limit=5)
        center = [n.id for n in seed_nodes]
    if not center:
        return Subgraph(nodes=[], edges=[])
    nodes, edges = await subgraph_bfs(session, center, hops=hops, node_limit=node_limit)
    return Subgraph(nodes=nodes, edges=edges)

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..graph import get_node, neighbors, subgraph_bfs
from ..models import Node, Subgraph

router = APIRouter()


@router.get("/entity/{node_id}")
async def entity(
    node_id: UUID,
    hops: int = Query(1, ge=0, le=4),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    node = await get_node(session, node_id)
    if not node:
        raise HTTPException(404, "node not found")
    if hops <= 1:
        ns, es = await neighbors(session, node_id, limit=limit)
    else:
        ns, es = await subgraph_bfs(session, [node_id], hops=hops, node_limit=limit)
    return {"node": node, "subgraph": Subgraph(nodes=ns, edges=es)}


@router.get("/node", response_model=list[Node])
async def find_node(
    type: str | None = Query(None),
    name: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[Node]:
    from sqlalchemy import text

    clauses = []
    params: dict = {"limit": limit}
    if type:
        clauses.append("type = :type")
        params["type"] = type
    if name:
        clauses.append("lower(name) LIKE lower(:name)")
        params["name"] = f"%{name}%"
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = text(
        f"""
        SELECT id, type, name, properties, source_id, created_at, updated_at
        FROM nodes {where}
        ORDER BY updated_at DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).all()
    from ..graph import _row_to_node

    return [_row_to_node(r) for r in rows]

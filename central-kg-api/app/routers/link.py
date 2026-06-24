from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..graph import get_node_by_ref, upsert_edge, upsert_node
from ..models import Edge, EdgeUpsert, Node, NodeUpsert

router = APIRouter()


@router.post("/node", response_model=Node)
async def upsert_node_route(payload: NodeUpsert, session: AsyncSession = Depends(get_session)) -> Node:
    embedding = None
    if payload.embed:
        from ..embeddings import embed_one

        embedding = await embed_one(f"{payload.type}: {payload.name}")
    node = await upsert_node(
        session,
        type_=payload.type,
        name=payload.name,
        properties=payload.properties,
        source_id=payload.source_id,
        embedding=embedding,
    )
    await session.commit()
    return node


@router.post("/link", response_model=Edge)
async def link(payload: EdgeUpsert, session: AsyncSession = Depends(get_session)) -> Edge:
    s_id = payload.source_node_id
    t_id = payload.target_node_id
    if s_id is None and payload.source_ref:
        existing = await get_node_by_ref(session, *payload.source_ref)
        s_id = existing.id if existing else None
    if t_id is None and payload.target_ref:
        existing = await get_node_by_ref(session, *payload.target_ref)
        t_id = existing.id if existing else None
    if not s_id or not t_id:
        raise HTTPException(400, "Both endpoints must resolve. Provide ids or refs.")
    edge = await upsert_edge(
        session,
        source_node_id=s_id,
        target_node_id=t_id,
        type_=payload.type,
        properties=payload.properties,
        weight=payload.weight,
        source_id=payload.source_id,
    )
    await session.commit()
    return edge

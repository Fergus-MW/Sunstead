from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..extract import extract_graph
from ..graph import bulk_embed_nodes, get_node_by_ref, upsert_edge, upsert_node
from ..models import ExtractRequest, ExtractResponse

router = APIRouter()


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest, session: AsyncSession = Depends(get_session)) -> ExtractResponse:
    body = req.text
    source_id: UUID | None = req.source_id

    if not body and source_id:
        row = (
            await session.execute(
                text("SELECT content FROM sources WHERE id = :id"), {"id": source_id}
            )
        ).first()
        if not row:
            raise HTTPException(404, "source not found")
        body = row._mapping["content"] or ""

    if not body:
        raise HTTPException(400, "Either text or source_id (with content) must be provided.")

    graph = await extract_graph(body, hints=req.hints)

    out_nodes = []
    ref_to_id: dict[tuple[str, str], UUID] = {}
    for n in graph["nodes"]:
        node = await upsert_node(
            session,
            type_=n["type"],
            name=n["name"],
            properties=n.get("properties") or {},
            source_id=source_id,
        )
        ref_to_id[(n["type"], n["name"].lower())] = node.id
        out_nodes.append(node)

    out_edges = []
    for e in graph["edges"]:
        s = e["source"]
        t = e["target"]
        s_id = ref_to_id.get((s["type"], s["name"].lower()))
        t_id = ref_to_id.get((t["type"], t["name"].lower()))
        if s_id is None:
            existing = await get_node_by_ref(session, s["type"], s["name"])
            s_id = existing.id if existing else None
        if t_id is None:
            existing = await get_node_by_ref(session, t["type"], t["name"])
            t_id = existing.id if existing else None
        if not s_id or not t_id:
            continue
        edge = await upsert_edge(
            session,
            source_node_id=s_id,
            target_node_id=t_id,
            type_=e["type"],
            properties=e.get("properties") or {},
            source_id=source_id,
        )
        out_edges.append(edge)

    await bulk_embed_nodes(session, out_nodes)
    await session.commit()
    return ExtractResponse(nodes=out_nodes, edges=out_edges)

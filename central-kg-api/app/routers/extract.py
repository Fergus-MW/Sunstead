from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..extract import extract_graph
from ..graph import persist_extracted_graph
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
    out_nodes, out_edges = await persist_extracted_graph(session, graph, source_id)
    await session.commit()
    return ExtractResponse(nodes=out_nodes, edges=out_edges)

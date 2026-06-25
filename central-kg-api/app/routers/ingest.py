from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..extract import extract_graph
from ..graph import persist_extracted_graph
from ..models import IngestResponse, Source, SourceCreate

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(payload: SourceCreate, session: AsyncSession = Depends(get_session)) -> IngestResponse:
    # 1. Insert the source row
    src_row = (
        await session.execute(
            text(
                """
                INSERT INTO sources (kind, uri, title, content, metadata)
                VALUES (:kind, :uri, :title, :content, CAST(:metadata AS jsonb))
                RETURNING id, kind, uri, title, content, metadata, created_at
                """
            ),
            {
                "kind": payload.kind,
                "uri": payload.uri,
                "title": payload.title,
                "content": payload.content,
                "metadata": json.dumps(payload.metadata),
            },
        )
    ).one()
    source = Source.model_validate(src_row._mapping)

    extracted_nodes = []
    extracted_edges = []

    if payload.extract and payload.content:
        graph = await extract_graph(payload.content)
        extracted_nodes, extracted_edges = await persist_extracted_graph(session, graph, source.id)

    await session.commit()
    return IngestResponse(source=source, extracted_nodes=extracted_nodes, extracted_edges=extracted_edges)


@router.get("/source/{source_id}", response_model=Source)
async def get_source(source_id: UUID, session: AsyncSession = Depends(get_session)) -> Source:
    row = (
        await session.execute(
            text(
                "SELECT id, kind, uri, title, content, metadata, created_at FROM sources WHERE id = :id"
            ),
            {"id": source_id},
        )
    ).first()
    if not row:
        raise HTTPException(404, "source not found")
    return Source.model_validate(row._mapping)

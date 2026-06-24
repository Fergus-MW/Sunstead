from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..extract import extract_graph
from ..graph import bulk_embed_nodes, get_node_by_ref, upsert_edge, upsert_node
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
        # Upsert nodes
        ref_to_id: dict[tuple[str, str], UUID] = {}
        for n in graph["nodes"]:
            node = await upsert_node(
                session,
                type_=n["type"],
                name=n["name"],
                properties=n.get("properties") or {},
                source_id=source.id,
            )
            ref_to_id[(n["type"], n["name"].lower())] = node.id
            extracted_nodes.append(node)

        # Upsert edges
        for e in graph["edges"]:
            s = e["source"]
            t = e["target"]
            s_id = ref_to_id.get((s["type"], s["name"].lower()))
            t_id = ref_to_id.get((t["type"], t["name"].lower()))
            if s_id is None:
                node = await get_node_by_ref(session, s["type"], s["name"])
                s_id = node.id if node else None
            if t_id is None:
                node = await get_node_by_ref(session, t["type"], t["name"])
                t_id = node.id if node else None
            if not s_id or not t_id:
                continue
            edge = await upsert_edge(
                session,
                source_node_id=s_id,
                target_node_id=t_id,
                type_=e["type"],
                properties=e.get("properties") or {},
                source_id=source.id,
            )
            extracted_edges.append(edge)

        # Async batch embed for new nodes
        await bulk_embed_nodes(session, extracted_nodes)

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

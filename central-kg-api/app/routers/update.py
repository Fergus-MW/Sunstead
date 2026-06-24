from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Event, EventCreate

router = APIRouter()


@router.post("/update", response_model=Event)
async def update(payload: EventCreate, session: AsyncSession = Depends(get_session)) -> Event:
    sql = text(
        """
        INSERT INTO events (node_id, source_id, kind, occurred_at, payload)
        VALUES (:node_id, :source_id, :kind, COALESCE(:occurred_at, now()), CAST(:payload AS jsonb))
        RETURNING id, node_id, source_id, kind, occurred_at, payload
        """
    )
    row = (
        await session.execute(
            sql,
            {
                "node_id": payload.node_id,
                "source_id": payload.source_id,
                "kind": payload.kind,
                "occurred_at": payload.occurred_at,
                "payload": json.dumps(payload.payload),
            },
        )
    ).one()

    if payload.node_id:
        await session.execute(
            text("UPDATE nodes SET updated_at = now() WHERE id = :id"), {"id": payload.node_id}
        )

    await session.commit()
    return Event.model_validate(row._mapping)

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Event

router = APIRouter()


@router.get("/timeline", response_model=list[Event])
async def timeline(
    node_id: UUID | None = Query(None),
    since: datetime | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[Event]:
    clauses = []
    params: dict = {"limit": limit}
    if node_id:
        clauses.append("node_id = :node_id")
        params["node_id"] = node_id
    if since:
        clauses.append("occurred_at >= :since")
        params["since"] = since
    if kind:
        clauses.append("kind = :kind")
        params["kind"] = kind
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = text(
        f"""
        SELECT id, node_id, source_id, kind, occurred_at, payload
        FROM events {where}
        ORDER BY occurred_at DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).all()
    return [Event.model_validate(r._mapping) for r in rows]

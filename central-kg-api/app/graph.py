"""Graph operations: upsert, traversal, hybrid search.

Tables are accessed via SQL text for simplicity (no ORM model mapping needed for the hack).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .embeddings import embed_one, embed_texts
from .models import Edge, Node


def _row_to_node(row: Any) -> Node:
    m = row._mapping
    return Node(
        id=m["id"],
        type=m["type"],
        name=m["name"],
        properties=m["properties"] or {},
        source_id=m.get("source_id"),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        score=m.get("score"),
    )


def _row_to_edge(row: Any) -> Edge:
    m = row._mapping
    return Edge(
        id=m["id"],
        source_node_id=m["source_node_id"],
        target_node_id=m["target_node_id"],
        type=m["type"],
        properties=m["properties"] or {},
        weight=m["weight"],
        source_id=m.get("source_id"),
        created_at=m["created_at"],
    )


async def upsert_node(
    session: AsyncSession,
    *,
    type_: str,
    name: str,
    properties: dict[str, Any] | None = None,
    source_id: UUID | None = None,
    embedding: list[float] | None = None,
) -> Node:
    sql = text(
        """
        INSERT INTO nodes (type, name, properties, source_id, embedding)
        VALUES (:type, :name, CAST(:properties AS jsonb), :source_id, :embedding)
        ON CONFLICT (type, lower(name)) DO UPDATE
        SET properties = nodes.properties || EXCLUDED.properties,
            source_id  = COALESCE(EXCLUDED.source_id, nodes.source_id),
            embedding  = COALESCE(EXCLUDED.embedding, nodes.embedding),
            updated_at = now()
        RETURNING id, type, name, properties, source_id, created_at, updated_at;
        """
    )
    res = await session.execute(
        sql,
        {
            "type": type_,
            "name": name,
            "properties": json.dumps(properties or {}),
            "source_id": source_id,
            "embedding": embedding,
        },
    )
    return _row_to_node(res.one())


async def get_node_by_ref(session: AsyncSession, type_: str, name: str) -> Node | None:
    sql = text(
        """
        SELECT id, type, name, properties, source_id, created_at, updated_at
        FROM nodes WHERE type = :type AND lower(name) = lower(:name) LIMIT 1
        """
    )
    res = await session.execute(sql, {"type": type_, "name": name})
    row = res.first()
    return _row_to_node(row) if row else None


async def get_node(session: AsyncSession, node_id: UUID) -> Node | None:
    sql = text(
        """
        SELECT id, type, name, properties, source_id, created_at, updated_at
        FROM nodes WHERE id = :id LIMIT 1
        """
    )
    res = await session.execute(sql, {"id": node_id})
    row = res.first()
    return _row_to_node(row) if row else None


async def upsert_edge(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    type_: str,
    properties: dict[str, Any] | None = None,
    weight: float = 1.0,
    source_id: UUID | None = None,
) -> Edge:
    sql = text(
        """
        INSERT INTO edges (source_node_id, target_node_id, type, properties, weight, source_id)
        VALUES (:s, :t, :type, CAST(:properties AS jsonb), :weight, :source_id)
        ON CONFLICT (source_node_id, target_node_id, type) DO UPDATE
        SET properties = edges.properties || EXCLUDED.properties,
            weight     = GREATEST(edges.weight, EXCLUDED.weight),
            source_id  = COALESCE(EXCLUDED.source_id, edges.source_id)
        RETURNING id, source_node_id, target_node_id, type, properties, weight, source_id, created_at;
        """
    )
    res = await session.execute(
        sql,
        {
            "s": source_node_id,
            "t": target_node_id,
            "type": type_,
            "properties": json.dumps(properties or {}),
            "weight": weight,
            "source_id": source_id,
        },
    )
    return _row_to_edge(res.one())


async def neighbors(session: AsyncSession, node_id: UUID, limit: int = 50) -> tuple[list[Node], list[Edge]]:
    edges_sql = text(
        """
        SELECT id, source_node_id, target_node_id, type, properties, weight, source_id, created_at
        FROM edges
        WHERE source_node_id = :id OR target_node_id = :id
        ORDER BY weight DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(edges_sql, {"id": node_id, "limit": limit})).all()
    edges = [_row_to_edge(r) for r in rows]
    ids: set[UUID] = set()
    for e in edges:
        ids.add(e.source_node_id)
        ids.add(e.target_node_id)
    if not ids:
        return [], []
    nodes_sql = text(
        """
        SELECT id, type, name, properties, source_id, created_at, updated_at
        FROM nodes WHERE id = ANY(:ids)
        """
    )
    nrows = (await session.execute(nodes_sql, {"ids": list(ids)})).all()
    return [_row_to_node(r) for r in nrows], edges


async def subgraph_bfs(
    session: AsyncSession, center_ids: list[UUID], hops: int = 2, node_limit: int = 100
) -> tuple[list[Node], list[Edge]]:
    """BFS hop-bounded subgraph using a recursive CTE."""
    if not center_ids:
        return [], []
    cte_sql = text(
        """
        WITH RECURSIVE frontier(node_id, depth) AS (
            SELECT id, 0 FROM nodes WHERE id = ANY(:center)
            UNION
            SELECT CASE WHEN e.source_node_id = f.node_id THEN e.target_node_id
                        ELSE e.source_node_id END,
                   f.depth + 1
            FROM frontier f
            JOIN edges e ON e.source_node_id = f.node_id OR e.target_node_id = f.node_id
            WHERE f.depth < :hops
        )
        SELECT DISTINCT node_id FROM frontier LIMIT :nlimit;
        """
    )
    rows = (
        await session.execute(cte_sql, {"center": center_ids, "hops": hops, "nlimit": node_limit})
    ).all()
    ids = [r._mapping["node_id"] for r in rows]
    if not ids:
        return [], []
    nodes_sql = text(
        """
        SELECT id, type, name, properties, source_id, created_at, updated_at
        FROM nodes WHERE id = ANY(:ids)
        """
    )
    edges_sql = text(
        """
        SELECT id, source_node_id, target_node_id, type, properties, weight, source_id, created_at
        FROM edges
        WHERE source_node_id = ANY(:ids) AND target_node_id = ANY(:ids)
        """
    )
    nrows = (await session.execute(nodes_sql, {"ids": ids})).all()
    erows = (await session.execute(edges_sql, {"ids": ids})).all()
    return [_row_to_node(r) for r in nrows], [_row_to_edge(r) for r in erows]


async def overview_centers(session: AsyncSession, limit: int = 12) -> list[UUID]:
    """Seed an at-a-glance view from the most-connected nodes (graph hubs).

    Falls back to most-recently-updated nodes when the graph has no edges yet.
    """
    deg_sql = text(
        """
        SELECT node_id FROM (
            SELECT source_node_id AS node_id FROM edges
            UNION ALL
            SELECT target_node_id AS node_id FROM edges
        ) z
        GROUP BY node_id
        ORDER BY count(*) DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(deg_sql, {"limit": limit})).all()
    if rows:
        return [r._mapping["node_id"] for r in rows]
    recent = (
        await session.execute(
            text("SELECT id FROM nodes ORDER BY updated_at DESC LIMIT :limit"),
            {"limit": limit},
        )
    ).all()
    return [r._mapping["id"] for r in recent]


async def hybrid_search(session: AsyncSession, q: str, limit: int = 12) -> list[Node]:
    """Hybrid retrieval: vector similarity (if embedding available) blended with trigram name match."""
    qvec = await embed_one(q)

    if qvec is not None:
        sql = text(
            """
            SELECT id, type, name, properties, source_id, created_at, updated_at,
                   (
                     0.7 * (1 - (embedding <=> CAST(:vec AS vector)))
                   + 0.3 * COALESCE(similarity(name, :q), 0)
                   ) AS score
            FROM nodes
            WHERE embedding IS NOT NULL
            ORDER BY score DESC
            LIMIT :limit
            """
        )
        rows = (await session.execute(sql, {"vec": qvec, "q": q, "limit": limit})).all()
        if rows:
            return [_row_to_node(r) for r in rows]

    # Fallback: trigram name + properties text search
    sql = text(
        """
        SELECT id, type, name, properties, source_id, created_at, updated_at,
               similarity(name, :q) AS score
        FROM nodes
        WHERE name % :q OR properties::text ILIKE '%' || :q || '%'
        ORDER BY score DESC NULLS LAST
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"q": q, "limit": limit})).all()
    return [_row_to_node(r) for r in rows]


async def embed_and_attach(session: AsyncSession, node_id: UUID, text_for_embed: str) -> None:
    vec = await embed_one(text_for_embed)
    if vec is None:
        return
    await session.execute(
        text("UPDATE nodes SET embedding = :v, updated_at = now() WHERE id = :id"),
        {"v": vec, "id": node_id},
    )


async def bulk_embed_nodes(session: AsyncSession, nodes_in: list[Node]) -> None:
    targets = [n for n in nodes_in if n is not None]
    if not targets:
        return
    payloads = [f"{n.type}: {n.name}. {json.dumps(n.properties)[:600]}" for n in targets]
    vecs = await embed_texts(payloads)
    for n, v in zip(targets, vecs, strict=False):
        if v is None:
            continue
        await session.execute(
            text("UPDATE nodes SET embedding = :v, updated_at = now() WHERE id = :id"),
            {"v": v, "id": n.id},
        )

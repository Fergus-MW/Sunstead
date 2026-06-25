"""Seed the knowledge graph from a graphify graph.json.

Usage:
    # Run graphify first to produce graphify-out/graph.json
    graphify /path/to/target/repo

    # Then load it into the KG
    python -m seed --graph-json /path/to/target/repo/graphify-out/graph.json \
                   --repo-name anthropics/anthropic-sdk-python

Reads .env in the central-kg-api directory for DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .graphify_adapter import EdgeRow, NodeRow, map_edge, map_node

logger = logging.getLogger("seed")

# Chunk sizes — asyncpg handles these comfortably in one round-trip each.
NODE_CHUNK = 500
EDGE_CHUNK = 1000


async def _connect():
    # Import here so `python -m seed --help` works without a configured env.
    import os
    from urllib.parse import urlparse, parse_qs

    import asyncpg

    raw = os.environ["DATABASE_URL"]
    # Allow either `postgresql+asyncpg://…` (SQLAlchemy-style) or `postgres://…`.
    raw = raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    # Accept ssl=require / sslmode=require and pass through asyncpg's string
    # form. We deliberately do NOT verify the cert chain here — the Aiven
    # service CA isn't in the system trust store, and downloading it on every
    # invocation is friction for a hack. ssl='require' encrypts the channel
    # without chain verification, matching what the SQLAlchemy + asyncpg
    # dialect does for the Lambda app.
    ssl_flag = (qs.get("ssl", qs.get("sslmode", ["disable"]))[0] or "disable").lower()
    ssl_arg: Any = ssl_flag if ssl_flag in {"disable", "allow", "prefer", "require"} else "require"

    return await asyncpg.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/") or "postgres",
        ssl=ssl_arg,
        timeout=30,
    )


async def _register_source(conn, *, kind: str, uri: str | None, title: str | None) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO sources (kind, uri, title, metadata)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING id
        """,
        kind,
        uri,
        title,
        json.dumps({"seeder": "graphify"}),
    )
    return str(row["id"])


async def _upsert_nodes(
    conn, rows: list[NodeRow], source_id: str
) -> dict[str, str]:
    """Insert/merge nodes; return {graphify_id: uuid_str}."""
    mapping: dict[str, str] = {}
    sql = """
        INSERT INTO nodes (type, name, properties, source_id)
        VALUES ($1, $2, $3::jsonb, $4::uuid)
        ON CONFLICT (type, lower(name)) DO UPDATE
        SET properties = nodes.properties || EXCLUDED.properties,
            source_id  = COALESCE(nodes.source_id, EXCLUDED.source_id),
            updated_at = now()
        RETURNING id
    """

    for start in range(0, len(rows), NODE_CHUNK):
        chunk = rows[start : start + NODE_CHUNK]
        # One transaction per chunk so partial failure can be retried cheaply.
        async with conn.transaction():
            for n in chunk:
                row = await conn.fetchrow(
                    sql, n.type, n.name, json.dumps(n.properties), source_id
                )
                mapping[n.graphify_id] = str(row["id"])
        logger.info("nodes upserted: %d/%d", min(start + NODE_CHUNK, len(rows)), len(rows))
    return mapping


async def _upsert_edges(
    conn,
    rows: list[EdgeRow],
    id_map: dict[str, str],
    source_id: str,
) -> int:
    sql = """
        INSERT INTO edges
            (source_node_id, target_node_id, type, properties, weight, source_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::uuid)
        ON CONFLICT (source_node_id, target_node_id, type) DO UPDATE
        SET properties = edges.properties || EXCLUDED.properties,
            weight     = GREATEST(edges.weight, EXCLUDED.weight)
    """
    skipped = 0
    inserted = 0
    batch: list[tuple] = []

    async def flush() -> None:
        nonlocal inserted
        if not batch:
            return
        async with conn.transaction():
            await conn.executemany(sql, batch)
        inserted += len(batch)
        batch.clear()
        logger.info("edges upserted so far: %d (skipped %d)", inserted, skipped)

    for e in rows:
        s = id_map.get(e.src_graphify_id)
        t = id_map.get(e.dst_graphify_id)
        if not s or not t:
            skipped += 1
            continue
        batch.append((s, t, e.type, json.dumps(e.properties), e.weight, source_id))
        if len(batch) >= EDGE_CHUNK:
            await flush()
    await flush()
    return skipped


async def run(graph_json: Path, repo_name: str, repo_url: str | None) -> None:
    t0 = time.perf_counter()
    payload = json.loads(graph_json.read_text())
    g_nodes = payload.get("nodes", [])
    g_edges = payload.get("links") or payload.get("edges") or []
    logger.info(
        "graph.json: %d nodes, %d edges (commit=%s)",
        len(g_nodes),
        len(g_edges),
        payload.get("built_at_commit"),
    )

    nodes = [map_node(n) for n in g_nodes if n.get("id")]
    edges = [map_edge(e) for e in g_edges if e.get("source") and e.get("target")]
    logger.info("mapped: %d nodes, %d edges", len(nodes), len(edges))

    conn = await _connect()
    try:
        source_id = await _register_source(
            conn,
            kind="graphify_seed",
            uri=repo_url,
            title=f"graphify seed: {repo_name}",
        )
        logger.info("source row: %s", source_id)

        id_map = await _upsert_nodes(conn, nodes, source_id)
        skipped = await _upsert_edges(conn, edges, id_map, source_id)

        n_count = await conn.fetchval("SELECT count(*) FROM nodes WHERE source_id = $1::uuid", source_id)
        e_count = await conn.fetchval("SELECT count(*) FROM edges WHERE source_id = $1::uuid", source_id)
        total_n = await conn.fetchval("SELECT count(*) FROM nodes")
        total_e = await conn.fetchval("SELECT count(*) FROM edges")
    finally:
        await conn.close()

    dt = time.perf_counter() - t0
    print(
        f"\n=== seed done in {dt:.1f}s ===\n"
        f"  source_id     : {source_id}\n"
        f"  nodes (this)  : {n_count}\n"
        f"  edges (this)  : {e_count}\n"
        f"  edges skipped : {skipped}  (endpoint not in graph.json)\n"
        f"  nodes (total) : {total_n}\n"
        f"  edges (total) : {total_e}\n"
    )


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="Seed the KG from a graphify graph.json")
    p.add_argument("--graph-json", type=Path, required=True, help="Path to graphify-out/graph.json")
    p.add_argument("--repo-name", required=True, help="Logical repo name (e.g. anthropics/anthropic-sdk-python)")
    p.add_argument("--repo-url", default=None, help="Optional canonical URL (e.g. https://github.com/...)")
    args = p.parse_args()

    if not args.graph_json.exists():
        print(f"graph.json not found: {args.graph_json}", file=sys.stderr)
        return 2

    asyncio.run(run(args.graph_json, args.repo_name, args.repo_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

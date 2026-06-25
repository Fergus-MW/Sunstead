"""Mirror every row in `nodes` into an Aiven OpenSearch index.

The OpenSearch `_id` is the same UUID as `nodes.id`, so the agent flow per
PLAN §3.5 is just:

    aiven_opensearch_search(q) -> [uuid, uuid, ...]
    aiven_pg_read(WITH RECURSIVE ... starting from those uuids)

Usage:
    OPENSEARCH_URL=https://avnadmin:PASS@host:port \\
        python -m seed.mirror_opensearch --index kg-nodes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import httpx
from dotenv import load_dotenv

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "code_text": {
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "type": {"type": "keyword"},
            "name": {
                "type": "text",
                "analyzer": "code_text",
                "fields": {"raw": {"type": "keyword"}},
            },
            "label": {"type": "text", "analyzer": "code_text"},
            "source_file": {
                "type": "text",
                "analyzer": "code_text",
                "fields": {"raw": {"type": "keyword"}},
            },
            # Free-text bag from utterances, decisions, etc. — the actual
            # body of the node, not just its name.
            "text": {"type": "text", "analyzer": "code_text"},
            "speaker": {"type": "keyword"},
            "meeting_id": {"type": "keyword"},
            "occurred_at": {"type": "date"},
            "community": {"type": "integer"},
            "source_id": {"type": "keyword"},
            "graphify_id": {"type": "keyword"},
            "graphify_file_type": {"type": "keyword"},
        }
    },
}


async def _pg_connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(raw)
    return await asyncpg.connect(
        host=p.hostname,
        port=p.port or 5432,
        user=p.username,
        password=p.password,
        database=p.path.lstrip("/") or "postgres",
        ssl="require",
        timeout=30,
    )


def _bulk_actions(rows: list[dict], index: str) -> str:
    out: list[str] = []
    for r in rows:
        raw_props = r["properties"]
        props = json.loads(raw_props) if isinstance(raw_props, str) else (raw_props or {})
        # Utterances carry their dialogue in properties.text; commits carry
        # the commit subject; decisions / topics fall back to the node name.
        text_parts: list[str] = []
        if props.get("text"):
            text_parts.append(str(props["text"]))
        if props.get("subject"):
            text_parts.append(str(props["subject"]))
        if props.get("title") and props["title"] != r["name"]:
            text_parts.append(str(props["title"]))
        doc = {
            "type": r["type"],
            "name": r["name"],
            "label": props.get("label") or r["name"],
            "source_file": props.get("source_file"),
            "text": " ".join(text_parts) or None,
            "speaker": props.get("speaker"),
            "meeting_id": props.get("meeting_id"),
            "occurred_at": props.get("occurred_at"),
            "community": props.get("community"),
            "source_id": str(r["source_id"]) if r["source_id"] else None,
            "graphify_id": props.get("graphify_id"),
            "graphify_file_type": props.get("graphify_file_type"),
        }
        # Drop None values so OpenSearch doesn't store a wall of nulls.
        doc = {k: v for k, v in doc.items() if v is not None}
        out.append(json.dumps({"index": {"_id": str(r["id"])}}))
        out.append(json.dumps(doc, default=str))
    return "\n".join(out) + "\n"


async def run(index: str, batch: int) -> None:
    os_url = os.environ["OPENSEARCH_URL"].rstrip("/")
    t_total = time.perf_counter()

    async with httpx.AsyncClient(timeout=60, verify=False) as os_client:
        # 1. Drop + recreate the index for a clean benchmark.
        await os_client.delete(f"{os_url}/{index}")
        r = await os_client.put(f"{os_url}/{index}", json=INDEX_MAPPING)
        r.raise_for_status()
        print(f"index {index} created")

        # 2. Stream every node out of Postgres.
        conn = await _pg_connect()
        try:
            rows = await conn.fetch(
                "SELECT id, type, name, properties, source_id FROM nodes"
            )
        finally:
            await conn.close()
        rows = [dict(r) for r in rows]
        print(f"fetched {len(rows)} nodes from Postgres in {time.perf_counter() - t_total:.1f}s")

        # 3. Bulk-index in chunks.
        t_index = time.perf_counter()
        for start in range(0, len(rows), batch):
            chunk = rows[start : start + batch]
            payload = _bulk_actions(chunk, index)
            r = await os_client.post(
                f"{os_url}/{index}/_bulk",
                content=payload,
                headers={"Content-Type": "application/x-ndjson"},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("errors"):
                bad = [i for i in body["items"] if i.get("index", {}).get("error")]
                if bad:
                    print(f"WARN: {len(bad)} errors in chunk {start}: {bad[0]}")
            print(f"indexed {min(start + batch, len(rows))}/{len(rows)}")
        t_index_done = time.perf_counter() - t_index

        # 4. Refresh + count.
        await os_client.post(f"{os_url}/{index}/_refresh")
        r = await os_client.get(f"{os_url}/{index}/_count")
        count = r.json()["count"]

    print(
        f"\n=== mirror done in {time.perf_counter() - t_total:.1f}s ===\n"
        f"  indexed       : {count}\n"
        f"  bulk index    : {t_index_done:.1f}s ({count / max(t_index_done, 1e-6):.0f} docs/s)\n"
    )


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--index", default="kg-nodes")
    p.add_argument("--batch", type=int, default=500)
    args = p.parse_args()
    asyncio.run(run(args.index, args.batch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Retrieval benchmark for the standup-grounded knowledge graph.

Each "scenario" runs:
  1. OpenSearch BM25 over node text
  2. Postgres recursive-CTE 2-hop traversal seeded with those ids
  3. Reports per-stage and total wall-clock latency

Run:
    python -m bench.retrieval                  # default scenarios
    python -m bench.retrieval --hops 1         # tweak traversal depth
    python -m bench.retrieval --warmup 2       # change warmup count
    python -m bench.retrieval --json           # machine-readable output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import httpx
from dotenv import load_dotenv

# Each scenario is the kind of question the listener would ask during the
# standup: text-search-able phrasing the agent would pull from the
# transcript or from a participant question.
SCENARIOS = [
    {"id": "streaming-refactor", "q": "streaming refactor retry policy"},
    {"id": "memory-tool-bug",    "q": "memory tool parent directory"},
    {"id": "who-is-blocked",     "q": "blocked codegen agents"},
    {"id": "release-status",     "q": "release 0.112 Thursday"},
    {"id": "sync-vs-async",      "q": "sync async retry policy"},
    {"id": "general-streaming",  "q": "streaming"},
    {"id": "general-retry",      "q": "retry"},
]


CTE_2HOP_UNFILTERED = """
WITH RECURSIVE frontier(node_id, depth) AS (
  SELECT id, 0 FROM nodes WHERE id = ANY($1::uuid[])
  UNION
  SELECT
    CASE WHEN e.source_node_id = f.node_id THEN e.target_node_id
         ELSE e.source_node_id END,
    f.depth + 1
  FROM frontier f
  JOIN edges e
    ON e.source_node_id = f.node_id OR e.target_node_id = f.node_id
  WHERE f.depth < $2
)
SELECT COUNT(DISTINCT node_id) FROM frontier;
"""


# Demo-relevant edge allowlist — matches app.graph.subgraph_bfs defaults.
# Skipping relates_to / re_exports cuts the frontier on hub nodes by ~80%.
EDGE_ALLOWLIST = [
    "mentions", "said", "in_meeting", "attended",
    "authored", "touches",
    "imports", "calls", "part_of", "derived_from", "rationale_for",
]

CTE_2HOP_FILTERED = """
WITH RECURSIVE frontier(node_id, depth) AS (
  SELECT id, 0 FROM nodes WHERE id = ANY($1::uuid[])
  UNION
  SELECT
    CASE WHEN e.source_node_id = f.node_id THEN e.target_node_id
         ELSE e.source_node_id END,
    f.depth + 1
  FROM frontier f
  JOIN edges e
    ON (e.source_node_id = f.node_id OR e.target_node_id = f.node_id)
    AND e.type = ANY($3::text[])
  WHERE f.depth < $2
)
SELECT COUNT(DISTINCT node_id) FROM frontier;
"""


# Frontier-capped version: stop expanding once we've collected enough
# distinct ids. Prevents hub-node explosions (Stream → 2500+ neighbours).
CTE_2HOP_CAPPED = """
WITH RECURSIVE frontier(node_id, depth) AS (
  SELECT id, 0 FROM nodes WHERE id = ANY($1::uuid[])
  UNION
  SELECT
    CASE WHEN e.source_node_id = f.node_id THEN e.target_node_id
         ELSE e.source_node_id END,
    f.depth + 1
  FROM frontier f
  JOIN edges e
    ON (e.source_node_id = f.node_id OR e.target_node_id = f.node_id)
    AND e.type = ANY($3::text[])
  WHERE f.depth < $2
)
SELECT COUNT(*) FROM (SELECT DISTINCT node_id FROM frontier LIMIT 200) s;
"""


async def main(hops: int, warmup: int, repeats: int, sql_template: str, as_json: bool, filtered: bool) -> dict:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    pg_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    os_url = os.environ["OPENSEARCH_URL"].rstrip("/")

    p = urlparse(pg_url)
    pg = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        user=p.username, password=p.password,
        database=p.path.lstrip("/"), ssl="require",
    )

    async with httpx.AsyncClient(timeout=30, verify=False) as os_client:
        # Warm up both engines so we measure steady-state, not cold latencies.
        for _ in range(warmup):
            await os_client.post(
                f"{os_url}/kg-nodes/_search",
                json={"size": 1, "query": {"match_all": {}}},
            )
            await pg.fetchval("SELECT 1")

        results: list[dict] = []
        for sc in SCENARIOS:
            samples: list[dict] = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                os_resp = await os_client.post(
                    f"{os_url}/kg-nodes/_search",
                    json={
                        "size": 8,
                        "query": {
                            "multi_match": {
                                "query": sc["q"],
                                "fields": ["name^3", "source_file^2", "label", "text"],
                            }
                        },
                        "_source": False,
                    },
                )
                t1 = time.perf_counter()
                body = os_resp.json()
                ids = [h["_id"] for h in body["hits"]["hits"]]
                if not ids:
                    samples.append({"os_ms": (t1 - t0) * 1000, "pg_ms": None, "total_ms": (t1 - t0) * 1000, "hits": 0, "frontier": 0})
                    continue
                if filtered:
                    count = await pg.fetchval(sql_template, ids, hops, EDGE_ALLOWLIST)
                else:
                    count = await pg.fetchval(sql_template, ids, hops)
                t2 = time.perf_counter()
                samples.append(
                    {
                        "os_ms": (t1 - t0) * 1000,
                        "pg_ms": (t2 - t1) * 1000,
                        "total_ms": (t2 - t0) * 1000,
                        "hits": len(ids),
                        "frontier": count,
                    }
                )

            ok = [s for s in samples if s["pg_ms"] is not None]
            results.append(
                {
                    "scenario": sc["id"],
                    "query": sc["q"],
                    "os_p50": statistics.median(s["os_ms"] for s in samples),
                    "pg_p50": statistics.median(s["pg_ms"] for s in ok) if ok else None,
                    "total_p50": statistics.median(s["total_ms"] for s in samples),
                    "total_min": min(s["total_ms"] for s in samples),
                    "total_max": max(s["total_ms"] for s in samples),
                    "hits": samples[-1]["hits"],
                    "frontier": samples[-1]["frontier"],
                }
            )

    await pg.close()

    summary = {
        "config": {"hops": hops, "warmup": warmup, "repeats": repeats, "cte": sql_template[:40]},
        "p50_total_ms": statistics.median(r["total_p50"] for r in results),
        "scenarios": results,
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"\nhops={hops}  warmup={warmup}  repeats={repeats}")
        print(f"{'scenario':22s}  {'OS p50':>7s}  {'PG p50':>7s}  {'total p50':>10s}  {'min':>5s}  {'max':>5s}  {'hits':>4s}  {'frontier':>8s}")
        for r in results:
            pg = f"{r['pg_p50']:>5.1f}ms" if r["pg_p50"] is not None else "    -  "
            print(f"{r['scenario']:22s}  {r['os_p50']:>5.1f}ms  {pg}  {r['total_p50']:>8.1f}ms  {r['total_min']:>3.0f}ms  {r['total_max']:>3.0f}ms  {r['hits']:>4d}  {r['frontier']:>8d}")
        print(f"\noverall p50: {summary['p50_total_ms']:.1f}ms")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hops", type=int, default=2)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--unfiltered", action="store_true",
                   help="Use the original CTE that follows every edge type (baseline)")
    p.add_argument("--capped", action="store_true",
                   help="Cap the frontier at 200 distinct nodes (default 2-hop expansion)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.unfiltered:
        sql, filtered = CTE_2HOP_UNFILTERED, False
    elif args.capped:
        sql, filtered = CTE_2HOP_CAPPED, True
    else:
        sql, filtered = CTE_2HOP_FILTERED, True
    asyncio.run(main(args.hops, args.warmup, args.repeats, sql, args.json, filtered=filtered))

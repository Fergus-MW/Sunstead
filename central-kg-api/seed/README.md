# Seed CLI

Two one-shot scripts that populate the knowledge graph from external sources.

```bash
# 1. Generate a graphify graph.json from any code repo
graphify /path/to/target/repo            # writes graphify-out/graph.json

# 2. Load it into Aiven Postgres
python -m seed --graph-json <path/to/graphify-out/graph.json> \
               --repo-name owner/repo \
               --repo-url https://github.com/owner/repo

# 3. (Optional) Mirror the resulting nodes into Aiven OpenSearch for BM25/k-NN
OPENSEARCH_URL=https://avnadmin:PASS@host:port \
    python -m seed.mirror_opensearch --index kg-nodes
```

Both read `central-kg-api/.env` for `DATABASE_URL` and `OPENSEARCH_URL`.

## Why this lives outside the FastAPI

Per `docs/PLAN.md §3.5`, the dominant agent → graph path goes through Aiven MCP
(`aiven_pg_read` / `aiven_pg_write`). The seed is the one place where per-row
MCP/REST round-trips would be too slow, so it bypasses the API and writes
straight to Postgres + OpenSearch.

## Measured run (anthropics/anthropic-sdk-python, do-lon ↔ London laptop)

| Step | Time | Notes |
|---|---:|---|
| `graphify .` | ~3 min | 1137 code files + 22 docs via Claude semantic pass |
| `python -m seed` | 525s | 7730 input → **6183 unique nodes** + **26179 edges** |
| `python -m seed.mirror_opensearch` | 9.7s | 6183 docs into OpenSearch @ 1263 docs/sec |

## Measured query latency (warm, trans-Atlantic)

Hybrid `OS BM25 → ids → PG recursive CTE` per `docs/PLAN.md §3.5`:

| Query | OpenSearch | Postgres CTE | Total | 2-hop frontier |
|---|---:|---:|---:|---:|
| `retry` | 69ms | 56ms | **125ms** | 93 nodes |
| `streaming` | 77ms | 57ms | **134ms** | 1142 nodes |
| `auth` | 68ms | 56ms | **124ms** | 24 nodes |
| `message batch` | 74ms | 55ms | **129ms** | 206 nodes |
| `stream` | 67ms | 59ms | **126ms** | 811 nodes |

Server-side work:
- OpenSearch `took`: 3–57ms (BM25 over 6183 docs)
- Postgres recursive CTE: 1–6ms

The other ~110ms is network (London laptop ↔ Aiven `do-lon`). A Lambda in
`aws-eu-west-2` running this exact hybrid should hit **~20–30ms end-to-end**.

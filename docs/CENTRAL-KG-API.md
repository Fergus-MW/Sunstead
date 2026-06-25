# Central KG API

> The graph + vector context service for the Sunstead listener and worker agents.
> Lives at `central-kg-api/`. Reads and writes to an Aiven Postgres instance with
> pgvector. Deployable as a single AWS Lambda behind API Gateway (Mangum).

Status as of this doc: **live against Aiven and seeded**. Schema applied, async DB
round-trip verified end-to-end, and the **repo-seed CLI is implemented** (`seed/`,
tree-sitter + git → ~6,183 nodes / 26,179 edges from `anthropic-sdk-python`) with a
**seed-time OpenSearch mirror** (`seed/mirror_opensearch.py`). Still open: a
**runtime** OpenSearch quicksearch endpoint (the app queries pgvector + trigram only)
and the `kg.updates` Kafka consumer — see [§7 Gaps vs PLAN.md](#7-gaps-vs-planmd).

---

## 1. Purpose

A low-latency context API the rest of the agent suite calls into. It hides the
fact that everything is Postgres: callers ask for entities, neighborhoods, and
ranked context for a question, and get back small focused subgraphs.

Two write paths feed it:

1. **Repo seeding** — tree-sitter + git walk over the target codebase produces
   `file/function/class/commit/person` nodes and `DEFINES/IMPORTS/CALLS/
   AUTHORED/TOUCHES` edges. *(implemented — `seed/`; ~6,183 nodes / 26,179 edges seeded live.)*
2. **Live meeting writes** — the Listener agent emits `meeting/utterance/claim`
   nodes plus `MENTIONS/ABOUT` edges into the same graph, grounding what's said
   in the code.

Both paths converge on the same `nodes` + `edges` tables, so a single graph
traversal can answer "what code does this claim relate to?" or "who has
touched the modules this meeting is about?".

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Async, well-typed, matches PLAN.md choice |
| DB driver | **SQLAlchemy 2.0 async + asyncpg** | Connection pooling, async-first |
| Storage | **Aiven Postgres 17** + `pgvector`, `pg_trgm`, `pgcrypto` | One database for graph + vectors (PLAN.md §3.3) |
| LLM | **Anthropic Claude** (`claude-sonnet-4-6`) | JSON-mode entity extraction |
| Embeddings | **OpenAI** (`text-embedding-3-small`, 1536-d) | Cheap, swap-in friendly |
| Deploy | **Mangum → AWS Lambda** | One zip / one container, no always-on cost |
| Local dev | **`docker compose up`** (`pgvector/pgvector:pg16`) | Same schema script applied at init |

---

## 3. Data model

```sql
sources(id, kind, uri, title, content, metadata jsonb, created_at)
nodes  (id, type, name, properties jsonb, embedding vector(1536),
        source_id → sources, created_at, updated_at)
edges  (id, source_node_id → nodes, target_node_id → nodes, type,
        properties jsonb, weight, source_id → sources, created_at)
events (id, node_id → nodes, source_id → sources, kind, occurred_at, payload jsonb)
```

Indexes that matter:
- `nodes (type, lower(name))` UNIQUE — canonical `(type, name)` key; lets any
  caller refer to a node by `("function", "transcribe")` without knowing UUIDs.
- `nodes USING gin (name gin_trgm_ops)` — fuzzy name match.
- `nodes USING ivfflat (embedding vector_cosine_ops)` — vector similarity.
- `edges (source_node_id, target_node_id, type)` UNIQUE — idempotent upserts.

`events` is the time-ordered log behind `/timeline` — it's what makes the API
useful for "what happened since 10 minutes ago" queries during a live meeting.

### Node and edge vocabulary

Defined as `Literal` types in `app/models.py`:

- **Node types** — `person`, `company`, `meeting`, `task`, `workflow`,
  `requirement`, `feature`, `user_story`, `code_module`, `product`,
  `source_document`, `topic`, `decision`.
- **Edge types** — `depends_on`, `discussed_in`, `implements`, `relates_to`,
  `derived_from`, `assigned_to`, `blocks`, `mentions`, `part_of`, `owns`.

Code-seeding will add `file`, `function`, `class`, `commit` node types and
`DEFINES/IMPORTS/CALLS/AUTHORED/TOUCHES` edges per PLAN.md §9.1; the schema is
intentionally open (`type` is just `TEXT`) so seeding can land without a
migration.

---

## 4. API surface

| Method | Path                    | Purpose                                                              |
|--------|-------------------------|----------------------------------------------------------------------|
| POST   | `/ingest`               | Add a raw source (transcript / doc / code chunk); optionally run extraction |
| POST   | `/extract`              | Run Claude extraction over text or an existing `source_id`           |
| POST   | `/node`                 | Upsert a node by `(type, name)`; optionally embed                    |
| POST   | `/link`                 | Upsert an edge — endpoints by id **or** by `(type, name)` ref        |
| GET    | `/query?q=…`            | Hybrid search → ranked nodes + focused subgraph                      |
| GET    | `/entity/{id}?hops=N`   | A node plus its N-hop neighborhood                                   |
| GET    | `/subgraph?center=…&hops=N` *or* `?q=…` | BFS subgraph around ids, or seeded by a search query |
| POST   | `/update`               | Append an `event`; bumps the related node's `updated_at`             |
| GET    | `/timeline`             | Time-ordered events, filterable by `node_id` / `kind` / `since`      |
| GET    | `/source/{id}`          | Fetch the raw source row                                             |
| GET    | `/health`               | Liveness                                                             |
| GET    | `/docs`                 | OpenAPI / Swagger UI                                                 |

### Retrieval (`/query` and `/subgraph?q=`)

```
score = 0.7 * (1 - cosine(query_embedding, node.embedding))
      + 0.3 * trigram_similarity(node.name, query)
```

If `OPENAI_API_KEY` isn't set, the embedding column stays NULL and the API
falls back to trigram-only — graceful degrade, no crash. Hop-bounded BFS is a
recursive CTE in `app/graph.py:subgraph_bfs`.

### Idempotency

- Nodes upserted by `(type, lower(name))` — `properties` are JSONB-merged on
  conflict, never overwritten wholesale.
- Edges upserted by `(source_node_id, target_node_id, type)` — `weight` is
  monotonic (`GREATEST`), `properties` JSONB-merged.

That means re-running an ingest on the same transcript is a no-op for shape and
strictly additive for properties — safe for the live writer.

---

## 5. Layout

```
central-kg-api/
├── docker-compose.yml      # pgvector/pgvector:pg16, schema auto-loaded
├── schema.sql              # source of truth — apply against Aiven once
├── pyproject.toml          # uv-managed deps
├── .env.example            # template (the real .env is gitignored)
├── main.py                 # python main.py → uvicorn dev server
└── app/
    ├── main.py             # FastAPI app + CORS + lifespan; exports `handler = Mangum(app)`
    ├── config.py           # pydantic-settings
    ├── db.py               # async engine + session
    ├── models.py           # pydantic schemas + node/edge type literals
    ├── extract.py          # Claude JSON-mode entity extraction
    ├── embeddings.py       # OpenAI embeddings (no-op without key)
    ├── graph.py            # upsert_node / upsert_edge / subgraph_bfs / hybrid_search
    └── routers/
        ├── ingest.py    extract.py    link.py
        ├── query.py     entity.py     subgraph.py
        └── update.py    timeline.py
```

---

## 6. Running it

### Local (Docker)

```bash
cd central-kg-api
docker compose up -d                  # pgvector/pgvector:pg16, schema auto-loaded
uv venv && source .venv/bin/activate
uv pip install -e .
cp .env.example .env                  # fill ANTHROPIC_API_KEY, OPENAI_API_KEY
python main.py                        # → http://localhost:8000/docs
```

### Against Aiven (current provisioning)

A free-tier Aiven Postgres is already running:

```
project : jq01
service : central-kg-pg
plan    : pg:free-1-1gb       (1 CPU / 1 GB RAM / 1 GB disk — fine for the hack)
cloud   : do-lon
host    : central-kg-pg-jq01.l.aivencloud.com:28093
db      : defaultdb
```

URI shape:

```
DATABASE_URL=postgresql+asyncpg://avnadmin:<password>@central-kg-pg-jq01.l.aivencloud.com:28093/defaultdb?ssl=require
```

The local `.env` (gitignored) has the real password — ask Alex or rotate via
`avn service user-password-reset --project jq01 --username avnadmin --new-password <…> central-kg-pg`.
The Aiven API now returns `<redacted>` for stored passwords, so the only way
to get a working credential is to reset it.

To apply the schema against Aiven:

```bash
psql "$AIVEN_URL" -f schema.sql      # idempotent — all CREATEs use IF NOT EXISTS
```

### As a Lambda

`app.main:handler = Mangum(app, lifespan="off")` is the AWS Lambda entrypoint.

1. Build a zip or container image with the project + deps.
2. Set the handler to `app.main.handler`.
3. Set env vars: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
4. The Lambda's egress must reach `*.aivencloud.com:28093` (open the SG or run
   the function outside a VPC).

---

## 7. Gaps vs PLAN.md

PLAN.md is the team contract; this section is the honest delta so we know what
still needs doing or deciding.

| PLAN.md says                                | This service today                            | Status      |
|---------------------------------------------|------------------------------------------------|-------------|
| Schema `nodes(kind, key, props, embedding)`, `edges(src_id, dst_id, rel, props)` with BIGINT ids and HNSW index | `nodes(type, name, properties, embedding)`, `edges(source_node_id, target_node_id, type, properties)` with UUID ids and IVFFLAT index | **Naming divergence** — see decision below |
| Endpoints: `POST /query`, `/semantic_search`, `/quicksearch`, `/upsert`, `/seed` | `POST /ingest /extract /node /link /update`; `GET /query /entity/:id /subgraph /timeline` | **Surface divergence** |
| OpenSearch mirror for quicksearch          | Seed-time mirror **done** (`seed/mirror_opensearch.py`); runtime `/quicksearch` endpoint not wired — app uses `pg_trgm` + pgvector | **Partial** |
| `seed` CLI: tree-sitter + git → nodes/edges | **Done** — `seed/` (tree-sitter + git, `graphify_adapter.py`); ~6,183 nodes / 26,179 edges seeded live | **Done** |
| `kg.updates` Kafka consumer → async writes | Not implemented                                | **Missing** |
| HNSW vector index                          | IVFFLAT (Postgres 17 + pgvector supports both) | **Minor**   |

**Recommendation to the team** (open to discussion before merge):

- **Schema names** — I diverged because `(type, name)` reads more naturally for
  the live-meeting write path ("upsert a `task` named `Build landing page`")
  and JSONB-merge on `properties` keeps the node honest. Happy to rename to
  `(kind, key, props)` if Person 2 prefers — it's a one-pass migration since
  no data is committed yet. **Not happy** to switch to BIGINT ids; UUIDs make
  the Listener safe to write without round-tripping for new ids.
- **Endpoint names** — `/upsert` collapses node + edge into one route which
  feels lossy. Suggest keeping `/node` + `/link`, and adding `/semantic_search`
  and `/quicksearch` as thin aliases over the existing hybrid `/query` once
  OpenSearch lands.
- **HNSW** — switch to HNSW per PLAN.md as soon as we have ≥10k nodes. IVFFLAT
  is fine while the table is empty (it warned us so on apply).
- **OpenSearch + seed + Kafka consumer** — three follow-up branches once the
  shape is agreed. Tracked in [§8](#8-todo-next).

---

## 8. TODO next

1. ~~**`seed` CLI**~~ ✅ **done** — tree-sitter + `git log` over a target repo, bulk
   upsert into `nodes`/`edges` (`central-kg-api/seed/`, `graphify_adapter.py`).
2. **OpenSearch — runtime path** — the seed-time mirror exists
   (`seed/mirror_opensearch.py`); still to do is the live `GET /quicksearch?q=…`
   endpoint (the MCP exposes no OpenSearch search tool, so query the OS HTTP endpoint directly — see `infra/README.md`).
3. **`kg.updates` Kafka consumer** — listen on the Aiven Kafka bus; the
   Listener agent publishes `(node|edge) upsert` events and we apply them
   async so the live write path never blocks the call.
4. **Auth** — currently open. Add a shared bearer token via env var before
   the demo if we expose this beyond the VPC.
5. **Tests** — at minimum a Docker-Compose-backed integration test that runs
   `/ingest` → `/query` → asserts a subgraph comes back.

---

## 9. Quick test

```bash
# Ingest a tiny transcript and see what comes back
curl -s -X POST http://localhost:8000/ingest -H 'content-type: application/json' -d '{
  "kind": "transcript",
  "title": "standup 2026-06-25",
  "content": "Alex is blocked on the auth migration. Jiaqi will pair with him this afternoon. The migration depends on the new session token schema legal asked for."
}' | jq '.extracted_nodes[].name, .extracted_edges | length'

# Then query it
curl -s 'http://localhost:8000/query?q=auth%20migration&hops=2' | jq '.nodes[].name'
```

If `ANTHROPIC_API_KEY` is unset, `/ingest` still creates the `source` row but
`extracted_nodes/edges` will be empty — useful for dev without burning tokens.

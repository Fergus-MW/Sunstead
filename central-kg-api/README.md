# Central KG API

A real-time agent context API. Specialized agents query pre-digested,
graph-shaped company context (people, meetings, tasks, requirements, code,
documents) and get back focused subgraphs — fast.

Backed by Postgres + pgvector (Aiven-ready). Deployable as an AWS Lambda
behind API Gateway via [Mangum](https://mangum.io/).

## Stack

- **FastAPI** + **Mangum** (Lambda adapter)
- **SQLAlchemy 2.0 async** + **asyncpg**
- **pgvector** for hybrid vector + graph retrieval
- **Anthropic Claude** for ontology / entity extraction
- **OpenAI** embeddings (swap-in optional)

## Data model

| Table     | Purpose                                                        |
| --------- | -------------------------------------------------------------- |
| `sources` | raw documents / transcripts / code metadata                    |
| `nodes`   | entities (person, meeting, task, requirement, code_module, …)  |
| `edges`   | typed relationships (depends_on, discussed_in, implements, …)  |
| `events`  | time-ordered updates for live-meeting / task follow-up         |

Vector similarity on `nodes.embedding` is blended with trigram name match
inside `/query` and `/subgraph`.

## API surface

| Method | Path                  | Purpose                                                         |
| ------ | --------------------- | --------------------------------------------------------------- |
| POST   | `/ingest`             | Add a raw source; optionally trigger Claude extraction          |
| GET    | `/source/{id}`        | Fetch the raw source row                                        |
| POST   | `/extract`            | Run Claude extraction over text or an existing source           |
| POST   | `/node`               | Upsert a node by (type, name)                                   |
| GET    | `/node?type=&name=`   | Find nodes by type/name filter (no UUID needed)                 |
| POST   | `/link`               | Upsert an edge (by ids or by `(type,name)` refs)                |
| GET    | `/query?q=…`          | Hybrid search (pgvector + trigram) → ranked nodes + subgraph    |
| GET    | `/search?q=…`         | **OpenSearch BM25** search → ranked nodes + subgraph (trigram fallback if OpenSearch is down) |
| GET    | `/entity/{id}?hops=N` | A node plus its N-hop neighborhood                              |
| GET    | `/overview`           | No-query landing view: the graph's busiest hubs + neighborhoods |
| GET    | `/subgraph`           | BFS subgraph around `center=` ids or seeded by `q=` (`hops`, `node_limit`) |
| POST   | `/update`             | Append an event; bumps the related node's `updated_at`          |
| GET    | `/timeline`           | Time-ordered events, optionally filtered by node / kind / since |
| GET    | `/health`             | Liveness                                                        |

OpenAPI is at `/docs`. Entity/relationship extraction (`/ingest` + `/extract`) is centralized through one
`graph.persist_extracted_graph()` upsert; the extraction prompt's node/edge vocabulary is generated from
`app/models.py` so it never drifts from the schema.

## Run locally

```bash
# 1. Boot Postgres + pgvector
docker compose up -d

# 2. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 3. Configure
cp .env.example .env
# fill ANTHROPIC_API_KEY (and OPENAI_API_KEY for embeddings)

# 4. Run
python main.py
# → http://localhost:8000/docs
```

## Deploying as a Lambda

`app.main:handler` is a `Mangum(app)` instance — drop-in for AWS Lambda
behind API Gateway (HTTP API or REST API). Typical setup:

1. Build a deployment zip (or container image) with the project + deps.
2. Set the Lambda handler to `app.main.handler`.
3. Set env vars: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
4. Make sure the Lambda's VPC / security group can reach your Aiven Postgres.

## Switching to Aiven Postgres

```env
DATABASE_URL=postgresql+asyncpg://avnadmin:<pass>@<host>:<port>/defaultdb?ssl=require
```

Then apply `schema.sql` once against the Aiven instance
(`psql "$AIVEN_URL" -f schema.sql`). pgvector and pg_trgm must be enabled
on the Aiven service — both are available by default.

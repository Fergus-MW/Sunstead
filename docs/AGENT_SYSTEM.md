# Agent System — focused plan (our part)

> Scope-down of [PLAN.md](PLAN.md) to **the part our team owns: the worker agent suite + the FE gateway.**
> Everything our agents touch in the data layer goes through **Aiven MCP** (the challenge's 34% spine — see
> [HACKINFO.md](HACKINFO.md)). Workers run as **AWS Lambda**; the FE is on **Vercel**; the live-call /
> transcription stack is a teammate's, on **EC2**; Aiven (Postgres + Kafka + OpenSearch) is the data layer.
>
> `PLAN.md` is the whole-system source of truth. **This doc is authoritative for our subsystem.** When `PLAN.md`
> changes in ways that touch our seams (Kafka topics, the message envelope), we update this doc first, then code.

---

## 1. What we own vs. what we integrate with

We own the **worker agents** and the **thin FE gateway**. We do **not** own the listener or the transcription
pipeline — a teammate runs those on EC2 (they're close to the call and need a persistent process; see §3). The
knowledge graph is a teammate's `central-kg-api`, but **our agents never call its HTTP API** — they read/write the
same Aiven Postgres directly through Aiven MCP (§6).

```
  TRANSCRIPTION (teammate, EC2)          OURS (workers = Lambda, gateway = always-on)
 ┌───────────────────────────┐          ┌──────────────────────────────────────────────┐
 │ call-gateway (Recall→STT)  │          │  agent.tasks.web ─▶ web-agent  (Lambda)        │
 │ listener (Agent A brain)   │  Kafka   │  agent.tasks.data ─▶ data-agent (Lambda)       │
 │  └─ emits task.create ─────┼────────▶ │  agent.tasks.git ─▶ git-agent  (Lambda)        │
 │  └─ consumes agent.results◀┼──────────┤  └─ all emit ─▶ agent.results / kg.updates     │
 └───────────────────────────┘          └───────────────┬──────────────────────────────┘
                                                         │ Aiven MCP (pg_read/write, kafka produce)
        ┌───────────────────────────┐                   ▼
        │ FRONTEND (Vercel)         │   WS/REST   ┌──────────────────────────────┐
        │ Meet overlay + activity   │◀──────────▶ │  gateway (ours, always-on)    │
        └───────────────────────────┘             │  + central-kg-api (teammate,  │
                                                   │    Lambda/Mangum — FE+seed)   │
                                                   └──────────────┬───────────────┘
                                                                  ▼  Aiven (Postgres+pgvector, Kafka, OpenSearch)
```

| | Owned here | Host | Integration seam |
|---|---|---|---|
| **web / data / git agents** | ✅ | **AWS Lambda** (Kafka event-source trigger) | consume `agent.tasks.*`, emit `agent.results` |
| **FE gateway** (thin bridge) | ✅ | **always-on** (small Fargate/EC2/Cloud Run) | REST + WS to the Vercel FE |
| **Listener (Agent A)** | ❌ teammate | EC2 | produces `agent.tasks.*`, consumes `agent.results` |
| **Call gateway** (Recall→Soniox→STT) | ❌ teammate | EC2 | produces `meeting.transcript` |
| **Knowledge graph** | ❌ teammate | `central-kg-api` on Lambda | **agents go via Aiven MCP, not its HTTP API** (§6) |
| **Message bus** | shared | Aiven Kafka | **Topics + envelope (§5)** |

**Inbound seam (to us):** `agent.tasks.{web,data,git}` — produced by the teammate's listener, one record per
delegated task. **Outbound seams (from us):** `agent.results` (task outputs) and `kg.updates` (facts to persist).
Those three topics + the envelope are the only contract that must stay stable across teams.

---

## 2. The Aiven MCP boundary (our 34% spine)

The challenge rewards agents that talk to data infra **natively via Aiven MCP** and penalises hand-written backend
code between an agent and a DB/queue. So **every data operation our agents perform is an Aiven MCP tool call.** The
real Aiven MCP data-plane tools (verified against [aiven.io/mcp](https://aiven.io/mcp) and
[Aiven-Open/mcp-aiven](https://github.com/Aiven-Open/mcp-aiven) — **not** the placeholder names in PLAN.md §3.5):

| Purpose | Aiven MCP tool |
|---|---|
| Read the graph (traversal CTEs, pgvector similarity — plain SQL) | `aiven_pg_read` |
| Write the graph (insert nodes/edges, facts) | `aiven_pg_write` |
| Publish a Kafka message (`agent.results`, `kg.updates`) | `aiven_kafka_topic_message_produce` |
| Consume Kafka (low-rate, ad-hoc) | `aiven_kafka_topic_message_list` |
| Provision Kafka / OpenSearch on camera (autonomy criterion) | control-plane: create/configure service |

**How a Lambda agent reaches these without a subprocess.** We do **not** use the Claude Agent SDK (it spawns the
Claude Code CLI as a subprocess — a poor Lambda fit). We use the **Anthropic Messages API remote MCP connector**
(`mcp-client-2025-11-20` beta): Anthropic connects to the Aiven **hosted** MCP server server-side and exposes its
tools to the model. The Lambda package stays pure-Python (`anthropic` + deps), no Node, no CLI, no local MCP
process. Sketch:

```python
client.beta.messages.create(
    model="claude-opus-4-8",
    betas=["mcp-client-2025-11-20"],
    mcp_servers=[{"type": "url", "name": "aiven",
                  "url": "https://mcp.aiven.live/mcp",          # Aiven hosted MCP
                  "authorization_token": AIVEN_MCP_TOKEN}],     # see §11 risk
    tools=[{"type": "mcp_toolset", "mcp_server_name": "aiven"}],
    messages=[...],   # the task
)
```

**Where the line sits (rate-based, settled).** All our seams are low-rate (a few messages per demo), so they all go
through MCP — *except* the one place Lambda forces an exception:

| Path | Transport | Why |
|---|---|---|
| KG reads/writes by agents | **Aiven MCP** `aiven_pg_read` / `aiven_pg_write` | The 34% showcase; raw SQL incl. recursive CTEs + pgvector |
| Emit `agent.results` / `kg.updates` | **Aiven MCP** `aiven_kafka_topic_message_produce` (model-driven) | Keeps agent↔agent pub/sub on MCP; no Kafka client in the Lambda |
| **Consume** `agent.tasks.*` (trigger) | **AWS Kafka event-source mapping** (managed poller) | A Lambda can't run a consumer loop — the ESM *is* the consumer. Unavoidable. |
| Bulk repo **seed** (thousands of upserts) | direct (teammate's `central-kg-api` seed CLI) | Per-row MCP round-trips too slow; PLAN.md §3.5 already carves this out |

**Honest caveat:** the inbound *trigger* is AWS-native, not MCP. The 34% criterion is about agents leveraging MCP
for **data operations** — every KG read/write and every result/fact we emit is MCP. That story holds. (Producing
the result via the MCP tool is model-driven; if it proves unreliable in the loop, the documented fallback is a
direct Kafka produce in the handler — accept one direct client at our own boundary rather than drop a result.)

---

## 3. Deployment topology

| Component | Host | Rationale |
|---|---|---|
| **web / data / git agents** | **AWS Lambda** | Per-task, stateless, bursty → serverless. Triggered by Aiven Kafka ESM. |
| **gateway** (FE bridge) | **always-on** small service (Fargate / a tiny EC2 / Cloud Run) | A WS server that continuously tails Kafka is *not* Lambda-shaped. The one piece that stays a process. (Alt: API Gateway WebSocket API + DynamoDB connection state — more moving parts; pick only if we want fully serverless.) |
| **central-kg-api** | **AWS Lambda** (Mangum) — *teammate's* | Already Lambda. FE bridge + ingestion/seed sidecar; not on the agent read path. |
| **Frontend** | **Vercel** | Meet overlay + agent-activity feed; calls the gateway over REST/WS. |
| **Listener + call-gateway** (transcription) | **EC2** — *teammate's* | Close to the live call, persistent Recall/Soniox connections. |
| **Aiven** (Postgres+pgvector, Kafka, OpenSearch) | **Aiven cloud** | Data layer. Agents reach it **only via Aiven MCP**. |

Cross-account glue we'll need (AWS CLI / Aiven MCP available; AWS auth to be supplied when we deploy):
- **Aiven Kafka → Lambda event-source mapping** for a *self-managed* (non-MSK) cluster: AWS runs a managed poller
  that invokes each worker with a batch of `agent.tasks.<x>` records. Auth is mTLS or SASL via AWS Secrets Manager
  (the Aiven `ca.pem`/SASL creds live there, not in the function).
- **Secrets**: `ANTHROPIC_API_KEY`, `AIVEN_MCP_TOKEN`, Vercel token (web-agent), Aiven Kafka creds → Secrets
  Manager / Lambda env. Never commit values.

---

## 4. Worker anatomy (the Lambda handler)

Every worker is the same shape: a `handler(event, context)` that the Kafka ESM invokes with a batch, runs one
Messages-API agent session per task with the Aiven MCP connector attached, and lets the model emit its result via
MCP.

```python
# agents/git-agent/handler.py  (web/data identical in shape)
import base64
from shared.contracts import Envelope, TaskCreatePayload

def handler(event, context):
    # Kafka ESM hands us records keyed by "topic-partition"; values are base64.
    for _tp, records in event["records"].items():
        for rec in records:
            env = Envelope[TaskCreatePayload].model_validate_json(
                base64.b64decode(rec["value"])
            )
            if already_done(env.payload.task_id):      # at-least-once → dedupe on task_id
                continue
            run_agent(env)                              # Messages API + Aiven MCP (§2)
```

Operational notes:
- **At-least-once delivery** — the ESM can redeliver; make the handler idempotent on `task_id`.
- **Timeout/memory** — a multi-turn agent loop (several Messages-API round-trips, each invoking MCP server-side)
  can run minutes. Set a generous Lambda timeout (up to 15 min) and enough memory; consider provisioned
  concurrency if demo cold-start latency bites.
- **No Kafka/PG client in the function** — the ESM provides the inbound records; every outbound data op is an MCP
  tool call. The deployment package is `anthropic` + `pydantic` + `shared`.

---

## 5. Seam A — Kafka topics & envelope (contract — unchanged by Lambda)

Topics (created once via an admin step — Aiven auto-create is OFF; can be an on-camera control-plane MCP moment):

| Topic | Key | Producer | Consumer |
|---|---|---|---|
| `meeting.transcript` | `meeting_id` | call-gateway *(teammate)* | listener, gateway |
| `meeting.events` | `meeting_id` | call-gateway *(teammate)* | listener, gateway |
| `agent.tasks.web` / `.data` / `.git` | `task_id` | listener *(teammate)* | **the matching worker (ours)** |
| `agent.results` | `task_id` | **workers (ours)** | listener, gateway |
| `kg.updates` | `node_key` | **workers (ours)**, listener | `central-kg-api` consumer |

Envelope (every message) — the single source of truth is `shared/contracts.py` (pydantic), unchanged by the
Lambda move (it describes *shape*, not *transport*):

```jsonc
{ "schema": "sunstead.v1", "id": "uuid", "type": "task.create", "meeting_id": "mtg_abc",
  "ts": "2026-06-25T10:00:00.123Z", "payload": { /* type-specific */ } }
```

**Changing a payload = PR to `shared/contracts.py` + a note here.** We *consume* `task.create` and *produce*
`task.completed`/`task.failed` + `kg.update` — those payload shapes are our half of the contract.

---

## 6. Seam B — Knowledge graph via Aiven MCP (not an HTTP client)

Our agents reach the graph with `aiven_pg_read` / `aiven_pg_write` against the same Aiven Postgres that backs
`central-kg-api`. **This replaces the old `kg_client.py` entirely** — its endpoints were guesses, and per the
MCP-native mandate agents must not call that HTTP API at all.

**The real schema** (from [CENTRAL-KG-API.md](CENTRAL-KG-API.md) — UUID ids, `(type, name)` natural key):

```sql
nodes  (id uuid, type, name, properties jsonb, embedding vector(1536), source_id, created_at, updated_at)
edges  (id uuid, source_node_id, target_node_id, type, properties jsonb, weight, source_id, created_at)
-- UNIQUE (type, lower(name));  edges UNIQUE (source_node_id, target_node_id, type)
```

Node types: `person, meeting, task, requirement, code_module, document, …` plus code-seeding adds `file, function,
class, commit`. Edge types: `depends_on, implements, relates_to, mentions, …` plus `DEFINES/IMPORTS/CALLS/
AUTHORED/TOUCHES` for code. SQL the agents issue via MCP:

```sql
-- 2-hop neighborhood of a node (recursive CTE)
WITH RECURSIVE nbr(id, depth) AS (
  SELECT id, 0 FROM nodes WHERE type='file' AND lower(name)=lower('src/auth.ts')
  UNION
  SELECT e.target_node_id, depth+1 FROM edges e JOIN nbr ON e.source_node_id = nbr.id WHERE depth < 2
) SELECT n.* FROM nodes n JOIN nbr ON n.id = nbr.id;

-- pgvector similarity ("what relates to X")
SELECT id, type, name FROM nodes
ORDER BY embedding <=> $query_embedding LIMIT 8;
```

**The demo data path.** Our example is a **codebase parsed into the graph**: the teammate's `central-kg-api` seed
CLI runs tree-sitter + `git log` over a target repo → `file/function/class/commit/person` nodes and
`DEFINES/IMPORTS/CALLS/AUTHORED/TOUCHES` edges. The **git-agent** then answers e.g. *"who last touched the auth
module"* with a single `aiven_pg_read` traversal (`person -AUTHORED-> commit -TOUCHES-> file`) — no git checkout
needed.

**`central-kg-api` is FE/seed only now** (ingestion sidecar + FE bridge). Its real surface, for the FE/gateway —
**not for agents**: `GET /query?q=`, `/subgraph`, `/entity/{id}`, `/timeline`; `POST /ingest|/extract|/node|/link|
/update`. OpenSearch quicksearch and the `kg.updates` consumer are still gaps on that service (see its README).

---

## 7. The worker agents

Common shape (§4): Kafka-ESM-triggered Lambda → Messages API + Aiven MCP → emit result via MCP. Build **git first**
(simplest, pure read), then web (best demo), then data.

- **git-agent** — intents `read_git`, `blame`, `who_changed`, `recent_changes`. Answers from the graph via
  `aiven_pg_read` (commits/people/files are seeded). **First milestone — see §10.**
- **web-agent** — intents `build_website`, `update_website`. Codegen + `vercel_deploy` (Vercel API/CLI from the
  Lambda); reads brand/context via `aiven_pg_read`. Returns a live URL → great demo.
- **data-agent** — intents `analyze`, `summarize_metrics`, `query_data`. Python/pandas; may pull rows via
  `aiven_pg_read`. Returns an answer + chart artifact.

Models: Opus 4.8 for hard reasoning, Sonnet for codegen, Haiku for simple retrieval.

---

## 8. Gateway — FE bridge (always-on)

So the Vercel FE has one clean, auth'd connection and never touches Kafka or MCP creds.
- `POST` commands (ask, approve task) → produce to Kafka.
- `WS /stream?meeting_id=` → tail `meeting.transcript` + `agent.results` + activity → browser.
- Hosted as a small always-on service (it holds long-lived WS connections + a continuous Kafka tail — the one
  component that shouldn't be Lambda). FE on Vercel builds the overlay on top.

---

## 9. Local dev & deploy

- **Run a worker locally** with a fake Kafka-ESM event (`python -m git_agent.handler` against a hand-built
  `event` dict) — no AWS needed to iterate on the agent loop.
- **Deploy a worker**: package (`anthropic` + `pydantic` + `shared`) → Lambda; create the **Aiven Kafka
  event-source mapping** (SASL/mTLS creds in Secrets Manager); set `ANTHROPIC_API_KEY` + `AIVEN_MCP_TOKEN`.
- **Aiven MCP** is available to us now; **AWS CLI** is available (request AWS auth when we start deploying /
  wiring the ESM). Secrets via env / Secrets Manager — never read secret *values*, only check key presence.

---

## 10. Build order

1. **Connectivity spike** *(de-risks everything)* — ~20 lines: Messages API + Aiven MCP connector → one
   `aiven_pg_read("select count(*) from nodes")` + one `aiven_kafka_topic_message_produce`. **Also answers the §11
   auth risk.**
2. **git-agent** — local handler: hardcoded `agent.tasks.git` payload → `aiven_pg_read` traversal against the live
   `central-kg-pg` → emit to `agent.results` via MCP. *First runnable, MCP-native, demo-bankable milestone.*
3. **Wire the Aiven Kafka → Lambda ESM** so the teammate's listener can trigger git-agent for real.
4. **web-agent** (deploys a real Vercel URL) then **data-agent** — clone the shape.
5. **gateway** WS bridge so the Vercel FE sees the live feed.

Each item is a short branch off `feat/agent-system`; `shared/contracts` changes get extra eyes.

---

## 11. Open questions & risks

- **★ Aiven hosted MCP auth (the one to verify first).** `https://mcp.aiven.live/mcp` documents OAuth 2.0 PKCE
  (browser) auth, but the Messages-API connector and a headless Lambda need a **static bearer**
  (`authorization_token`). *If* Aiven issues a PAT/bearer the hosted server accepts → §2 path as written. *If not*
  → **self-host the Aiven MCP** (`npx mcp-aiven` with `AIVEN_TOKEN`) as a small always-on URL beside the gateway,
  and point the connector there. The connectivity spike (§10.1) settles this before any worker structure is built.
- **Result emission reliability** — model-driven `aiven_kafka_topic_message_produce` is the all-MCP path; if the
  model skips it, fall back to a direct Kafka produce in the handler (one direct client at our own boundary).
- **OpenSearch via MCP unverified** — documented Aiven MCP data tools are Postgres + Kafka; treat OpenSearch-through
  -MCP as a stretch. pgvector is the primary retrieval path (consistent with HACKINFO "OpenSearch not guaranteed").
- **Gateway hosting** — always-on small service vs API Gateway WebSocket + DynamoDB. Default to the former (less
  moving parts); revisit only if we want fully serverless.
- **`central-kg-api` gaps** — its seed CLI (tree-sitter + git → graph), OpenSearch mirror, and `kg.updates`
  consumer are still unbuilt (teammate's); our git-agent demo depends on the **seed** being run.

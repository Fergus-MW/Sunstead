# Sunstead — System Plan

> A real-time **video-call agent**: it joins a live meeting, transcribes it, understands it against a
> **knowledge graph**, and dispatches work to a **suite of specialist agents** over a Kafka bus.
> Built on the **Claude Agent SDK (Python)** with **Aiven** (Kafka + PostgreSQL + OpenSearch) as the data layer.

Status: **planning** — this document is the contract we execute against. Nothing here is built yet.
All technology claims below are grounded in current docs (June 2026); see [§14 Sources](#14-sources).

---

## 1. Vision & one-line description

**"A low-latency, knowledge-graph-backed employee that sits in your video calls."**

Top-level flow (from the whiteboard):

1. **Agent A (the Listener)** joins an online meeting and transcribes live; pushes the transcript to a message stream.
2. The **Agent Ecosystem** picks up delegated tasks (build a website, analyse data, read git, …).
3. Everything is grounded in a **central knowledge graph** seeded from a repo and enriched live from the call.

Manifested use case for the demo: **team standup / engineering team assistant** — ask the agent things mid-call,
have it fact-check, pull context from the codebase graph, and spin up real work (a deployed site, a data answer,
a git lookup) without anyone leaving the call.

---

## 2. Architecture at a glance

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                      FRONTEND (FE team)                   │
                        │  Google Meet wrapped + draw-on-top overlay                │
                        │  • live transcript   • agent activity feed   • ask box    │
                        └───────────────▲───────────────────────┬──────────────────┘
                                        │ WebSocket (events)     │ HTTP (commands)
                                        │                        ▼
                        ┌──────────────────────────────────────────────────────────┐
                        │              API GATEWAY  (FastAPI, Python)               │
                        │  • REST for FE commands   • WS bridge Kafka→FE            │
                        └───────▲───────────────────────────────┬──────────────────┘
                                │                               │
        Recall.ai bot          │ produce/consume (Kafka)        │ produce/consume
   ┌─────────────────┐         │                               │
   │  Google Meet    │   ┌─────┴───────────┐         ┌──────────┴───────────────────┐
   │  (real call)    │──▶│  CALL GATEWAY    │         │        AIVEN KAFKA            │
   └─────────────────┘   │  Recall→Soniox   │────────▶│  meeting.transcript          │
        raw S16LE        │  STT → Kafka     │         │  meeting.events              │
        16k mono         │  (+ TTS out)     │◀────────│  agent.tasks.*  agent.results│
                         └──────────────────┘         │  agent.reasoning  kg.updates │
                                                       └───────▲──────────┬───────────┘
                                ┌──────────────────────────────┘          │
                                │ consumes transcript                     │ tasks / results
                       ┌────────┴─────────┐                    ┌──────────┴───────────────┐
                       │ LISTENER AGENT   │  emits task intents │   AGENT SUITE (workers)  │
                       │ "Agent A" brain  │───────────────────▶│  • web-agent  (Vercel)   │
                       │ live parse +     │◀───────────────────│  • data-agent (analysis) │
                       │ decide + speak   │   results           │  • git-agent  (repo)     │
                       └────────┬─────────┘                    └──────────┬───────────────┘
                                │ query / write                            │ query
                                ▼                                          ▼
                       ┌──────────────────────────────────────────────────────────┐
                       │            CENTRAL KG API (FastAPI, Python)               │
                       │  nodes/edges + pgvector  +  OpenSearch quicksearch        │
                       │  seeded from repo via tree-sitter + git                   │
                       └───────────────────────────┬──────────────────────────────┘
                                                    ▼
                       ┌──────────────────────────────────────────────────────────┐
                       │   AIVEN PostgreSQL (graph + vectors)  +  AIVEN OpenSearch │
                       └──────────────────────────────────────────────────────────┘
```

### Components

| Component | Folder | Role | Owner (suggested) |
|---|---|---|---|
| **Call Gateway** | `call-gateway/` | Recall.ai bot → Soniox STT → Kafka transcript; TTS out | Person 1 |
| **Listener Agent (A)** | `listener-agent/` | The brain near the call: live parse, decide, delegate, speak | Person 1 |
| **Central KG API** | `central-kg-api/` *(exists)* | Graph + vector + search over Aiven PG/OpenSearch; repo seeding | Person 2 |
| **Agent suite** | `agents/{web,data,git}-agent/` | Specialist workers consuming Kafka tasks | Person 3 |
| **API Gateway** | `api-gateway/` | FE-facing REST + WS bridge to Kafka | Person 3 (shared) |
| **Shared lib** | `shared/` | Kafka client, message schemas, config, KG client | All |
| **Infra** | `infra/` | Aiven provisioning + Kafka topic admin | All |
| **Frontend** | `frontend/` | Meet wrapper + overlay | FE team |

---

## 3. Key technology decisions (research-grounded)

These are settled by the research pass — call them out so we don't relitigate:

1. **Meeting join = Recall.ai, not iframe.** `meet.google.com` sends `X-Frame-Options: DENY` + CSP `frame-ancestors`,
   so you **cannot** embed Meet in an iframe server-side. Recall.ai sends a real bot into Meet/Zoom/Teams and streams
   **raw audio per participant as base64 S16LE, 16 kHz, mono** — which is byte-for-byte what Soniox wants. The FE's
   "draw on top" overlay is still useful as a *local* assistant surface, but the **audio capture must go through Recall**.
   *(A mock/file-based audio source is our dev fallback so we don't burn Recall minutes — see phases.)*

2. **STT = Soniox real-time** (`wss://stt-rt.soniox.com/transcribe-websocket`, model `stt-rt-v5`). Config message →
   binary PCM frames → token stream with `is_final`, word-level `start_ms`/`end_ms`, and per-token `speaker`/`language`.
   Because Recall already labels audio **per participant**, we get authoritative speaker identity *without* paying for
   diarization — one Soniox stream per active participant (or one mixed stream).

3. **Graph DB: Apache AGE is NOT available on Aiven.** Aiven's supported-extension allowlist excludes the `age` C
   extension (it's untrusted/superuser-only). **No openCypher on Aiven.** → We model the property graph with **plain
   `nodes` + `edges` tables, traverse with recursive CTEs, and use pgvector for semantics** — all supported on Aiven.
   This is the single most important constraint in the whole plan; design around it from day one. ("Graphify on Postgres"
   from the notes refers to `safishamsi/graphify`, a tree-sitter repo→graph extractor — useful prior art for seeding,
   not a Postgres extension.)

4. **pgvector IS supported on Aiven** → embeddings live in the same Postgres as the graph (hybrid graph+semantic queries).

5. **Kafka on Aiven defaults to mTLS** (three files: `ca.pem`, `service.cert`, `service.key`). SASL is opt-in.
   **Auto-topic-create is OFF** — we create topics explicitly via an admin script (good, we want controlled partitions).

6. **Claude Agent SDK multiagent is first-class** — a coordinator agent can delegate to subagents in-process. We will
   *still* prefer **Kafka-based delegation** between our top-level agents (see §4) for decoupling and to use the bus we're
   sponsored on; the in-process coordinator is reserved for a worker that needs tight sub-task fan-out.

7. **Language = Python everywhere.** Claude Agent SDK Python is mature; Soniox/Recall/Kafka all have solid Python async
   clients; tree-sitter seeding and any data analysis are most natural in Python. The "web-agent deploys to Vercel" job is
   language-agnostic (it shells out / calls Vercel's API), so Python costs us nothing there.

---

## 3.5 Aiven MCP-native architecture (challenge alignment) ★

> The Aiven challenge explicitly rewards **MCP-native agent ↔ data-infra interaction** and explicitly penalises building
> "thousands of lines of backend boilerplate" between an agent and a database/queue. Our current `central-kg-api` HTTP
> layer is exactly that anti-pattern if the *agents* call it. This section is the realignment that lets us win the
> Aiven category without throwing the existing work away. **Treat this as binding** — every subsequent section that
> says "agent calls KG API" should be read as "agent calls Aiven MCP, the FastAPI is for the FE only."

> **Verified tool names** (this section originally used placeholders). The real Aiven MCP data-plane tools, confirmed
> against [aiven.io/mcp](https://aiven.io/mcp) + [Aiven-Open/mcp-aiven](https://github.com/Aiven-Open/mcp-aiven):
> `aiven_pg_read` / `aiven_pg_write` (SQL incl. recursive CTEs + pgvector), `aiven_kafka_topic_message_produce` /
> `aiven_kafka_topic_message_list`. OpenSearch data-plane tools are **unverified** — treat OpenSearch-via-MCP as a
> stretch (pgvector is the primary retrieval path). Agents reach these via the **Anthropic Messages API remote MCP
> connector** (no Agent-SDK subprocess). See [AGENT_SYSTEM.md](AGENT_SYSTEM.md) §2 for the agent-suite specifics.

**What needs to change to actually attack the challenge well:**

1. **Re-route the agent → KG path through Aiven MCP, not our HTTP API.** Listener and workers issue `aiven_pg_read` /
   `aiven_pg_write` MCP calls for: *"fetch the 2-hop neighborhood of `meeting:standup-2026-06-25`"*, *"insert
   `mentions` edge between utterance N and function X"*, *"pgvector similarity over `nodes.embedding`"*. Recursive CTEs
   run as plain SQL through the MCP — no FastAPI hop. Our Lambda becomes the *write enrichment* path (Claude
   extraction → upsert), not the read path.

2. **Kafka via Aiven MCP for the agent suite.** Listener publishes to `agent.tasks.*`; workers produce results back on
   `agent.results` via `aiven_kafka_topic_message_produce`. *Consumption* depends on the host: an EC2/long-running
   worker can poll `aiven_kafka_topic_message_list`, but our **Lambda** workers are triggered by an AWS Kafka
   event-source mapping (the managed poller *is* the consumer — see [AGENT_SYSTEM.md](AGENT_SYSTEM.md) §2/§4). Either
   way, every data *operation* stays on MCP.

3. **Provision the remaining Aiven services via MCP, on camera / in commits.** Kafka cluster + OpenSearch via Aiven
   MCP tool calls during the build, not `avn` CLI. That's the visible evidence judges will look for. The Postgres
   service is already up (provisioned via `avn` before this realignment) — leave it; do every *next* service via MCP
   and document the tool calls in the commit message.

4. **Reframe `central-kg-api`'s purpose.** Keep it as:
   1. **Claude extraction worker** that turns transcripts into graph upserts (called from the Listener over Kafka, or
      invoked directly during seeding — *not* hit by other agents for context reads).
   2. **The `seed` CLI** from §9.3 (tree-sitter + git → bulk upsert), where per-row MCP roundtrips would be too slow.
   3. **A thin HTTP wrapper for the FE only** — the browser cannot speak MCP, so the gateway translates `HTTP / WS`
      into the same MCP tool calls the agents use. This is *also* where demo-data seeding endpoints live (`POST
      /demo/seed`) so the demo can be re-armed in one click without leaving the UI.

   Demote it from "central context API the agents call" to **"ingestion sidecar + FE bridge."**

### What this means for OpenSearch ↔ knowledge graph wiring

OpenSearch is a separate Aiven service; nothing auto-syncs from Postgres. The connection is **a shared id**:
`opensearch._id == nodes.id (UUID)` and `opensearch._id == sources.id (UUID)`. The chosen approach is **dual-write at
upsert time, via MCP**:

- When the Listener (or the seed CLI, or any worker) upserts a node/source, it issues *two* MCP calls in the same
  turn: `aiven_pg_write("INSERT INTO nodes ...")` **and** an OpenSearch index call (tool name *unverified* — see the
  note at the top of this section; if Aiven MCP exposes no OpenSearch data-plane tool, the seed CLI handles the
  OpenSearch write and live agents skip it). No connector code, no Debezium, no Python "syncer" service.
- `quicksearch(q)` is then a single OpenSearch search call (again, *if* exposed via MCP) → returns ids → the agent
  follows up with an `aiven_pg_read` for the 1- or 2-hop neighborhood of those ids. The KG provides *structure*,
  OpenSearch provides *full-text recall*. Same primary keys mean we never need a join table.
- The seed CLI is the one place this *does* need code, because we're upserting thousands of nodes and per-row
  MCP roundtrips would be slow. The seed CLI does the dual-write itself (bulk `psql COPY` + OpenSearch `_bulk`).
  Everything else (live meeting writes, ad-hoc upserts from agents) goes through MCP.

**Net: no dedicated "OpenSearch connector" code is needed.** OpenSearch ↔ KG is held together by id equality and
discipline at write time, both of which we get for free as long as every writer goes through MCP.

### What this means for the FE / demo

We still need a browser-facing layer (the Meet wrapper + overlay can't speak MCP, and the agents shouldn't expose
Kafka credentials to the browser). That's exactly what `central-kg-api` becomes:

- **`GET /demo/*`** — demo-data endpoints the FE uses to bootstrap a clean state (`POST /demo/seed` re-runs a
  canned ingest; `POST /demo/reset` wipes the graph). Cheap to add, lets us re-arm the demo without leaving the UI.
- **`POST /ingest`** — kept; this is what the FE calls when a user pastes a doc or kicks off the seed.
- **WebSocket bridge** — the FE subscribes here; the gateway tails `meeting.transcript` / `agent.results` over the
  Aiven Kafka MCP and pushes events into the WS so the browser sees the live feed.
- **No `/query`, `/entity`, `/subgraph` traffic from agents.** Those routes can stay for the FE's "explore the graph"
  view, but they should NOT be the canonical path for any agent — agents go through MCP.

So: the FastAPI service shrinks in *scope* (agents stop calling it) but grows in *value* (it's the demo-experience
surface). That's the right framing for the judges: **"our agents talk to data via MCP; our humans talk to agents via
this FastAPI."**

---

## 3.6 Deployment topology (where each piece runs)

The system is **multi-host by design** — serverless where work is per-task and bursty, always-on where a process
must hold open connections. (Agent-suite specifics: [AGENT_SYSTEM.md](AGENT_SYSTEM.md) §3.)

| Component | Host | Why |
|---|---|---|
| **Worker agents** (web / data / git) | **AWS Lambda** | Per-task, stateless, bursty. Triggered by an **Aiven Kafka event-source mapping** (AWS-managed poller invokes the function with a batch). Each runs the **Anthropic Messages API + remote Aiven MCP connector** — no Agent-SDK/CLI subprocess, so the package stays pure-Python. |
| **API / FE gateway** | **always-on** (small Fargate / EC2 / Cloud Run) | Holds long-lived WebSockets + a continuous Kafka tail → not Lambda-shaped. (Alt: API Gateway WebSocket API + DynamoDB.) |
| **central-kg-api** | **AWS Lambda** (Mangum) | Already serverless. Ingestion sidecar + FE bridge; off the agent read path. |
| **Frontend** | **Vercel** | Meet overlay + agent-activity feed; talks only to the gateway. |
| **Listener (A) + Call gateway** | **EC2** | Close to the live call; persistent Recall/Soniox connections. |
| **Aiven** (Postgres+pgvector, Kafka, OpenSearch) | **Aiven cloud** | Data layer. Reached **only via Aiven MCP** from agents. |

**The one MCP-vs-direct exception, made explicit:** a Lambda can't run a Kafka consumer loop, so worker *ingest* is
the AWS-native event-source mapping, not an MCP `aiven_kafka_topic_message_list` call. Every data *operation* an
agent performs (KG read/write, result/fact emit) still goes through Aiven MCP — which is what the 34% MCP-depth
criterion measures. **Open risk:** the Aiven *hosted* MCP (`mcp.aiven.live`) documents OAuth-PKCE auth; headless
Lambda needs a static bearer for the Messages-API connector. If unavailable, self-host `npx mcp-aiven` beside the
gateway. Verify first — see [AGENT_SYSTEM.md](AGENT_SYSTEM.md) §11.

---

## 4. Architectural recommendation — "FE calls agent BE; or a better way?"

Your instinct (FE → agent backend) is right. The refinement that makes it scale to *N agents + barge-in + a reviewer
agent* later is to **split the FE boundary from the inter-agent boundary**:

- **FE ↔ backend = one API Gateway (FastAPI).** The FE never touches Kafka or the agents directly. It does:
  - `HTTP POST` for commands ("start bot on this meeting", "ask: …", "approve task X").
  - `WebSocket` subscription for the live feed (transcript tokens, agent activity, results). The gateway **bridges Kafka
    → WS** so the browser gets a clean, auth'd, single connection. This keeps the "live assistant / draw on top" UX snappy
    and keeps Kafka credentials server-side.

- **Backend ↔ backend = Kafka (Aiven).** The Call Gateway, Listener, and every agent communicate **only** via Kafka
  topics with versioned JSON messages. Benefits that matter for this project:
  - **Team decoupling** — 3 people own different services that share *contracts*, not code paths.
  - **Each agent is an independent consumer group** — scale/deploy/restart in isolation.
  - **The "reasoning stream / agent-on-agent barge-in / reviewer agent" future is just another topic** (`agent.reasoning`)
    — no re-architecture needed. (Keep this as a *stretch* topic; don't build barge-in for the MVP.)

- **Where does the Claude Agent SDK coordinator fit?** Use it **inside** an agent that genuinely needs to fan out
  sub-tasks (e.g. the web-agent splitting "build + test + deploy" across subagent threads). Do **not** make the Listener a
  giant in-process coordinator — see §7 on keeping the Listener lightweight.

**Net:** FE → API Gateway (REST + WS). Everything behind it is Kafka. One mental model, room to grow.

---

## 5. Kafka topic design (the "kit")

All topics keyed by `meeting_id` (or `task_id`) so per-meeting ordering is preserved within a partition.
Start with **3 partitions / RF 3 / min.insync.replicas 2** (Aiven default RF is 3). JSON values for the MVP;
upgrade path is Avro + Karapace schema registry (Aiven-hosted) if we want enforced schemas.

| Topic | Key | Produced by | Consumed by | Purpose |
|---|---|---|---|---|
| `meeting.transcript` | `meeting_id` | Call Gateway | Listener, API Gateway | Live STT tokens (partial + final) |
| `meeting.events` | `meeting_id` | Call Gateway | Listener, API Gateway | Join/leave, mute, bot lifecycle |
| `agent.tasks.web` | `task_id` | Listener / Orchestrator | web-agent | Build/deploy website intents |
| `agent.tasks.data` | `task_id` | Listener / Orchestrator | data-agent | Data-analysis intents |
| `agent.tasks.git` | `task_id` | Listener / Orchestrator | git-agent | Repo/git lookups |
| `agent.results` | `task_id` | All agents | Listener, API Gateway | Task outputs / status |
| `agent.reasoning` | `task_id` | All agents *(stretch)* | reviewer, FE | Streamed reasoning tokens (barge-in/review) |
| `kg.updates` | `node_key` | Listener, agents | KG indexer | Async "write this fact to the graph" events |

**Per-agent task topics** (vs one `agent.tasks` with a `target` field) chosen so each teammate owns a topic and a consumer
group cleanly, and so we can set per-agent partition counts independently.

Admin script (`infra/kafka_admin.py`) creates all topics idempotently on boot (because auto-create is off on Aiven).

---

## 6. Message contracts

One envelope for everything; `type`-specific `payload`. Versioned so we can evolve without breaking consumers.

```jsonc
// Envelope (all topics)
{
  "schema": "sunstead.v1",
  "id": "uuid",
  "type": "transcript.final",          // see per-topic types below
  "meeting_id": "mtg_abc",
  "ts": "2026-06-25T10:00:00.123Z",    // producer clock, ISO-8601 (pass in, never Date.now() in workflows)
  "payload": { /* type-specific */ }
}
```

```jsonc
// meeting.transcript  →  type: "transcript.partial" | "transcript.final"
"payload": {
  "speaker": { "id": 123, "name": "Alice" },   // from Recall participant
  "text": "can we deploy the landing page",
  "start_ms": 12340, "end_ms": 13980,
  "is_final": true, "confidence": 0.94, "language": "en"
}
```

```jsonc
// agent.tasks.*  →  type: "task.create"
"payload": {
  "task_id": "tsk_001",
  "intent": "build_website",                    // controlled vocab per agent
  "args": { "brief": "one-page landing for Sunstead", "style": "dark" },
  "context_refs": ["node:file:src/app.ts", "kg:query:..."],  // pointers into KG, not blobs
  "requested_by": "listener",
  "reply_to": "agent.results"
}
```

```jsonc
// agent.results  →  type: "task.progress" | "task.completed" | "task.failed"
"payload": {
  "task_id": "tsk_001",
  "status": "completed",
  "result": { "url": "https://sunstead-xyz.vercel.app", "summary": "Deployed." },
  "artifacts": [{ "kind": "url", "value": "https://..." }],
  "error": null
}
```

Contracts live in `shared/contracts/` as Pydantic models + JSON Schema, imported by every service.

---

## 7. The Listener Agent (Agent A) — detailed scope ★

This is the heart of the system. The central design tension from your notes:
*"it's nice to have something very close to the call with lots of context, but maybe offload to the graph so it doesn't
hold everything — maybe it's just live parsing."* **Recommendation: keep the Listener lightweight and stateless-ish; the
graph is the memory.** The Listener holds only a short rolling window; durable context lives in the KG.

### 7.1 Responsibilities
1. **Consume** `meeting.transcript` (final tokens; partials only for UI hints).
2. **Maintain a rolling window** — last ~N utterances / ~3 minutes in memory. Periodically **summarize → `kg.updates`** so
   long-term context is in Postgres, not RAM.
3. **Detect actionable moments** — questions to the agent, explicit tasks ("build us a landing page"), factual claims to
   check, references to code/people/data that the KG can resolve.
4. **Decide & delegate** — turn an actionable moment into a `task.create` on the right `agent.tasks.*` topic.
5. **Ground answers in the KG** — query nodes/edges + vector + quicksearch before answering or delegating.
6. **Speak** — when a response is warranted, synthesize TTS and play it back **into the meeting via Recall's output-audio**
   (Recall bots can emit audio), and mirror text to the FE feed.

### 7.2 What it is NOT
- Not a monolithic orchestrator that runs all sub-work in-process.
- Not the long-term memory store (that's the KG).
- Not the transcription engine (that's Call Gateway + Soniox).

### 7.3 Internal design (two-tier: cheap continuous loop + expensive decision)
```
transcript.final ──▶ [Tier 1: continuous classifier]  (Haiku / Sonnet, cheap, every finalized utterance)
                         │  is this actionable? (question | task | claim | reference | none)
                         │  none ─▶ append to window, maybe summarize
                         ▼  actionable
                     [Tier 2: decision]  (Opus, only when triggered)
                         │  query KG (graph + vector + search) for grounding
                         │  choose: answer-inline | delegate(task) | fact-check | clarify
                         ▼
        emit task.create  ──or──  speak(TTS→Recall)  ──or──  write fact to kg.updates
```
- **Tier 1** keeps cost/latency low — a small structured-output classifier (`strict: true` JSON) runs on every final
  utterance. Most utterances are `none`.
- **Tier 2** only fires on actionable moments, uses **Opus 4.8** + KG tools, and may delegate or answer.

### 7.4 Tools (Claude Agent SDK custom tools)
| Tool | Calls | Purpose |
|---|---|---|
| `kg_query(cypher_like \| sql)` | Central KG API | Graph traversal / lookups |
| `kg_semantic_search(text, k)` | Central KG API (pgvector) | "What in the codebase relates to X?" |
| `quicksearch(text)` | Central KG API (OpenSearch) | Fast full-text over docs/code |
| `emit_task(agent, intent, args, context_refs)` | Kafka `agent.tasks.*` | Delegate work |
| `speak(text)` | TTS → Recall output audio | Talk back into the call |
| `write_fact(node, edges)` | Kafka `kg.updates` | Persist a new fact/claim |

### 7.5 Live fact-checking (the "Cluely" note)
When Tier 1 tags a **claim**, Tier 2 runs a grounded check: `kg_semantic_search` + `quicksearch` (+ optional web later) →
verdict (`supported | contradicted | unverified`) with sources → push to FE feed (and optionally `speak` only if high
confidence + clearly wrong, to avoid being annoying).

### 7.6 TTS choice
Soniox is STT-only. For voice-out use **ElevenLabs / Cartesia / OpenAI TTS** → PCM → **Recall "output audio in meeting"**.
For the MVP, text-to-FE is enough; voice-out is a clearly-scoped add-on once Recall output is wired.

### 7.7 Open question to resolve in build
Is there a **separate Orchestrator**, or is the Listener's Tier-2 the orchestrator? **Recommendation for MVP:** Listener
Tier-2 emits tasks directly (no separate orchestrator). Introduce a dedicated Orchestrator only if/when we add the
reviewer agent and `agent.reasoning` barge-in (stretch).

---

## 8. The Agent Suite (workers)

Common shape: each worker consumes its `agent.tasks.<x>` topic, runs **one Anthropic Messages-API session with the
Aiven MCP connector** per task, and publishes to `agent.results` via MCP. Stateless and horizontally scalable. On our
chosen hosting these are **AWS Lambda** functions triggered by an Aiven Kafka event-source mapping — see
[AGENT_SYSTEM.md](AGENT_SYSTEM.md) §2–§4 and §3.6 above. (We deliberately avoid the Agent-SDK CLI-subprocess model on
Lambda; the listener on EC2 may still use it.)

### 8.1 Agent B — Web Agent → Vercel
- **Intent vocab:** `build_website`, `update_website`.
- **Tools:** filesystem/codegen (SDK `agent_toolset`), `vercel_deploy` (Vercel API / CLI), `kg_query` for brand/context.
- **LLM routing:** can route its model calls through **Vercel AI Gateway** (`anthropic/claude-*`, one key, observability,
  fallback) — natural fit given it already deploys to Vercel.
- **Output:** deployed URL → `agent.results` → Listener announces it / FE shows it.
- **Model:** Opus for plan, Sonnet for codegen (optionally in-process coordinator/subagents).

### 8.2 Agent C — Data Agent
- **Intent vocab:** `analyze`, `summarize_metrics`, `query_data`.
- **Tools:** `bash`/python execution (pandas/numpy/matplotlib), `kg_query`, optional DB connectors.
- **Output:** answer + chart artifact (image URL) → `agent.results`.
- **Model:** Sonnet default, Opus for hard reasoning.

### 8.3 Agent D — Git Agent
- **Intent vocab:** `read_git`, `blame`, `who_changed`, `recent_changes`.
- **Tools:** `bash` (git), `kg_query` (the graph already encodes commits/people/files from seeding — often answer from KG
  without touching git).
- **Output:** structured answer → `agent.results`.
- **Model:** Haiku/Sonnet (mostly retrieval).

### 8.4 Result lifecycle
`task.create` → worker emits `task.progress`* → `task.completed`/`task.failed`. Listener consumes results, may `speak`,
API Gateway forwards to FE. `reply_to` lets us re-route later.

---

## 9. Knowledge Graph design

### 9.1 Schema (Aiven Postgres — portable property graph)
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE nodes (
  id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind      TEXT NOT NULL,                  -- file|function|class|module|commit|person|claim|meeting|utterance
  key       TEXT NOT NULL,                  -- stable natural key, e.g. 'src/auth.ts#login'
  props     JSONB NOT NULL DEFAULT '{}',
  embedding vector(1536),                   -- nullable; for semantic search
  UNIQUE (kind, key)
);

CREATE TABLE edges (
  id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  src_id BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  dst_id BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  rel    TEXT NOT NULL,                     -- DEFINES|IMPORTS|CALLS|AUTHORED|TOUCHES|MENTIONS|ABOUT
  props  JSONB NOT NULL DEFAULT '{}',
  UNIQUE (src_id, dst_id, rel)
);

CREATE INDEX edges_src_rel    ON edges (src_id, rel);
CREATE INDEX edges_dst_rel    ON edges (dst_id, rel);
CREATE INDEX nodes_kind       ON nodes (kind);
CREATE INDEX nodes_props_gin  ON nodes USING gin (props);
CREATE INDEX nodes_embed_hnsw ON nodes USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
```
- **Code entities** (seeded): `file`, `function`, `class`, `module`, `commit`, `person` with `DEFINES/IMPORTS/CALLS/AUTHORED/TOUCHES`.
- **Live meeting entities** (written by Listener): `meeting`, `utterance`, `claim`, plus `MENTIONS`/`ABOUT` edges linking
  what was said to the code/people it references. This is what makes the call *grounded*.
- Traverse with **recursive CTEs** (call-graph reachability, import closure, ownership). Cap depth / track visited array to
  avoid cycles. (Example queries in the research appendix; we'll lift them into `central-kg-api`.)

### 9.2 OpenSearch (quicksearch)
Mirror searchable text (file contents, docstrings, utterances) into an Aiven OpenSearch index for fast `multi_match`
full-text — the "quicksearch" box. Use `avnadmin` to dodge the read-only `_msearch` 500 in demos.

### 9.3 Seeding from a repo
Pipeline in `central-kg-api` (`seed` CLI):
1. **tree-sitter** parse → files, functions, classes, imports; name-based call resolution → `CALLS` (optional LSP later).
2. **git log** → commits + people + `AUTHORED`/`TOUCHES`.
3. **Bulk upsert** `INSERT … ON CONFLICT (kind,key) DO UPDATE` into `nodes`/`edges`.
4. **Embed** function/file text → `embedding`; **mirror** text → OpenSearch.
- Alternative/accelerator: evaluate `safishamsi/graphify` as an off-the-shelf extractor and write its output into our
  schema. Decide during Phase 2; don't block on it.

### 9.4 Central KG API surface (FastAPI)
`POST /query` (graph/SQL), `POST /semantic_search`, `POST /quicksearch`, `POST /upsert` (nodes/edges), `POST /seed`
(kick off seeding), plus a `kg.updates` consumer that applies async writes. This is the existing `central-kg-api` service —
we flesh it out here.

---

## 10. Repository structure & branch strategy

Monorepo, Python, **uv workspace** (matches existing `central-kg-api/pyproject.toml`). Each service is its own package;
`shared/` is a path dependency.

```
Sunstead/
├─ central-kg-api/      # EXISTS — KG service + seeding (Person 2)
├─ call-gateway/        # Recall + Soniox + Kafka producer + TTS out (Person 1)
├─ listener-agent/      # Agent A brain (Person 1)
├─ agents/
│  ├─ web-agent/        # Agent B → Vercel (Person 3)
│  ├─ data-agent/       # Agent C (Person 3)
│  └─ git-agent/        # Agent D (Person 3)
├─ api-gateway/         # FE-facing REST + Kafka→WS bridge (Person 3)
├─ shared/              # kafka client, contracts (pydantic), kg client, config
├─ infra/               # aiven provisioning (avn/terraform), kafka_admin.py, schema.sql
├─ frontend/            # FE team (Meet wrapper + overlay)
├─ docs/PLAN.md         # this file
└─ pyproject.toml       # uv workspace root
```

**Branching for 3 people in one repo:** you asked for "a branch and a folder so we can cleanly merge later." Since each
service is its own folder, conflicts are naturally minimal. Recommended:
- This plan lands on branch **`plan/agent-architecture`** (current).
- Each person works on a service folder via short-lived feature branches (`feat/call-gateway`, `feat/web-agent`, …) and
  merges to `main` through PRs. The `shared/contracts` package is the coordination point — change it via PR so everyone
  sees schema changes.

---

## 11. Aiven provisioning plan

Fastest path: **$300 / 30-day free trial (no card)** or the forever-free tier (one service per type). Provision ~2–5 min
each. Script it (`infra/`):

```bash
pip install aiven-client
avn user login jchennq@gmail.com --token
avn service create sunstead-kafka --service-type kafka      --cloud google-europe-west3 --plan business-4 \
    -c kafka_authentication_methods.sasl=true -c kafka.auto_create_topics_enable=false
avn service create sunstead-pg    --service-type pg         --cloud google-europe-west3 --plan startup-4
avn service create sunstead-os    --service-type opensearch --cloud google-europe-west3 --plan startup-4
avn service wait sunstead-kafka && avn service wait sunstead-pg && avn service wait sunstead-os
# then: download ca.pem/service.cert/service.key for Kafka; grab service URIs for PG/OS
python infra/kafka_admin.py   # create topics from §5 idempotently
psql "$PG_URI" -f infra/schema.sql
```
- **Kafka:** enable SASL (SCRAM-SHA-256) for simpler app auth, keep `ca.pem` for TLS. Keep auto-create off.
- **Postgres:** `CREATE EXTENSION vector;` then `schema.sql`.
- **OpenSearch:** create the quicksearch index; use `avnadmin`.
- Terraform (`aiven/aiven ~> 4.59`) is an option if we want reproducible infra; CLI is faster for the hackathon.

---

## 12. Environment & secrets

`.env.example` per service (never commit real `.env`; never read secret *values*). Keys we'll need:

```
ANTHROPIC_API_KEY=
# Optional: route via Vercel AI Gateway instead
AI_GATEWAY_API_KEY=
ANTHROPIC_BASE_URL=            # https://ai-gateway.vercel.sh  (if using gateway)

RECALL_API_KEY=
RECALL_REGION=                 # e.g. us-east-1
SONIOX_API_KEY=

KAFKA_BOOTSTRAP=               # host:sasl_port
KAFKA_USERNAME=                # avnadmin
KAFKA_PASSWORD=
KAFKA_CA_PATH=./ca.pem

PG_URI=                        # postgresql://avnadmin:...@host:port/defaultdb?sslmode=require
OPENSEARCH_URI=                # https://avnadmin:...@host:port

VERCEL_TOKEN=                  # web-agent deploys
TTS_API_KEY=                   # ElevenLabs/Cartesia (voice-out, later)
```

---

## 13. Build phases & milestones

Sequenced so we always have something demoable, and so the 3 owners can work in parallel after Phase 0.

| Phase | Goal | Deliverable | Depends on |
|---|---|---|---|
| **0. Foundation** | Aiven up, repo skeleton, contracts, topics | Services provisioned; `shared/contracts`; `kafka_admin.py`; `schema.sql` | — |
| **1. Transcript pipeline** | Audio → text → Kafka → FE | Mock audio → Soniox → `meeting.transcript`; FE shows live transcript via API Gateway WS. Then swap mock→Recall. | 0 |
| **2. Knowledge graph** | Seed + query | `central-kg-api` seeds a repo; `/query` `/semantic_search` `/quicksearch` work | 0 |
| **3. Listener MVP** | Parse → ground → answer | Tier-1 classifier + Tier-2 decision; answers a question in the call using the KG; text to FE | 1, 2 |
| **4. Agent suite** | Delegation end-to-end | Listener emits `task.create`; web/data/git agents consume + return results; FE shows them; web-agent deploys a real URL | 2, 3 |
| **5. Polish / stretch** | Voice-out, fact-check, barge-in | TTS into call via Recall; live fact-check verdicts; (stretch) `agent.reasoning` + reviewer agent | 4 |

**Demo target (standup use case):** someone says "can you build us a landing page and tell me who last touched the auth
module" → Listener delegates to web-agent (returns a deployed URL) and git-agent (answers from the KG) → Listener speaks/
shows both. That single flow exercises the whole system.

---

## 14. Risks & open questions

- **Recall.ai minutes/cost** — use mock audio in dev (Phase 1 starts on a recorded WAV / mic), only hit Recall for
  integration + demo.
- **Soniox model name** — examples use `stt-rt-v5`, older docs `stt-rt-preview`; verify at build.
- **Call-graph accuracy** — name-based `CALLS` resolution is approximate; fine for demo, note it. LSP is the upgrade.
- **Listener "annoying voice-out"** — gate `speak` behind confidence + explicit address; default to FE-text.
- **Schema evolution** — `shared/contracts` is the coordination chokepoint; change via PR.
- **Decision deferred:** separate Orchestrator vs Listener-as-orchestrator — MVP picks Listener-as-orchestrator (§7.7).

---

## 15. Sources

Claude Agent SDK & multiagent / streaming / structured output — platform.claude.com/docs/en/managed-agents/* ,
build-with-claude/structured-outputs, models/overview.
Aiven — aiven.io/docs (Kafka SASL/mTLS, auto-create, Karapace, Connect; PostgreSQL extensions list incl. **pgvector yes /
AGE no**, pgvector howto; OpenSearch get-started; free tier; CLI; Terraform `aiven/aiven ~> 4.59`).
Soniox — soniox.com/docs (websocket-api, real-time, audio-formats, node/python SDK, pricing).
Recall.ai — docs.recall.ai (separate/mixed real-time audio, payloads, websocket endpoints, regions, output audio).
KG — Apache AGE (age.apache.org; not on managed PG), pgvector (github.com/pgvector/pgvector), tree-sitter, safishamsi/graphify.
Vercel AI Gateway — vercel.com/docs/ai-gateway (Anthropic Messages API, model strings, OIDC/key, Claude Code env vars).

*Detailed snippets from the research pass are preserved in the team's research notes; lift them into each service's README
as we build.*
```

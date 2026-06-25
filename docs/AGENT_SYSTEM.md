# Agent System — focused plan (our part)

> Scope-down of [PLAN.md](PLAN.md) to **the part our team owns: the worker agent suite + the FE gateway**, packaged
> as **one long-lived agent-runner container**. Everything our agents touch in the data layer goes through **Aiven
> MCP** (the challenge's 34% spine — see [HACKINFO.md](HACKINFO.md)). Dispatch is **pure Kafka**: the call/
> transcription container (a teammate's, separate) produces `agent.tasks.*` onto Aiven Kafka; our container consumes.
> FE is on **Vercel**; Aiven (Postgres + Kafka + OpenSearch) is the data layer.
>
> `PLAN.md` is the whole-system source of truth. **This doc is authoritative for our subsystem.** When `PLAN.md`
> changes in ways that touch our seams (Kafka topics, the message envelope), we update this doc first, then code.

---

## 1. What we own vs. what we integrate with

We own **one agent-runner container**: a long-lived Kafka consumer + a shared agent harness + the specialist
agents + a file/session store + the thin FE gateway. We do **not** own the call/transcription stack — a teammate
runs that in its **own** container (`avatar-agent/` on `main`: LiveKit + Recall + Anam, Soniox STT). The knowledge graph is a teammate's
`central-kg-api`, but **our agents never call its HTTP API** — they read/write the same Aiven Postgres directly
through Aiven MCP (§7).

```
  CALL CONTAINER (teammate)                   AGENT-RUNNER CONTAINER (ours, one image)
 ┌───────────────────────────┐              ┌────────────────────────────────────────────┐
 │ LiveKit/Recall + Soniox STT│   Aiven      │  long-lived Kafka consumer (agent.tasks.*)  │
 │ "ack fast, defer long"     │   Kafka      │  shared HARNESS: validate→claim→fetch→plan  │
 │  └ produces ───────────────┼────────────▶ │     →act→verify→persist→emit                │
 │     meeting.transcript     │              │  specialists: git·web·data·ops·research·echo│
 │     agent.tasks.*          │              │  FILE/SESSION store: sessions/<id>/...       │
 │  └ consumes ◀──────────────┼──────────────┤  Aiven MCP (local mcp-aiven + AIVEN_TOKEN): │
 │     agent.results/activity │              │     aiven_pg_read/write · provision         │
 └───────────────────────────┘              │  FE gateway (REST cmds + Kafka→WS)           │
        ▲ WS/REST                            └───────────────┬────────────────────────────┘
        │                                                    ▼ Aiven Postgres+pgvector (the KG)
   FRONTEND (Vercel) ◀── gateway ── Aiven Kafka tail     (a codebase parsed → graph)
```

| | Owned here | Where | Integration seam |
|---|---|---|---|
| **git / web / data / meeting-ops / research agents** (+ `echo`; kg-writer roadmap) | ✅ | in the **agent-runner container** | consume `agent.tasks.*`, emit `agent.results`/`agent.activity`/`agent.trace` |
| **shared harness + file/session store** | ✅ | same container | — |
| **FE gateway** (thin bridge) | ✅ | same container | REST + WS to the Vercel FE |
| **Call/transcription** (LiveKit/Recall + STT) | ❌ teammate | its own container | produces `meeting.transcript` + `agent.tasks.*` |
| **Knowledge graph** | ❌ teammate | `central-kg-api` (Lambda) | **agents go via Aiven MCP, not its HTTP API** (§7) |
| **Message bus** | shared | Aiven Kafka | **Topics + envelope (§6)** |

**Inbound seam (to us):** `agent.tasks.{web,data,git,…}` — produced by the teammate's call/listener side, one
record per delegated task. **Outbound seams (from us):** `agent.results` (final task outputs), `agent.activity`
(visible status feed — *not* chain-of-thought), and `kg.updates` (facts to persist). Those topics + the envelope
are the only contract that must stay stable across teams.

---

## 2. The agent-runner container (the core)

One image, one deploy. Its main loop is a **long-lived Kafka consumer** — the thing a container can do that a
Lambda can't, and the reason we're not on Lambda (web builds need durable FS + minutes-long loops; one container
is far simpler to debug than N functions + event-source mappings + IAM).

```
[Kafka consumer]  agent.tasks.web/.data/.git ─▶ route by intent ─▶ [shared harness] ─▶ [specialist.run(task, ctx)]
        │                                                                                      │
        └────────────────────────── emit agent.activity / agent.results / kg.updates ◀─────────┘
```

Inside one process:
- **Kafka consumer** (`shared/kafka.py`, `aiokafka`) — one consumer group per agent topic (or one consumer
  subscribed to all three); long-lived, real offset commits.
- **Shared harness** (§4) — every task runs the same lifecycle; specialists only implement `run()`.
- **Specialists** — `git`, `web`, `data`, `meeting-ops`, `research` (all real) + `echo`; `kg-writer` roadmap. Each
  is a module, *not* a separate process.
- **File/session store** (§5) — a folder tree on a durable volume; the web agent's persistent workspace lives here.
- **Aiven MCP** (§3) — local `mcp-aiven` for all KG/provisioning ops.
- **FE gateway** (§8) — a small FastAPI in the same image: REST commands → Kafka, and a Kafka→WS tail for the FE.

All of these share **one warm process**: the `mcp-aiven` stdio session, the Kafka clients, and the Anthropic client
are created once at startup and reused; tasks run as concurrent `asyncio` coroutines so a slow web build never
blocks a fast git lookup. Latency rules are first-class — see §3.5.

---

## 3. The Aiven MCP boundary (our 34% spine)

The challenge rewards agents that talk to data infra **natively via Aiven MCP** and penalises hand-written backend
code between an agent and a DB/queue. So **every KG operation and every fact our agents persist is an Aiven MCP
tool call.** Verified Aiven MCP data-plane tools (per [aiven.io/mcp](https://aiven.io/mcp) +
[Aiven-Open/mcp-aiven](https://github.com/Aiven-Open/mcp-aiven)):

| Purpose | Aiven MCP tool |
|---|---|
| Read the graph (traversal CTEs, pgvector similarity — plain SQL) | `aiven_pg_read` |
| Write the graph (nodes/edges, facts) | `aiven_pg_write` |
| Publish Kafka (`agent.results`, `agent.activity`, `kg.updates`) | `aiven_kafka_topic_message_produce` |
| Provision Kafka / OpenSearch on camera (autonomy criterion) | control-plane: create/configure service |

**How the container reaches MCP — and why the auth risk is gone.** Because we're a long-lived container (not a
subprocess-less Lambda), we run **`mcp-aiven` locally** (`npx mcp-aiven`, stdio) with a **static `AIVEN_TOKEN`** —
no hosted-server OAuth-PKCE dance. The `anthropic` SDK's MCP helpers (`anthropic.lib.tools.mcp`) convert the local
MCP tools into Messages-API tools for the agent loop; our harness wraps that. (The Claude Agent SDK with a local
`mcpServers` entry is a heavier alternative — fine in a container, but our harness already owns the loop.)

**The line (settled):**

| Path | Transport | Why |
|---|---|---|
| KG reads/writes by agents | **Aiven MCP** `aiven_pg_read` / `aiven_pg_write` | The 34% showcase; raw SQL incl. recursive CTEs + pgvector |
| Provision next Aiven service | **Aiven MCP** control-plane (on camera) | The 33% autonomy criterion |
| Emit `agent.results` / `agent.activity` / `kg.updates` | **direct `aiokafka` producer** (harness code) | Realtime: routing a publish through an extra LLM round-trip is pure latency, no benefit. MCP-produce is kept as a *showcase* moment, not the hot path. |
| **Consume** `agent.tasks.*` (main loop) | **direct `aiokafka` consumer** | Consumer-group offsets + low latency |

**The streamlining principle (realtime):** *the LLM tool-loop is only for reasoning-time decisions; the harness
never spends an LLM round-trip on plumbing.* Two ways the harness uses the **one warm MCP session**:
- **Programmatic MCP** (no LLM) — for deterministic, intent-driven queries (e.g. git-agent's templated traversal),
  the harness calls `aiven_pg_read` **directly via the MCP client**. Still "via Aiven MCP" (rubric ✓), but zero LLM
  round-trips for retrieval.
- **LLM-exposed MCP** — only when the agent genuinely must *decide* what to query, the MCP tools are exposed to the
  Messages-API tool-loop.

So the only unavoidable direct (non-MCP) clients are the Kafka consumer/producer at our service boundary; every
*data* read/write is Aiven MCP — which is what the 34% measures.

---

## 3.5 Latency & streamlining (realtime is a hard requirement)

This is realtime tech — perceived latency is a feature. Design rules, in priority order:

1. **Ack fast, defer long.** The listener acknowledges in the call instantly (*"on it — building the page now"*);
   heavy work streams `agent.activity` to the FE; only final/high-confidence `agent.results` are spoken. Perceived
   latency is decoupled from actual work time.
2. **Two paths.** *Fast path* (git/data lookups, fact checks) targets ~1–2 s: programmatic-MCP retrieval (no LLM) +
   at most one **Haiku, streaming, low-effort** call to phrase — or none. *Slow path* (web build/deploy) is
   inherently minutes; it runs async and only streams status. A slow task must never block a fast one.
3. **Minimize LLM round-trips** — the dominant cost (~seconds each). Templated SQL over agentic exploration when the
   intent is known; one streamed call over a multi-turn loop; **prompt-cache** the system prompt + tool schemas + KG
   schema so TTFT and cost drop on every repeat.
4. **Warm everything, spawn nothing per task.** `mcp-aiven` is spawned **once at container start** and its stdio
   session is reused; the Kafka producer/consumer, the Anthropic client, and (via MCP) the PG connection stay warm;
   pre-warm the prompt cache at boot.
5. **Concurrency, not serialization.** One container, but `asyncio` dispatch with bounded concurrency — many tasks
   in flight; the consumer hands each task to a coroutine and keeps reading.
6. **Fewest hops.** Pure-Kafka dispatch + one container means no inter-service HTTP for agents (in-process harness
   calls). Agent→agent subtasks cross Kafka (produce→consume latency), so keep the depth limit tight and prefer
   in-process steps for tightly-coupled work.
7. **Stream end-to-end.** Stream LLM output → `agent.activity` → gateway WS → FE (→ TTS), so the first useful token
   reaches the human as early as possible.

---

## 4. Shared harness (build this first)

The Listener routes work; capability lives in our agents — and every agent runs the **same lifecycle** so we add
specialists, not plumbing:

```
validate → claim/dedupe → fetch context → plan → act → verify → persist artifacts → emit result
```

- **validate** the `task.create` against `shared/contracts.py`.
- **claim/dedupe** on `idempotency_key` (Kafka is at-least-once — redelivery must be a no-op).
- **fetch context** from the KG via `aiven_pg_read` (the `context_refs` pointers).
- **plan → act → verify** — the specialist's `run()`; `verify` gates outputs before they're emitted/spoken.
- **persist** artifacts into the session store (§5).
- **emit** `agent.activity` (visible status: *"building site"*, *"running chart code"* — never raw reasoning) as it
  goes, and `agent.results` (`task.completed`/`task.failed`) at the end — both are **direct Kafka produces** from
  harness code, no LLM round-trip (§3.5).

**Task-schema additions** (to `TaskCreatePayload` in `shared/contracts.py`): `workspace_id`, `parent_task_id`,
`idempotency_key`. Defer `deadline_ms` / `priority` / `requires_confirmation` / `artifact_policy` until they earn
their keep. **Rename** the stretch `agent.reasoning` topic → **`agent.activity`** (visible status, not
chain-of-thought) and add an `ActivityPayload`.

**Agent→agent subtasks** go over Kafka with a `parent_task_id` and a **hard depth limit** (e.g. ≤2) so debugging
stays sane: e.g. web-agent emits `agent.tasks.data` for a chart, waits for that `agent.results`, then deploys.

---

## 5. File / session store

A simple, inspectable folder tree on a **durable volume** (EBS if EC2 / EFS if Fargate; ephemeral container FS is
fine for the demo if we re-seed):

```
sessions/<session_id>/
├─ meta.json                 # meeting id, participants, created_at
├─ workspace/                # the web agent's PERSISTENT site workspace (not one-shot)
├─ artifacts/                # charts, screenshots, build logs, deployed URLs
└─ tasks/<task_id>/          # per-task inputs/outputs/logs
```

The **web agent keeps a persistent `workspace/`** keyed by `workspace_id`: brief + source files + Vercel project/
deployment IDs + latest URL + revision history. `update_website` reopens it, patches, redeploys — never regenerates
from scratch.

---

## 6. Seam A — Kafka topics & envelope (contract)

Topics (created once — Aiven auto-create is OFF; can be an on-camera control-plane MCP moment):

| Topic | Key | Producer | Consumer |
|---|---|---|---|
| `meeting.transcript` | `meeting_id` | avatar `/transcript` · `say.py` · `mock_meeting` (via gateway) | **planner**, gateway Hub |
| `agent.tasks.{web,data,git,ops,research,dev}` | `task_id` | **planner** (transcript→tasks) · gateway `POST /tasks` (ask box / avatar `delegate()`) | **runner** (our container) |
| `agent.results` | `task_id` | **runner** (+ async grounding verdict) | gateway (FE feed) |
| `agent.activity` | `task_id` | **runner** | gateway (FE feed) |
| `agent.trace` | `task_id` | **runner** (streamed thinking/output deltas) | gateway (FE reasoning panel) |
| `agent.control` | `task_id` | gateway `POST /control` (FE stop button) | **runner** (broadcast → cancel) |
| `kg.updates` | `node_key` | **runner** | `central-kg-api` consumer *(roadmap)* |

> The original "call/transcription is a teammate's separate container that produces `agent.tasks.*`" framing (above
> in §1–§2) is **superseded**: the avatar landed on `main`, emits only `meeting.transcript`, and the **planner**
> (in our container) is the single delegation brain that produces the task topics. See [DESIGN.md](DESIGN.md) §6.

Envelope (every message) — single source of truth is `shared/contracts.py` (pydantic), transport-agnostic:

```jsonc
{ "schema": "sunstead.v1", "id": "uuid", "type": "task.create", "meeting_id": "mtg_abc",
  "ts": "2026-06-25T10:00:00.123Z", "payload": { /* type-specific */ } }
```

**Changing a payload = PR to `shared/contracts.py` + a note here.** We *consume* `task.create` and *produce*
`task.completed`/`task.failed` + `activity` + `kg.update` — that's our half of the contract.

---

## 7. Seam B — Knowledge graph via Aiven MCP (not an HTTP client)

Agents reach the graph with `aiven_pg_read` / `aiven_pg_write` against the same Aiven Postgres that backs
`central-kg-api`. **This replaces the old `kg_client.py`** — its endpoints were guesses, and per the MCP-native
mandate agents must not call that HTTP API at all.

**The real schema** (from [CENTRAL-KG-API.md](CENTRAL-KG-API.md) — UUID ids, `(type, name)` natural key):

```sql
nodes  (id uuid, type, name, properties jsonb, embedding vector(1536), source_id, created_at, updated_at)
edges  (id uuid, source_node_id, target_node_id, type, properties jsonb, weight, source_id, created_at)
-- UNIQUE (type, lower(name));  edges UNIQUE (source_node_id, target_node_id, type)
```

Node types: `person, meeting, task, requirement, code_module, document, …` + code-seeding adds `file, function,
class, commit`. Edge types: `depends_on, implements, relates_to, mentions, …` + `DEFINES/IMPORTS/CALLS/AUTHORED/
TOUCHES` for code. SQL the agents issue via MCP:

```sql
-- 2-hop neighborhood of a node (recursive CTE)
WITH RECURSIVE nbr(id, depth) AS (
  SELECT id, 0 FROM nodes WHERE type='file' AND lower(name)=lower('src/auth.ts')
  UNION
  SELECT e.target_node_id, depth+1 FROM edges e JOIN nbr ON e.source_node_id = nbr.id WHERE depth < 2
) SELECT n.* FROM nodes n JOIN nbr ON n.id = nbr.id;

-- pgvector similarity ("what relates to X")
SELECT id, type, name FROM nodes ORDER BY embedding <=> $query_embedding LIMIT 8;
```

**The demo data path.** Our example is a **codebase parsed into the graph**: the teammate's `central-kg-api` seed
CLI runs tree-sitter + `git log` over a target repo → `file/function/class/commit/person` nodes and
`DEFINES/IMPORTS/CALLS/AUTHORED/TOUCHES` edges. The **git-agent** then answers e.g. *"who last touched the auth
module"* with a single `aiven_pg_read` traversal (`person -AUTHORED-> commit -TOUCHES-> file`) — no git checkout.

**`central-kg-api` is FE/seed only** (ingestion sidecar + FE bridge). Its real surface, for the FE/gateway — *not*
for agents: `GET /query?q=`, `/subgraph`, `/entity/{id}`, `/timeline`; `POST /ingest|/extract|/node|/link|/update`.

---

## 8. The agent suite + gateway

Specialists (each a module the harness calls `run(task, ctx)`). **Five are real** (`git`, `web`, `data`,
`meeting-ops`, `research`) plus the no-creds `echo`; `kg-writer` is roadmap, `reviewer` shipped as the **grounding
verifier** (§4, the harness pre-emit gate).

- **git / KG-agent** — `read_git`, `blame`, `who_changed`, `recent_changes`, plus **`ask`** (general KG question
  over any node/edge type). A config-driven, *general* knowledge-graph agent (answers code **and** knowledge
  questions: decisions, meetings, people, policies) via `aiven_pg_read`. Now has a **canned fast path** (templated,
  injection-guarded SQL + a 60s cache + one streamed Haiku phrasing turn) for the known intents, and the agentic LLM
  path for `ask`. Returns `_verify` evidence (the fetched rows) for the grounding gate.
- **web-agent** — `build_website`, `update_website`. **Persistent revisioned workspace** (§5) keyed by
  `workspace_id`; Claude streams a one-file site → written to `SITES_DIR/<task>/` → served URL artifact. (Vercel
  deploy is the documented swap-in; today it's local-serve.)
- **data-agent** — `analyze`, `summarize_metrics`, `query_data`. A **strict-tool** turn plans SQL + a chart spec;
  rows pulled via `aiven_pg_read`; renders a matplotlib PNG **off-thread** → answer + chart artifact. Returns
  `_verify` evidence.
- **meeting-ops** ✅ *(real)* — `recap` / `action_items` / `decisions`. A strict-tool extraction over a transcript
  window (with evidence quotes + speaker attribution) that **writes outcomes back into the KG** via `aiven_pg_write`
  — idempotent slug-keyed `action_item` / `decision` nodes + `in_meeting` / `owns` edges. The flywheel that grows
  the graph from the meeting itself.
- **research** ✅ *(real)* — `research`. Claude **server-side `web_search` / `web_fetch`** (adaptive thinking,
  bounded turns) → answer + sources as URL artifacts; streams thinking/text to `agent.trace`.
- **kg-writer** *(roadmap)* — background transcript→graph extraction (meeting-ops now covers the outcome-write
  slice). **reviewer** — shipped as the **grounding verifier** in the harness (gate, not a separate agent).

**Gateway** (same container) — the Vercel FE's one clean, auth'd connection: `POST` commands → produce to Kafka;
`WS /stream?meeting_id=` tails `meeting.transcript` + `agent.results` + `agent.activity` → browser. FE never touches
Kafka or MCP creds.

Models (as deployed): **Opus 4.8** for the hard structured extractions (meeting-ops) and the research web-search
loop; **Haiku 4.5** for the cheap/fast turns (data-agent's strict-tool plan, the git-agent canned-path phrasing).
Strict tool use is GA on both, so no beta header is needed.

---

## 9. Local dev & deploy

- **Run locally (local-first)**: `docker compose up` starts **redpanda** (local Kafka); `mcp-aiven` runs as a local
  stdio process with `AIVEN_TOKEN`; the session store is a mounted folder. The no-creds **echo** path proves the
  consume→harness→emit loop; `scripts/publish_task.py` stands in for the listener. Switch to Aiven Kafka by env only.
- **Deploy**: one image → **ECS Fargate** or a **plain EC2** (rest of stack is AWS; the teammate's call container
  is already on EC2). Durable volume for `sessions/`. Secrets (`ANTHROPIC_API_KEY`, `AIVEN_TOKEN`, Kafka creds,
  `VERCEL_TOKEN`) via env / Secrets Manager — never read secret *values*, only check key presence.
- **Aiven MCP** + **AWS CLI** are available to us; request AWS auth when we start deploying.

---

## 10. Build order

1. **Connectivity spike** *(de-risks everything; now trivially)* — local `mcp-aiven` + `AIVEN_TOKEN` → one
   `aiven_pg_read("select count(*) from nodes")` + one `aiven_kafka_topic_message_produce`. (The container model
   means this uses the static token — no auth dance.)
2. **Shared harness skeleton** — the lifecycle (§4) + contracts additions + one consumer loop.
3. **git-agent** — `agent.tasks.git` → `aiven_pg_read` traversal against the live `central-kg-pg` → emit
   `agent.results` (direct produce via the harness). *First runnable, MCP-native, demo-bankable milestone.*
4. **web-agent** (persistent workspace → real Vercel URL) then **data-agent**.
5. **gateway** WS bridge so the Vercel FE sees `agent.activity` + `agent.results` live.
6. **meeting-ops** / **kg-writer** as time allows.

Each item is a short branch off `feat/agent-system`; `shared/contracts` changes get extra eyes.

---

## 11. Scaffold realignment (`agent-system/`) — done

The scaffold was rebuilt to the container + all-MCP model (`agent-system/`); the list below is the record:
- **Keep** `shared/contracts.py` (add `workspace_id`/`parent_task_id`/`idempotency_key` + `ActivityPayload`),
  `shared/config.py`, `infra/kafka_admin.py`, the uv workspace.
- **`shared/kafka.py` is now central** (the container's long-lived consumer + the produce fallback) — keep and
  flesh out, *do not* drop it.
- **Delete `shared/kg_client.py`** (agents use Aiven MCP for the KG).
- **Add `shared/mcp.py`** (local `mcp-aiven` stdio + `anthropic.lib.tools.mcp` tool-runner) and
  **`shared/harness.py`** (the §4 lifecycle).
- **Collapse** the per-agent `__main__.py` run-loops into the **one container**: `agents/{web,data,git}/` become
  modules exposing `run(task, ctx)`; a single `agent_runner/` entrypoint hosts the consumer + harness + gateway.
- **Remove** `call-gateway/` and `listener-agent/` from our folder — they're the teammate's call container.
- **Local dev added** — `docker-compose.yml` (redpanda), `.env.example`, `Makefile`, and
  `scripts/{kafka_smoke,publish_task,spike}.py`. The no-creds echo path proves the loop; see the README.

---

## 12. Open questions & risks

- **Auth risk: RESOLVED** by the container model — local `mcp-aiven` + static `AIVEN_TOKEN` removes the hosted-MCP
  OAuth-PKCE dependency.
- **Container host** — ECS Fargate vs a plain EC2 vs Fly/Render. Lean AWS (stack + teammate are there); decide at
  deploy.
- **Durable session volume** — EBS (EC2) / EFS (Fargate); ephemeral is OK for the demo if we re-seed.
- **Heavy web builds** — npm/Playwright inside the container need enough CPU/mem + a build timeout; keep generated
  sites small (static or tiny Vite app) for the demo.
- **Kafka produce is direct (decided)** — the harness emits `agent.results`/`agent.activity` with a direct
  `aiokafka` producer (no LLM round-trip, §3). Optionally demo *one* `aiven_kafka_topic_message_produce` for the rubric.
- **OpenSearch via MCP unverified** — pgvector is the primary retrieval path (HACKINFO: "OpenSearch not guaranteed").
- **Naming** — confirm the call stack is LiveKit vs Recall (doesn't change our Kafka seam; just doc accuracy).
- **`central-kg-api` gaps** — its seed CLI (tree-sitter + git → graph), OpenSearch mirror, and `kg.updates`
  consumer are still the teammate's to build; our git-agent demo depends on the **seed** being run.

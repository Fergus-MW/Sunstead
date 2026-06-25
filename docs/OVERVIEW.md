# Sunstead — Overview (the HEAD)

> _Agent-facing orientation. Current state of the repo, not the aspiration. Last synced 2026-06-25 (avatar merged +
> delegating, Aiven Kafka provisioned, web-agent real, planner + `mock_meeting` shipped). For where we're going, see
> [DESIGN.md](DESIGN.md); for why things are the way they are, see [LOG.md](LOG.md)._

---

## 1. What Sunstead is

An **AI "employee" that joins a live video call** — a photorealistic avatar that listens, converses, and does
real work, grounded in a **knowledge graph** of a codebase. The demo sentence: someone in a standup says *"build
us a landing page and tell me who last touched the auth module,"* and the agent deploys a real URL and answers
from the code graph. Built for the **Aiven "Autonomous Data Operator"** challenge — the differentiator we control
most is **agents talking to data infra natively via Aiven MCP** (34% of the score).

## 2. The honest current state

**Three strong pillars + a FE, now wired into one system.** Each pillar is individually credible *and* the seams
between them are closed in code: there are two working delegation paths (the **planner** consuming
`meeting.transcript`, and the **avatar's `delegate()` tool**), both reaching the worker suite via the gateway. The
remaining risk is no longer "missing wires" — it's **proving the full happy-path as one run, and deploying it.**

| Pillar | State | Reality |
|---|---|---|
| **Knowledge graph** (`central-kg-api/`) | ✅ **strongest** | Live Aiven Postgres+pgvector, **seeded ~6,183 nodes / 26,179 edges** from `anthropic-sdk-python`; runtime retrieval is hybrid **pgvector + trigram** (~125 ms). OpenSearch is mirrored at seed time (`seed/mirror_opensearch.py`) but not yet queried at runtime. Demo-bankable data exists *today*. |
| **Agent suite** (`agent-system/`) | ✅ **MCP-native showpiece** | One container: warm `mcp-aiven` session, Kafka consumer, harness, **+ a planner** (`planner.py`) that turns `meeting.transcript` into delegated `agent.tasks.*`. **git-agent + web-agent both work** — git answers from the live graph via `aiven_pg_read` (verified: top authors 387/32/23 commits); web-agent generates a site with Claude and publishes it to a served URL. echo runs no-creds; **data-agent is still a stub**; web-agent is **local-serve only** (no Vercel yet). |
| **Avatar / listener** (`avatar-agent/`, merged to `main`) | ✅ **the wow** | LiveKit + Recall + Anam talking-face; realtime STT→Sonnet→TTS; full Terraform. Reads the KG over HTTP to `central-kg-api` (correct for the latency-bound realtime layer), and **delegates heavy work via a wired `delegate()` tool** → gateway `POST /tasks` → Kafka `agent.tasks.*` ([tools.py](../avatar-agent/src/avatar_agent/tools.py)). Code-complete; prod just needs `GATEWAY_URL` injected (the Terraform doesn't set it yet). |
| **Frontend** (`meet-joiner/`) | 🟡 **growing** | Next.js bot-launcher (`POST /api/join`) **+ a knowledge-graph explorer** (`/graph`, zero-dep canvas force graph over `central-kg-api`) **+ an agent dashboard** (`/dashboard`: live feed + task board + ask box over the gateway's `WS /stream` / `POST /tasks`). Still missing the in-call transcript overlay. The dashboard's e2e path (gateway↔Kafka↔runner) and `/graph` over the live KG are **both verified running locally**; degrades gracefully when a backend is down. |

**The gap (narrowed to proof + deploy):** every seam is wired in code — `say.py`/avatar → `meeting.transcript` →
planner → `agent.tasks.*` → runner → `agent.results` → gateway WS → dashboard. What's *not* yet banked: a single
recorded run of the full happy-path, and a cloud deployment. Dispatch is proven on local redpanda; **Aiven Kafka is
provisioned** (`kafka-254bd14f`, RUNNING, 9 topics created via MCP — see [LOG.md](LOG.md)), so the remaining deploy
step is just pointing the runner/gateway at it (bootstrap + SASL creds). A few capability spots are still hollow:
**data-agent (stub), embeddings (`embeddings.py` is a no-op → semantic search degrades to trigram), web-agent
(local-serve, no Vercel), and the FE in-call transcript overlay (missing).**

## 3. Repo map — where the code actually is

| Path | Role | Lives on | Status |
|---|---|---|---|
| `central-kg-api/` | KG service (FastAPI/Mangum) + `seed/` CLI (tree-sitter + git → graph) | `main` | real, deployed-ready |
| `infra/` | Aiven provisioning (`provision.sh`, `aiven-mcp.json`) + OpenSearch mirror | `main` | scripts ready; **Aiven Kafka provisioned** (`kafka-254bd14f`, 9 topics via MCP) |
| `agent-system/` | our agent-runner container (shared spine + harness + git/echo/web agents + **gateway** + **planner**) + local redpanda dev + `mock_meeting` | `main` | foundation + gateway + web-agent + delegation (planner + avatar `delegate()`) done; needs the Aiven Kafka switch |
| `meet-joiner/` | FE: bot-launcher + KG graph explorer (`/graph`) + agent dashboard (`/dashboard`) (Next.js, Vercel) | `main` | graph + dashboard built; e2e to gateway proven locally |
| `avatar-agent/` | the listener/avatar (LiveKit/Recall/Anam) | `main` | **merged + delegating**; reads KG over HTTP, delegates heavy work via wired `delegate()` → gateway → Kafka |
| demo transcripts / bench / extra tests | seed demo data | **`origin/feat/demo-data-layers`** | **unmerged** (additive) |
| `docs/` | this doc system + deep references | `main` | — |

> **First mechanical step toward a clean repo:** avatar is now on `main`; the one branch left to land is
> **demo-data** so `main` is the whole system, then close the seams. See [DESIGN.md](DESIGN.md) §Roadmap.

## 4. Where we stand vs the rubric (34 / 33 / 33)

- **34% MCP depth** — **git-agent verified end-to-end on real data** (`aiven_pg_read` through `mcp-aiven`) after the
  org's *Allow MCP connection* toggle was enabled. Strong. *But* the avatar bypasses MCP (HTTP to the KG) — a
  contradiction DESIGN resolves with a two-layer framing.
- **33% autonomy** — the Aiven Kafka cluster exists and **all 9 topics were created via MCP** (`aiven_kafka_topic_create`)
  — the exact evidence judges want, already banked. Re-running a provision/topic-create step *on camera* makes it visible.
- **33% creativity/impact** — the **web-agent is now real**: Claude generates a site and publishes it to a live URL,
  shown as a clickable artifact on the dashboard task board. The **avatar** adds a differentiator most teams lack.

## 5. Run it (local-first)

- **Agent suite — one command:** `cd agent-system && cp .env.example .env && docker compose up --build` (or
  `make stack`) brings up redpanda + topics + **runner + planner + gateway + sites** together. Drive the delegation
  loop with `uv run python scripts/say.py --text "build a landing page and tell me who owns auth"` (or
  `make say T="…"`) — the **planner** turns the utterance into delegated tasks. Full runbook: **[DEPLOY.md](DEPLOY.md)**.
- **Agent suite — per-process (host):** `uv sync && make up && make topics`, then in separate terminals `make run`,
  `make planner`, `make gateway` (:8800), `make sites` (:8810). Smoke: `make echo`; real site: `make web`. echo needs
  no creds; git/web/planner need `AIVEN_TOKEN` + `ANTHROPIC_API_KEY` (the loader merges the repo-root `.env`).
- **KG:** `cd central-kg-api` — FastAPI over the live Aiven PG; seed via `seed/`. (Details: `central-kg-api/README.md`.)
- **Avatar:** `cd avatar-agent` — `avatar-agent start` + `dispatch-bot <meet-url>`. (Details: `avatar-agent/README.md`.)
- **FE:** `cd meet-joiner && npm install && cp .env.example .env && npm run dev` → `/graph` (needs `central-kg-api`
  at `KG_API_URL`), `/dashboard` (needs the gateway: `make gateway` in `agent-system/`). Both degrade gracefully if
  their backend is down. (Details: `meet-joiner/README.md`.)

## 6. The immediate plan

The seams are wired; the fastest path to a winning state is now **proof + polish, not building** (full roadmap in
[DESIGN.md](DESIGN.md) §Roadmap):
1. ~~Merge the avatar branch~~ ✅ **done** (on `main`). Land **demo-data** to finish the merge.
2. ~~Provision Aiven Kafka via MCP~~ ✅ **done** — cluster RUNNING, 9 topics created via MCP. Next: point the
   runner/gateway at it (bootstrap + SASL creds), and re-show a provision step on camera for the autonomy evidence.
3. ~~Wire one delegation avatar → gateway → `agent.tasks.*` → worker → FE~~ ✅ **done** — `delegate()` tool wired
   ([tools.py](../avatar-agent/src/avatar_agent/tools.py)); a **planner** adds a second, avatar-free path.
   **← now: prove the full happy-path as one recorded run** (`make stack` + `make say T="…"`), the highest-leverage
   action left before the pitch.
4. ~~Make web-agent real~~ ✅ **done** — Claude-generated site → served URL artifact on the dashboard.
5. Reconcile the docs to reality (this doc system is step zero of that).

The thing to guard against: polishing pillars in isolation instead of closing seams. The marginal *wire* is worth
far more than the marginal feature right now.

## 7. Deployment

Multi-host by design — each unit deploys in its native idiom. Full rationale is in [DESIGN.md §4](DESIGN.md); the
**operator runbook (one-command local + per-component deploy) is [DEPLOY.md](DEPLOY.md)**; this is the summary.

**Containerization status:** `agent-system/Dockerfile` (one image → runner/planner/gateway/sites, **built &
verified** — includes Node + `mcp-aiven`), `central-kg-api/Dockerfile.server` (uvicorn variant beside the Lambda
one), and a `docker-compose.yml` that runs the whole local stack in one command (`make stack`).

| Unit | Where | Lifecycle | Notes |
|---|---|---|---|
| **Frontend** (`meet-joiner/`) | **Vercel** | serverless | Set `GATEWAY_URL` (server proxy) + `NEXT_PUBLIC_GATEWAY_WS_URL` (browser WS). |
| **Gateway + runner + planner** (`agent-system/`) | **one always-on container host** (Fly / Railway / ECS Fargate / VM) | long-lived | **One image, three processes** (`gateway` + `runner` + `planner`), plus `sites`. **Cannot be serverless** — long-lived Kafka consumers + persistent producer + WebSockets. |
| **Central KG API** (`central-kg-api/`) | **Lambda** (Mangum `Dockerfile`) **or container** (`Dockerfile.server`) | serverless or long-lived | Stateless; talks to Aiven PG/OpenSearch. |
| **Avatar** (`avatar-agent/`) | its **Terraform** stack (LiveKit on EC2 + ECS worker + S3/CloudFront viewer + dispatch Lambda) | long-lived | See `avatar-agent/deploy/`. |
| **Data** | **Aiven cloud** | managed | PG+pgvector + OpenSearch + Kafka, all reached by agents via Aiven MCP. |

**Two non-obvious things:**
- The dashboard's WebSocket connects **browser → gateway directly** (Vercel can't proxy a long-lived WS), so the
  gateway must be publicly reachable over **`wss://` + TLS**. The web-agent's served-site port (`:8810`) must be
  public too for artifact URLs to resolve.
- **Hackathon scoping:** deploy the verifiable loop — **FE (Vercel) + gateway/runner (one Fly app) + KG API (Lambda)
  + the existing Aiven Kafka** — and keep the **avatar local**. The avatar is the demo "wow" but the riskiest to
  deploy (its own realtime Terraform stack: LiveKit + Recall + Anam); cloud-deploying the ask-box → Kafka → agent →
  live-result loop is the thing a judge can actually click.

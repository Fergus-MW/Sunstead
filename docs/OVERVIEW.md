# Sunstead — Overview (the HEAD)

> _Agent-facing orientation. Current state of the repo, not the aspiration. Last synced 2026-06-25 against
> `main` @ `ff8adde`. For where we're going, see [DESIGN.md](DESIGN.md); for why things are the way they are,
> see [LOG.md](LOG.md)._

---

## 1. What Sunstead is

An **AI "employee" that joins a live video call** — a photorealistic avatar that listens, converses, and does
real work, grounded in a **knowledge graph** of a codebase. The demo sentence: someone in a standup says *"build
us a landing page and tell me who last touched the auth module,"* and the agent deploys a real URL and answers
from the code graph. Built for the **Aiven "Autonomous Data Operator"** challenge — the differentiator we control
most is **agents talking to data infra natively via Aiven MCP** (34% of the score).

## 2. The honest current state

**Three strong, mostly-disconnected pillars + a thin FE.** Each pillar is individually credible; the end-to-end
flow is **not wired yet**, and that — not missing capability — is the whole remaining risk.

| Pillar | State | Reality |
|---|---|---|
| **Knowledge graph** (`central-kg-api/`) | ✅ **strongest** | Live Aiven Postgres+pgvector, **seeded ~6,183 nodes / 26,179 edges** from `anthropic-sdk-python`; runtime retrieval is hybrid **pgvector + trigram** (~125 ms). OpenSearch is mirrored at seed time (`seed/mirror_opensearch.py`) but not yet queried at runtime. Demo-bankable data exists *today*. |
| **Agent suite** (`agent-system/`) | ✅ **MCP-native showpiece** | One container: warm `mcp-aiven` session, Kafka consumer, harness. **git-agent + web-agent both work** — git answers from the live graph via `aiven_pg_read` (verified: top authors 387/32/23 commits); web-agent generates a site with Claude and publishes it to a served URL. echo runs no-creds; data-agent still a stub. **Full local loop proven end-to-end** (FE ask box → gateway → Kafka → runner → result → WS). |
| **Avatar / listener** (`avatar-agent/`, merged to `main`) | ✅ **the wow** | LiveKit + Recall + Anam talking-face; realtime STT→LLM→TTS; full Terraform. **But it reaches data over HTTP to `central-kg-api`, emits no Kafka, and doesn't delegate to the agent suite** — merged into the tree, but the delegation seam is still unwired. |
| **Frontend** (`meet-joiner/`) | 🟡 **growing** | Next.js bot-launcher (`POST /api/join`) **+ a knowledge-graph explorer** (`/graph`, zero-dep canvas force graph over `central-kg-api`) **+ an agent dashboard** (`/dashboard`: live feed + task board + ask box over the gateway's `WS /stream` / `POST /tasks`). Still missing the in-call transcript overlay. The dashboard's e2e path (gateway↔Kafka↔runner) and `/graph` over the live KG are **both verified running locally**; degrades gracefully when a backend is down. |

**The gap (narrowed):** the FE → gateway → Kafka → runner → FE loop is now **wired and proven locally** — the
dashboard ask box dispatches real tasks and git/web agents return results live. The remaining seam is the
**avatar**, which still produces no `agent.tasks.*`, so *mid-call* delegation isn't live yet. Dispatch is proven on
local redpanda; **Aiven Kafka is now provisioned** (`kafka-254bd14f`, RUNNING, 9 topics created via MCP — see
[LOG.md](LOG.md)), so the remaining deploy step is just pointing the runner/gateway at it (bootstrap + SASL creds).

## 3. Repo map — where the code actually is

| Path | Role | Lives on | Status |
|---|---|---|---|
| `central-kg-api/` | KG service (FastAPI/Mangum) + `seed/` CLI (tree-sitter + git → graph) | `main` | real, deployed-ready |
| `infra/` | Aiven provisioning (`provision.sh`, `aiven-mcp.json`) + OpenSearch mirror | `main` | scripts ready; **Aiven Kafka provisioned** (`kafka-254bd14f`, 9 topics via MCP) |
| `agent-system/` | our agent-runner container (shared spine + harness + git/echo/web agents + **gateway**) + local redpanda dev | `main` | foundation + gateway + web-agent done; needs the delegation wire + the Aiven Kafka switch |
| `meet-joiner/` | FE: bot-launcher + KG graph explorer (`/graph`) + agent dashboard (`/dashboard`) (Next.js, Vercel) | `main` | graph + dashboard built; e2e to gateway proven locally |
| `avatar-agent/` | the listener/avatar (LiveKit/Recall/Anam) | `main` | **merged**; strong but off-architecture (HTTP to KG, emits no Kafka, no `delegate()`) |
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

- **Agent suite:** `cd agent-system && uv sync && cp .env.example .env && make up && make topics`, then in separate
  terminals: `make run` (runner), `make gateway` (:8800, FE bus), `make sites` (:8810, web-agent output). Smoke:
  `make echo`; real site: `make web`. echo needs no creds; git/web agents need `AIVEN_TOKEN` + `ANTHROPIC_API_KEY` in
  the runner's env (the loader merges the repo-root `.env`). (Details: `agent-system/README.md`.)
- **KG:** `cd central-kg-api` — FastAPI over the live Aiven PG; seed via `seed/`. (Details: `central-kg-api/README.md`.)
- **Avatar:** `cd avatar-agent` — `avatar-agent start` + `dispatch-bot <meet-url>`. (Details: `avatar-agent/README.md`.)
- **FE:** `cd meet-joiner && npm install && cp .env.example .env && npm run dev` → `/graph` (needs `central-kg-api`
  at `KG_API_URL`), `/dashboard` (needs the gateway: `make gateway` in `agent-system/`). Both degrade gracefully if
  their backend is down. (Details: `meet-joiner/README.md`.)

## 6. The immediate plan

The fastest path to a winning state is **not more building — it's a merge and a few wires** (full roadmap in
[DESIGN.md](DESIGN.md) §Roadmap):
1. ~~Merge the avatar branch~~ ✅ **done** (on `main`). Land **demo-data** to finish the merge.
2. ~~Provision Aiven Kafka via MCP~~ ✅ **done** — cluster RUNNING, 9 topics created via MCP. Next: point the
   runner/gateway at it (bootstrap + SASL creds), and re-show a provision step on camera for the autonomy evidence.
3. **Wire one delegation** avatar → gateway `POST /tasks` → `agent.tasks.*` → worker → deliverable on the FE. *(The
   worker side is proven; only the avatar's `delegate()` call is missing.)* **← the single highest-leverage wire left.**
4. ~~Make web-agent real~~ ✅ **done** — Claude-generated site → served URL artifact on the dashboard.
5. Reconcile the docs to reality (this doc system is step zero of that).

The thing to guard against: polishing pillars in isolation instead of closing seams. The marginal *wire* is worth
far more than the marginal feature right now.

## 7. Deployment

Multi-host by design — each unit deploys in its native idiom. Full rationale + the target table is in
[DESIGN.md §4](DESIGN.md); this is the operator-facing summary.

| Unit | Where | Lifecycle | Notes |
|---|---|---|---|
| **Frontend** (`meet-joiner/`) | **Vercel** | serverless | Set `GATEWAY_URL` (server proxy) + `NEXT_PUBLIC_GATEWAY_WS_URL` (browser WS). |
| **Gateway + agent-runner** (`agent-system/`) | **one always-on container host** (Fly / Railway / ECS Fargate) | long-lived | Same image, two processes (`make gateway` + `make run`). **Cannot be serverless** — long-lived Kafka consumer + persistent producer + WebSockets. |
| **Central KG API** (`central-kg-api/`) | **Lambda + API Gateway** (Mangum) | serverless | Stateless; talks to Aiven PG/OpenSearch. Cloud Run / Fly also fine. |
| **Avatar** (`avatar-agent/`) | its **Terraform** stack (LiveKit on EC2 + ECS worker + S3/CloudFront viewer + dispatch Lambda) | long-lived | See `avatar-agent/deploy/`. |
| **Data** | **Aiven cloud** | managed | PG+pgvector + OpenSearch + Kafka, all reached by agents via Aiven MCP. |

**Two non-obvious things:**
- The dashboard's WebSocket connects **browser → gateway directly** (Vercel can't proxy a long-lived WS), so the
  gateway must be publicly reachable over **`wss://` + TLS**. The web-agent's served-site port (`:8810`) must be
  public too for artifact URLs to resolve.
- **Hackathon scoping:** deploy the verifiable loop — **FE (Vercel) + gateway/runner (one Fly app) + KG API (Lambda)
  + the existing Aiven Kafka** — and keep the **avatar local**. The avatar is the demo "wow" but the riskiest to
  deploy and still off-architecture; cloud-deploying the ask-box → Kafka → agent → live-result loop is the thing a
  judge can actually click.

# Sunstead — Overview (the HEAD)

> _Agent-facing orientation. Current state of the repo, not the aspiration. Last synced 2026-06-25 against
> `main` @ `af6427d`. For where we're going, see [DESIGN.md](DESIGN.md); for why things are the way they are,
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
| **Knowledge graph** (`central-kg-api/`) | ✅ **strongest** | Live Aiven Postgres+pgvector, **seeded ~6,183 nodes / 26,179 edges** from `anthropic-sdk-python`; hybrid OpenSearch→PG retrieval ~125 ms. Demo-bankable data exists *today*. |
| **Agent suite** (`agent-system/`) | ✅ **MCP-native showpiece** | One container: warm `mcp-aiven` session, Kafka consumer, harness; **git-agent works** (answers from the graph via `aiven_pg_read`). web/data agents are stubs. |
| **Avatar / listener** (`ferg/avatar-agent`, unmerged) | ✅ **the wow** | LiveKit + Recall + Anam talking-face; realtime STT→LLM→TTS; full Terraform. **But it reaches data over HTTP to `central-kg-api`, emits no Kafka, and doesn't delegate to the agent suite.** |
| **Frontend** (`meet-joiner/`) | 🟡 **growing** | Next.js bot-launcher (`POST /api/join`) **+ a knowledge-graph explorer** (`/graph`, zero-dep canvas force graph over `central-kg-api`) **+ an agent dashboard** (`/dashboard`: live feed + task board + ask box over the gateway's `WS /stream` / `POST /tasks`). Still missing the in-call transcript overlay; the dashboard's e2e path (gateway↔Kafka↔runner) is **not yet run** — it degrades gracefully until then. |

**The gap:** the avatar (the real listener) produces no `agent.tasks.*`; the agent suite consumes a topic only a
dev script writes to. The two best-built parts don't talk to each other. **Aiven Kafka is also not provisioned**
(no free tier) — the dispatch spine exists only as local redpanda.

## 3. Repo map — where the code actually is

| Path | Role | Lives on | Status |
|---|---|---|---|
| `central-kg-api/` | KG service (FastAPI/Mangum) + `seed/` CLI (tree-sitter + git → graph) | `main` | real, deployed-ready |
| `infra/` | Aiven provisioning (`provision.sh`, `aiven-mcp.json`) + OpenSearch mirror | `main` | scripts ready; **Kafka not provisioned** |
| `agent-system/` | our agent-runner container (shared spine + harness + git/echo agents + **gateway**) + local redpanda dev | `main` | foundation + gateway done; needs web-agent + the delegation wire |
| `meet-joiner/` | FE: bot-launcher + KG graph explorer (`/graph`) + agent dashboard (`/dashboard`) (Next.js, Vercel) | `feat/agent-system` | graph + dashboard built; e2e to gateway untested |
| `avatar-agent/` | the listener/avatar (LiveKit/Recall/Anam) | **`origin/ferg/avatar-agent`** | **unmerged**, strong, off-architecture |
| demo transcripts / bench / extra tests | seed demo data | **`origin/feat/demo-data-layers`** | **unmerged** (additive) |
| `docs/` | this doc system + deep references | `main` | — |

> **First mechanical step toward a clean repo:** land the unmerged branches on the integration line (avatar +
> demo-data) so `main` is the whole system, then close the seams. See [DESIGN.md](DESIGN.md) §Roadmap.

## 4. Where we stand vs the rubric (34 / 33 / 33)

- **34% MCP depth** — **git-agent is the proof** (`aiven_pg_read` through `mcp-aiven`, on real data). Strong. *But*
  the avatar bypasses MCP (HTTP to the KG) — a contradiction DESIGN resolves with a two-layer framing.
- **33% autonomy** — the **un-provisioned Kafka is the opportunity**: provision it via MCP *on camera* = the exact
  evidence judges want. Not done yet.
- **33% creativity/impact** — the **avatar is a differentiator** most teams won't have; the **web-agent deliverable**
  (deploy a real URL mid-call) is the visual wow and is currently a stub.

## 5. Run it (local-first)

- **Agent suite:** `cd agent-system && uv sync && cp .env.example .env && docker compose up -d && uv run python infra/kafka_admin.py`,
  then `uv run python -m agent_runner` + `uv run python scripts/publish_task.py --intent echo --args '{"text":"hi"}'`.
  The echo path needs no creds; git-agent needs `AIVEN_TOKEN` + `ANTHROPIC_API_KEY`. (Details: `agent-system/README.md`.)
- **KG:** `cd central-kg-api` — FastAPI over the live Aiven PG; seed via `seed/`. (Details: `central-kg-api/README.md`.)
- **Avatar:** on `ferg/avatar-agent` — `avatar-agent start` + `dispatch-bot <meet-url>`. (Details: `avatar-agent/README.md`.)
- **FE:** `cd meet-joiner && npm install && cp .env.example .env && npm run dev` → `/graph` (needs `central-kg-api`
  at `KG_API_URL`), `/dashboard` (needs the gateway: `make gateway` in `agent-system/`). Both degrade gracefully if
  their backend is down. (Details: `meet-joiner/README.md`.)

## 6. The immediate plan

The fastest path to a winning state is **not more building — it's a merge and a few wires** (full roadmap in
[DESIGN.md](DESIGN.md) §Roadmap):
1. Merge the unmerged branches (avatar, demo-data) onto `main`.
2. **Provision Aiven Kafka via MCP, on camera** (kills the infra gap + banks autonomy).
3. **Wire one delegation** avatar → `agent.tasks.*` → worker → deliverable on the FE.
4. **Make web-agent real** (tiny static site → Vercel) for the visual payoff.
5. Reconcile the docs to reality (this doc system is step zero of that).

The thing to guard against: polishing pillars in isolation instead of closing seams. The marginal *wire* is worth
far more than the marginal feature right now.

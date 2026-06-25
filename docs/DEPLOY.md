# Sunstead — Deploy & Run (the RUNBOOK)

> _Operator-facing. How to run the whole system locally with one command, and how to deploy each piece to its
> target. The "where/why" summary is [OVERVIEW.md §7](OVERVIEW.md); the architecture rationale is
> [DESIGN.md §4](DESIGN.md). This is the how._

---

## Cheat sheet — every way to launch, drive, and deploy

> _All commands run from `agent-system/` unless noted. `make X` aliases are shown in parens; if `make` isn't
> installed (e.g. Windows), use the raw command. Section numbers point to the detail below._

**Launch locally** (pick one mode):

| Goal | Command | Notes |
|---|---|---|
| **Everything, one command** | `docker compose up --build`  *(make stack)* | Full backend in containers. The easiest. → §2 |
| Everything **+ KG API** | `docker compose --profile full up --build`  *(make stack-full)* | Needs `DATABASE_URL` in `.env`. → §2 |
| **Hybrid** (best for dev) | `docker compose up -d redpanda`, then run services on the host (below) | Kafka in Docker, Python on host = fast edit→restart. |
| Per-process on the host | `uv run python -m agent_runner` · `… .planner` · `… .gateway` · `python scripts/serve_sites.py`  *(make run / planner / gateway / sites)* | One per terminal/pane. |
| Stop & reset | `docker compose down -v`  *(make stack-down)* | `-v` also wipes the sites/sessions volumes. |

**Drive / test it** (after the stack is up):

| Goal | Command | Needs |
|---|---|---|
| **Plumbing only** (no creds) | `docker compose exec runner python scripts/publish_task.py --intent echo --args '{"text":"hi"}'` | nothing |
| **Planner delegation loop** | `docker compose exec planner python scripts/say.py --text "build a landing page and tell me who owns auth"`  *(host: make say T="…")* | `ANTHROPIC_API_KEY` (+ `AIVEN_TOKEN` for git questions) |
| Direct ask (no Kafka) | `uv run python scripts/ask.py --question "…"`  *(make ask Q="…")* | `AIVEN_TOKEN` + `ANTHROPIC_API_KEY` |
| Watch what happens | `docker compose logs -f planner runner`  *(make stack-logs)* | — |
| See it in the UI | `cd meet-joiner && npm run dev` → open `/dashboard` | gateway running |

**Deploy** (→ §4 for the full how):

| Component | Target | How |
|---|---|---|
| Frontend | **Vercel** | push / `vercel deploy`; set `GATEWAY_URL`, `KG_API_URL`, `NEXT_PUBLIC_GATEWAY_WS_URL` |
| Agent suite | **container host / VM** | build & push `sunstead/agent-suite`; run `runner` + `planner` + `gateway` (+ `sites`) with `KAFKA_*` → Aiven (§3) |
| Central KG API | **Lambda or container** | `Dockerfile` (Mangum) → Lambda, **or** `Dockerfile.server` → container `:8000` |
| Avatar | **its Terraform** | `avatar-agent/deploy/` |
| Data | **Aiven** | managed; already provisioned |

---

## 0. The shape (what runs where)

The backend is **not one app** — it's a handful of small services plus managed data. They talk over a mix of
Kafka (worker↔worker), HTTP/WS (browser↔gateway, avatar↔KG), and MCP (agents↔Postgres). Kafka is the internal
backbone, **not** the only transport.

```
┌─ Vercel ───────────────┐      ┌─ Aiven (managed) ──────────┐
│  meet-joiner (Next.js)  │      │  Kafka  (kafka-254bd14f)   │
│  FE + /api/* proxies    │      │  Postgres + pgvector       │
└───────────┬─────────────┘      └────────────▲───────────────┘
            │ HTTPS / WSS                      │ SASL_SSL │ asyncpg
            ▼                                  │          │
┌─ Container host (Fly / Railway / ECS / a VM) ┴──────────┴────┐
│  agent-suite image — ONE image, four processes:              │
│    • runner    • planner    • gateway (:8800)   • sites (:8810)│
│  central-kg-api (uvicorn, :8000)                              │
│  avatar / transcriber (its own Terraform stack)              │
└───────────────────────────────────────────────────────────────┘
```

**Why these placements:** the gateway/runner/planner hold long-lived Kafka consumers, a persistent producer, and
(for the gateway) WebSockets — none of which survive on serverless, so they need an always-on host. The FE is
stateless request/response → Vercel. The data is managed → Aiven. The avatar has its own realtime infra → its
Terraform.

---

## 1. The two images

| Image | Built from | Runs | Notes |
|---|---|---|---|
| **`sunstead/agent-suite`** | `agent-system/Dockerfile` | runner **or** planner **or** gateway **or** sites | One image, four entrypoints (compose picks the `command`). Includes **Node.js + `mcp-aiven`** — the git/data agents launch the Aiven MCP server over stdio. |
| **`sunstead/central-kg-api`** | `central-kg-api/Dockerfile.server` | uvicorn HTTP server (:8000) | The sibling `central-kg-api/Dockerfile` (Mangum) is the **Lambda** variant — pick one per deploy target. |

The agent suite is **one image run four ways** on purpose: identical dependencies, one thing to build and push,
and the processes differ only by their `command`. Adding the planner cost zero new infra — it's just a fourth
command on the same image.

---

## 2. Local: the whole stack in one command

This replaces the old "four terminals" dance.

```bash
cd agent-system
cp .env.example .env          # fill ANTHROPIC_API_KEY + AIVEN_TOKEN (git/web/planner need them)
docker compose up --build     # redpanda + topics + runner + planner + gateway + sites
#   make stack   does the same; make stack-full   also starts central-kg-api
```

What comes up (see `agent-system/docker-compose.yml`):
- **redpanda** — local Kafka. Host tools reach it at `localhost:19092`; the containers reach it at `redpanda:9092`
  (compose overrides `KAFKA_BOOTSTRAP` for you, so `.env` stays localhost-first).
- **topics** — a one-shot that creates all topics, then exits. Every other service waits for it.
- **runner / planner / gateway / sites** — the agent suite.

**Drive it** (the delegation loop, no avatar needed):
```bash
# in another terminal
docker compose exec planner python scripts/say.py \
  --text "build us a dark landing page for Sunstead and tell me who last touched the auth module"
#   or, from the host venv:  cd agent-system && uv run python scripts/say.py --text "..."
```
Watch `docker compose logs -f planner runner` (or `make stack-logs`): the planner logs **two** delegations under
one `plan_id`, the runner picks both up, results land on `agent.results`. The dashboard at the gateway (`:8800`)
shows them live.

**Tear down:** `docker compose down -v` (the `-v` also drops the `sites-data`/`sessions-data` volumes).

### Add the KG API locally
```bash
docker compose --profile full up --build    # also starts central-kg-api on :8000
```
It needs `DATABASE_URL` in `.env` (the Aiven asyncpg URL, or a local Postgres). Without the profile, run the KG
API however you do today — it's independent of the Kafka stack.

---

## 3. Point at Aiven Kafka (local → cloud bus)

No code change — it's all env. In `.env` (or the deploy host's environment):
```bash
KAFKA_BOOTSTRAP=<service>-<project>.aivencloud.com:<port>
KAFKA_SECURITY=SASL_SSL                 # or SSL for mTLS
KAFKA_SASL_MECHANISM=SCRAM-SHA-256
KAFKA_USERNAME=avnadmin
KAFKA_PASSWORD=<from Aiven console>
KAFKA_CA_PATH=./ca.pem                  # mount/copy the CA into the container
KAFKA_PARTITIONS=3
KAFKA_RF=3
```
Then drop the `redpanda` dependency (you're using the managed cluster). The 9 topics already exist on
`kafka-254bd14f` (created via MCP — see [LOG.md](LOG.md)), so you can skip the `topics` one-shot in cloud.

---

## 4. Deploy each component

### Frontend — Vercel
- `meet-joiner/` deploys as-is. Set `GATEWAY_URL` (server-side proxy target for `/api/tasks`),
  `KG_API_URL` (for `/api/graph/*`), and `NEXT_PUBLIC_GATEWAY_WS_URL` (the browser's WebSocket target).
- Vercel **cannot** hold a Kafka consumer — and doesn't need to. The browser opens the WS **directly** to the
  gateway; the `/api/*` routes are stateless proxies.

### Agent suite — one always-on container host (Fly / Railway / ECS Fargate / a VM)
- Build & push `sunstead/agent-suite`. Run it as **three long-lived services** — `runner`, `planner`, `gateway`
  — plus `sites` (or fold the static host into the gateway / move web output to real object storage).
- All config is env: `ANTHROPIC_API_KEY`, `AIVEN_TOKEN`, the `KAFKA_*` block (§3), `SITES_BASE_URL` (the public
  URL artifacts resolve at), and optionally `VERCEL_TOKEN` / `VERCEL_PROJECT` to publish sites to Vercel instead
  of the local static host (then `SITES_BASE_URL`/`:8810` is unused for artifact links).
- On a single VM, `docker compose up -d` with the `KAFKA_*` pointed at Aiven is the lowest-effort path.

### Central KG API — container or Lambda
- **Container:** `central-kg-api/Dockerfile.server` → any container host, `:8000`. Set `DATABASE_URL`
  (Aiven asyncpg, `?ssl=require`), `ANTHROPIC_API_KEY`, optional `OPENSEARCH_URL`.
- **Lambda:** the existing `central-kg-api/Dockerfile` (Mangum) → Lambda + API Gateway. Same env. Stateless.

### Avatar — its own Terraform
- `avatar-agent/deploy/` (LiveKit on EC2 + ECS worker + S3/CloudFront viewer + dispatch Lambda), **or** the
  consolidated single-box runbook ([SINGLE_EC2.md](../avatar-agent/deploy/SINGLE_EC2.md)). **Delegation seam:** its
  `delegate(intent, brief)` tool already targets the gateway's `POST /tasks` (`GATEWAY_URL`). Alternative single-brain
  design: have the avatar publish `meeting.transcript` and let the **planner** do all routing — then the avatar needs
  to know nothing about intents (see [DESIGN.md](DESIGN.md) §6).

### Data — Aiven (managed)
- Postgres+pgvector + Kafka already provisioned. Nothing to deploy; reached by agents via MCP and by the KG API
  via asyncpg.

---

## 5. The two things that bite on demo day

1. **Public TLS for the gateway *and* the KG API.** The browser (on Vercel) opens a WebSocket straight to the
   gateway, so it must be reachable over **`wss://` with a real cert**; the FE also proxies graph queries to the
   KG API over HTTPS. On a VM, put **Caddy** (automatic TLS) in front of both — it's the 5-minute fix. The
   web-agent's `:8810` (or wherever `SITES_BASE_URL` points) must be public too, or artifact links 404 —
   **unless** `VERCEL_TOKEN` is set, in which case sites publish to Vercel and `:8810` needn't be exposed.
2. **Secrets live in the host environment, never the image.** The `.dockerignore` files keep `.env` out of the
   build. Inject `ANTHROPIC_API_KEY` / `AIVEN_TOKEN` / `KAFKA_PASSWORD` / `DATABASE_URL` as host env vars or your
   platform's secrets store.

---

## 6. Hackathon-scoped recommendation

Deploy the loop a judge can **click**, keep the riskiest piece local:
- **FE → Vercel**, **agent suite (runner + planner + gateway) → one Fly/Railway app**, **KG API → Lambda or the
  same host**, **Aiven Kafka + PG** already up.
- Keep the **avatar local** (it's the "wow" but the most fragile to deploy — its own realtime stack). The
  cloud-deployed ask-box → planner → agent → live-result loop is the demonstrable thing; the avatar narrates over
  it from a laptop.

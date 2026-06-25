# Sunstead

An **AI "employee" that joins a live video call** — a photorealistic avatar that
listens, converses, and does real work, grounded in a **knowledge graph** of a
codebase and backed by a swarm of specialist agents that talk to data **natively
via Aiven MCP**. Built for the Aiven "Autonomous Data Operator" challenge.

> One sentence exercises the whole system: someone in a standup says *"build us a
> landing page and tell me who last touched the auth module"* — the avatar emits the
> utterance, a planner decomposes it into tasks, the web-agent deploys a real URL and
> the git/KG-agent answers from the live code graph, and both surface on a dashboard.

## Repo map

| Path | What it is |
|---|---|
| [`avatar-agent/`](avatar-agent) | The realtime listener/avatar — LiveKit + Recall + Anam talking-face, STT→Sonnet→TTS. Reads the KG over HTTP; emits each utterance to the gateway. |
| [`agent-system/`](agent-system) | The worker suite in one container — Kafka consumer → harness → specialist agents (**git · web · data · meeting-ops · research · echo**), plus the **planner** (transcript→tasks) and the FE **gateway** (`POST /tasks` + `WS /stream`). All data ops go through Aiven MCP. |
| [`central-kg-api/`](central-kg-api) | The knowledge-graph service (FastAPI/Mangum) + the `seed/` CLI (tree-sitter + git → graph). Live on Aiven Postgres + pgvector, seeded ~6,183 nodes / 26,179 edges. |
| [`meet-joiner/`](meet-joiner) | The Next.js frontend — bot-launcher + a KG **graph explorer** (`/graph`) + a mission-control **agent dashboard** (`/dashboard`). |
| [`infra/`](infra) | Aiven provisioning (`provision.sh`, `aiven-mcp.json`) + the OpenSearch mirror. |

## Where to start

- **[docs/OVERVIEW.md](docs/OVERVIEW.md)** — the HEAD: current state of the whole system. Start here.
- **[docs/DESIGN.md](docs/DESIGN.md)** — the target architecture and roadmap.
- **[docs/LOG.md](docs/LOG.md)** — the decision history (more insight than `git log`).
- **[docs/DEPLOY.md](docs/DEPLOY.md)** — the operator runbook (one-command local stack + per-component deploy).
- **[docs/README.md](docs/README.md)** — how the doc system is organized (split by tense).

Each subproject has its own README with run instructions; the fastest local path is
`cd agent-system && cp .env.example .env && docker compose up --build` (the whole bus
in one command) — see OVERVIEW §5.

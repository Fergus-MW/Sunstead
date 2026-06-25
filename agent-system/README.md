# agent-system

Our team's subsystem: the **worker agent suite + FE gateway**, packaged as **one long-lived
agent-runner container**. Dispatch is **pure Kafka**; every KG read/write goes through **Aiven MCP**
(`mcp-aiven`, run locally with a static `AIVEN_TOKEN`). Realtime latency is a first-class constraint.

**Plan:** [`../docs/AGENT_SYSTEM.md`](../docs/AGENT_SYSTEM.md) (focused) · [`../docs/PLAN.md`](../docs/PLAN.md) (whole system).
**Run/deploy:** [`../docs/DEPLOY.md`](../docs/DEPLOY.md) (the operator runbook — one-command stack + per-component deploy).

## Layout
| Path | What |
|---|---|
| `shared/` | `contracts` (pydantic) · `kafka` (PLAINTEXT/SASL) · `mcp` (warm Aiven MCP) · `harness` (lifecycle) · `streaming` (trace deltas) · `sessions` · `config` |
| `agent-runner/` | one image, run several ways: the **runner** (Kafka consumer → harness → `agents/{echo,git,web,data}`), the **planner** (transcript→tasks), and the **gateway** (HTTP `/tasks` + WS `/stream`) |
| `infra/kafka_admin.py` | create topics idempotently (local + Aiven) |
| `scripts/` | `kafka_smoke`/`publish_task` (drive the bus) · `say` (speak into a meeting) · `ask` (direct slice) · `spike`/`ingest_kg` (Aiven MCP) · `serve_sites` (web-agent host) · `fetch_kafka_creds` (Aiven Kafka mTLS) |
| `Dockerfile` · `docker-compose.yml` | one image + the whole local stack (redpanda + topics + runner + planner + gateway + sites) |

## Quickstart — the whole stack, one command
```bash
cd agent-system
cp .env.example .env                     # add ANTHROPIC_API_KEY + AIVEN_TOKEN for git/web/planner
docker compose up --build                # redpanda + topics + runner + planner + gateway + sites  (make stack)
# drive the delegation loop (another terminal):
docker compose exec planner python scripts/say.py --text "build a landing page and tell me who owns auth"
# -> planner splits it into tasks; runner runs them; results stream on the gateway WS (:8800)
```
Full runbook (hybrid dev, Aiven Kafka, deploy targets): [`../docs/DEPLOY.md`](../docs/DEPLOY.md).

## Quickstart — local, zero credentials (proves the bus + loop)
```bash
cd agent-system
uv sync                                  # installs the workspace (shared + agent-runner)
cp .env.example .env                     # local defaults already point at redpanda
docker compose up -d                     # local Kafka on localhost:19092   (make up)
uv run python infra/kafka_admin.py       # create topics                    (make topics)
uv run python scripts/kafka_smoke.py     # Kafka round-trip                 (make smoke)

# end-to-end loop (one terminal runs the container, another publishes a task):
uv run python -m agent_runner            # the agent-runner                 (make run)
uv run python scripts/publish_task.py --intent echo --args '{"text":"hi"}'   # (make echo)
# -> watch the runner log + an agent.results / agent.activity message
```

## With credentials (the MCP path)
```bash
# in .env:  ANTHROPIC_API_KEY=...   AIVEN_TOKEN=...
uv run python scripts/spike.py           # dumps Aiven MCP tool schemas + reads the graph (make spike)
uv run python scripts/publish_task.py --intent who_changed \
    --args '{"question":"who last touched the auth module?"}'   # needs the codebase seeded into the KG
```

To target **Aiven Kafka** instead of local: set the `KAFKA_*` Aiven block in `.env` (+ `KAFKA_RF=3`).

## Build order
1. local bus + loop (above) ✅ foundation  2. `scripts/spike.py` (settle Aiven MCP) 3. `git` agent
4. `web` agent (Vercel) → `data` agent  5. FE gateway (Kafka→WS)

Short-lived branches off `feat/agent-system`; `shared/contracts.py` changes get extra review.

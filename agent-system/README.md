# agent-system

Our team's subsystem: the live-call pipeline + the Claude agent suite.
Self-contained here; integrates with the rest of Sunstead only via **Kafka topics** and the
**`central-kg-api` HTTP client**.

**Plan:** see [`../docs/AGENT_SYSTEM.md`](../docs/AGENT_SYSTEM.md) (focused) and [`../docs/PLAN.md`](../docs/PLAN.md) (whole system).

## Layout
| Path | What |
|---|---|
| `shared/` | contracts (pydantic), kafka helpers, KG client, config — **build first** |
| `call-gateway/` | Recall→Soniox→Kafka (+ mock WAV source, + TTS out) |
| `listener-agent/` | Agent A: two-tier parse → decide → delegate → speak |
| `agents/web-agent/` | Agent B → Vercel |
| `agents/data-agent/` | Agent C → analysis |
| `agents/git-agent/` | Agent D → repo/git (often answers from KG) |
| `gateway/` | thin FE-facing REST + Kafka→WS bridge |
| `infra/` | `kafka_admin.py` (creates topics; auto-create is OFF on Aiven) |

## Quickstart (dev)
```bash
cd agent-system
uv sync                       # installs workspace + shared
cp .env.example .env          # fill in secrets (never commit .env)
uv run python infra/kafka_admin.py   # create Kafka topics on Aiven
# then run a component, e.g.:
uv run -m call_gateway        # dev mode: plays a WAV → transcript on Kafka
```

## Build order
1. `shared` contracts/kafka/config  2. `infra` topics  3. `call-gateway` (mock)  4. `gateway` WS
5. `listener-agent`  6. agents (git → web → data)  7. `call-gateway` live (Recall) + TTS

Short-lived branches off `feat/agent-system`; `shared/contracts.py` changes get extra review.

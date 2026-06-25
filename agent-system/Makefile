.PHONY: sync up down topics smoke run planner gateway gateway-smoke sites echo web say mock spike ask try \
        build stack stack-full stack-down stack-logs

sync:    ; uv sync
up:      ; docker compose up -d
down:    ; docker compose down
topics:  ; uv run python infra/kafka_admin.py
smoke:   ; uv run python scripts/kafka_smoke.py
run:     ; uv run python -m agent_runner
planner: ; uv run python -m agent_runner.planner
gateway: ; uv run python -m agent_runner.gateway
gateway-smoke: ; uv run python scripts/gateway_smoke.py
sites:   ; uv run python scripts/serve_sites.py
echo:    ; uv run python scripts/publish_task.py --intent echo --args '{"text":"hello"}'
web:     ; uv run python scripts/publish_task.py --intent build_website --args '{"brief":"one-page landing for Sunstead","style":"dark, aurora"}'
say:     ; uv run python scripts/say.py --text "$(T)"
mock:    ; uv run python scripts/mock_meeting.py        # interactive: speak + watch the agents (no Recall/LiveKit)
spike:   ; uv run python scripts/spike.py
ask:     ; uv run python scripts/ask.py --question "$(Q)"
try:     ; uv run python scripts/try_agent.py --intent $(I) --args '$(A)'   # one agent, no Kafka (I=intent A=json)

# --- containerized stack (one command; no per-service terminals) — docs/DEPLOY.md ---
build:      ; docker compose build
stack:      ; docker compose up --build                 # redpanda + topics + runner + planner + gateway + sites
stack-full: ; docker compose --profile full up --build  # + central-kg-api
stack-down: ; docker compose down -v
stack-logs: ; docker compose logs -f planner runner

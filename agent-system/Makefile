.PHONY: sync up down topics smoke run gateway echo spike

sync:    ; uv sync
up:      ; docker compose up -d
down:    ; docker compose down
topics:  ; uv run python infra/kafka_admin.py
smoke:   ; uv run python scripts/kafka_smoke.py
run:     ; uv run python -m agent_runner
gateway: ; uv run python -m agent_runner.gateway
echo:    ; uv run python scripts/publish_task.py --intent echo --args '{"text":"hello"}'
spike:   ; uv run python scripts/spike.py

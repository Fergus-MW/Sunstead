.PHONY: sync up down topics smoke run gateway gateway-smoke sites echo web spike ask

sync:    ; uv sync
up:      ; docker compose up -d
down:    ; docker compose down
topics:  ; uv run python infra/kafka_admin.py
smoke:   ; uv run python scripts/kafka_smoke.py
run:     ; uv run python -m agent_runner
gateway: ; uv run python -m agent_runner.gateway
gateway-smoke: ; uv run python scripts/gateway_smoke.py
sites:   ; uv run python scripts/serve_sites.py
echo:    ; uv run python scripts/publish_task.py --intent echo --args '{"text":"hello"}'
web:     ; uv run python scripts/publish_task.py --intent build_website --args '{"brief":"one-page landing for Sunstead","style":"dark, aurora"}'
spike:   ; uv run python scripts/spike.py
ask:     ; uv run python scripts/ask.py --question "$(Q)"

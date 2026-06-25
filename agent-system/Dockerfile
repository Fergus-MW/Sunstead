# Sunstead agent suite — ONE image, run four ways (docs/DEPLOY.md).
#   runner   : python -m agent_runner            (the workers; default CMD)
#   planner  : python -m agent_runner.planner    (the transcript -> delegation brain)
#   gateway  : python -m agent_runner.gateway    (HTTP/WS front door, :8800)
#   sites    : python scripts/serve_sites.py     (static host for web-agent output, :8810)
# docker-compose overrides `command:` per service; all four share this image.
FROM python:3.12-slim

# Node.js — the git/data agents launch the Aiven MCP server (`mcp-aiven`) over stdio.
# Pre-install it globally so the first task doesn't pay an npx download at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
 && npm install -g mcp-aiven \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# uv — fast, lockfile-driven installer (matches local `uv sync`).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Workspace manifests + members, then a frozen sync (reproducible from uv.lock).
COPY pyproject.toml uv.lock ./
COPY shared ./shared
COPY agent-runner ./agent-runner
RUN uv sync --frozen --no-dev

# Runtime helpers (the static server + the topic admin used by the `topics` one-shot).
COPY scripts ./scripts
COPY infra ./infra

# Default to the worker; compose overrides this for planner/gateway/sites.
CMD ["python", "-m", "agent_runner"]

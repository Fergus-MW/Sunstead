"""Environment-driven config + the single source of Kafka topic names.

Topic names live here so every component and the admin script agree. Defaults are
**local-first** (redpanda PLAINTEXT on localhost); flip the env to point at Aiven.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

logger = logging.getLogger("shared.config")


# --- topics (single source of truth) -------------------------------------

TRANSCRIPT = "meeting.transcript"
MEETING_EVENTS = "meeting.events"
TASKS_WEB = "agent.tasks.web"
TASKS_DATA = "agent.tasks.data"
TASKS_GIT = "agent.tasks.git"
TASKS_OPS = "agent.tasks.ops"        # meeting-ops (recap / action items / decisions)
TASKS_RESEARCH = "agent.tasks.research"  # research agent (live web search)
TASKS_DEV = "agent.tasks.dev"        # dev/echo (no creds)
RESULTS = "agent.results"
ACTIVITY = "agent.activity"          # visible status feed to the FE
TRACE = "agent.trace"                # streamed reasoning/output deltas to the FE
CONTROL = "agent.control"            # operator/conductor commands (cancel) → the runner
KG_UPDATES = "kg.updates"

# topics our agent-runner CONSUMES
TASK_TOPICS: list[str] = [TASKS_WEB, TASKS_DATA, TASKS_GIT, TASKS_OPS, TASKS_RESEARCH, TASKS_DEV]

# every topic the admin script ensures exists
ALL_TOPICS: list[str] = [
    TRANSCRIPT, MEETING_EVENTS,
    TASKS_WEB, TASKS_DATA, TASKS_GIT, TASKS_OPS, TASKS_RESEARCH, TASKS_DEV,
    RESULTS, ACTIVITY, TRACE, CONTROL, KG_UPDATES,
]

# intent -> the task topic a producer should publish to
TASK_TOPIC_BY_INTENT = {
    "echo": TASKS_DEV,
    "build_website": TASKS_WEB, "update_website": TASKS_WEB,
    "analyze": TASKS_DATA, "summarize_metrics": TASKS_DATA, "query_data": TASKS_DATA,
    "read_git": TASKS_GIT, "blame": TASKS_GIT, "who_changed": TASKS_GIT, "recent_changes": TASKS_GIT,
    "ask": TASKS_GIT,    # general KG question -> same agent/topic as git (the agent is general)
    "recap": TASKS_OPS, "action_items": TASKS_OPS, "decisions": TASKS_OPS,  # meeting-ops
    "research": TASKS_RESEARCH,                                              # research agent
}


# --- settings -------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


# --- knowledge-graph datastore (single source) ---------------------------
# The Aiven Postgres holding the central KG; agents target it via MCP pg_read.
# Read at import (like the topic names above) so they're usable in agents'
# module-level system prompts.
KG_PROJECT = _env("KG_PROJECT", "jq01")
KG_SERVICE = _env("KG_SERVICE", "central-kg-pg")
KG_DB = _env("KG_DB", "defaultdb")


@dataclass
class KafkaSettings:
    bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:19092"))
    security: str = field(default_factory=lambda: _env("KAFKA_SECURITY", "PLAINTEXT"))  # PLAINTEXT|SASL_SSL|SSL
    sasl_mechanism: str = field(default_factory=lambda: _env("KAFKA_SASL_MECHANISM", "SCRAM-SHA-256"))
    username: str = field(default_factory=lambda: _env("KAFKA_USERNAME", "avnadmin"))
    password: str = field(default_factory=lambda: _env("KAFKA_PASSWORD"))
    ca_path: str = field(default_factory=lambda: _env("KAFKA_CA_PATH", "./ca.pem"))
    cert_path: str = field(default_factory=lambda: _env("KAFKA_CERT_PATH"))
    key_path: str = field(default_factory=lambda: _env("KAFKA_KEY_PATH"))
    partitions: int = field(default_factory=lambda: _int("KAFKA_PARTITIONS", 1))   # local=1, Aiven=3
    replication_factor: int = field(default_factory=lambda: _int("KAFKA_RF", 1))   # local=1, Aiven=3


def _default_mcp_cmd() -> str:
    # npx on Windows is npx.cmd; the Linux container uses npx
    return _env("MCP_AIVEN_CMD", "npx.cmd" if sys.platform == "win32" else "npx")


@dataclass
class McpSettings:
    """Local Aiven MCP server (mcp-aiven over stdio)."""
    aiven_token: str = field(default_factory=lambda: _env("AIVEN_TOKEN"))
    services_scope: str = field(default_factory=lambda: _env("AIVEN_SERVICES_SCOPE", "pg,kafka"))
    cmd: str = field(default_factory=_default_mcp_cmd)
    args: list[str] = field(default_factory=lambda: (_env("MCP_AIVEN_ARGS", "-y mcp-aiven")).split())
    read_only: bool = field(default_factory=lambda: _env("AIVEN_READ_ONLY", "false").lower() == "true")
    # expose service credentials (Kafka certs, connection URIs) in MCP responses — needed
    # to fetch the Aiven Kafka connection for the agent-runner. Keep OFF unless fetching creds.
    allow_secrets: bool = field(default_factory=lambda: _env("AIVEN_ALLOW_SECRETS", "false").lower() == "true")

    @property
    def configured(self) -> bool:
        return bool(self.aiven_token)


@dataclass
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_base_url: str = field(default_factory=lambda: _env("ANTHROPIC_BASE_URL"))
    model_smart: str = field(default_factory=lambda: _env("MODEL_SMART", "claude-opus-4-8"))
    model_mid: str = field(default_factory=lambda: _env("MODEL_MID", "claude-sonnet-4-6"))
    model_fast: str = field(default_factory=lambda: _env("MODEL_FAST", "claude-haiku-4-5"))
    sessions_dir: str = field(default_factory=lambda: _env("SESSIONS_DIR", "./.sessions"))
    # web-agent publish target: with a token set, deploy all sites to ONE Vercel project
    # (many files, one project) instead of the local server. Team-scoped token → no team id.
    vercel_token: str = field(default_factory=lambda: _env("VERCEL_TOKEN"))
    vercel_project: str = field(default_factory=lambda: _env("VERCEL_PROJECT", "sunstead-sites"))
    vercel_team_id: str = field(default_factory=lambda: _env("VERCEL_TEAM_ID"))
    max_concurrency: int = field(default_factory=lambda: _int("MAX_CONCURRENCY", 8))
    consumer_group: str = field(default_factory=lambda: _env("CONSUMER_GROUP", "agent-runner"))
    kafka: KafkaSettings = field(default_factory=KafkaSettings)
    mcp: McpSettings = field(default_factory=McpSettings)

    def __post_init__(self) -> None:
        # defensive: ignore a base_url that isn't a real URL (e.g. a stray
        # inline-comment value copied from .env.example, or an empty
        # `ANTHROPIC_BASE_URL=` injected by a docker env_file) — fall back to the
        # direct API. The Anthropic SDK reads ANTHROPIC_BASE_URL straight from
        # os.environ when we don't pass base_url, so a junk/empty value there
        # breaks every client with a misleading "Connection error"; scrub it so
        # the SDK uses its built-in default.
        if not self.anthropic_base_url.startswith("http"):
            self.anthropic_base_url = ""
            os.environ.pop("ANTHROPIC_BASE_URL", None)


def load() -> Settings:
    """Load settings. Merge every .env from cwd up to the filesystem root,
    nearest-wins, **skipping empty values** — so a blank template .env (e.g. a
    fresh `agent-system/.env` from `cp .env.example .env`) can't shadow a real
    key that lives in the repo-root .env. dotenv is an optional dev convenience.
    """
    try:
        from pathlib import Path

        from dotenv import dotenv_values

        merged: dict[str, str] = {}
        for d in reversed([Path.cwd(), *Path.cwd().parents]):  # farthest first → nearest overrides
            p = d / ".env"
            if p.exists():
                for k, v in dotenv_values(p).items():
                    if v and not v.lstrip().startswith("#"):  # ignore empty + comment-only values
                        merged[k] = v
        for k, v in merged.items():
            os.environ.setdefault(k, v)
    except ImportError:
        pass  # python-dotenv is an optional dev dependency; prod uses the real env
    except Exception:  # a malformed/unreadable .env shouldn't be invisible
        logger.debug("failed to merge .env files", exc_info=True)
    return Settings()

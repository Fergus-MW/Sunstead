"""Environment-driven config + the single source of Kafka topic names.

Topic names live here (not in infra/) so every component and the admin script agree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# --- topics (single source of truth) -------------------------------------

TRANSCRIPT = "meeting.transcript"
MEETING_EVENTS = "meeting.events"
TASKS_WEB = "agent.tasks.web"
TASKS_DATA = "agent.tasks.data"
TASKS_GIT = "agent.tasks.git"
RESULTS = "agent.results"
KG_UPDATES = "kg.updates"
REASONING = "agent.reasoning"  # stretch

# (name, partitions, replication_factor) — RF 3 is the Aiven default
ALL_TOPICS: list[tuple[str, int, int]] = [
    (TRANSCRIPT, 3, 3),
    (MEETING_EVENTS, 3, 3),
    (TASKS_WEB, 3, 3),
    (TASKS_DATA, 3, 3),
    (TASKS_GIT, 3, 3),
    (RESULTS, 3, 3),
    (KG_UPDATES, 3, 3),
    (REASONING, 3, 3),
]

TASK_TOPIC_BY_INTENT = {
    "build_website": TASKS_WEB, "update_website": TASKS_WEB,
    "analyze": TASKS_DATA, "summarize_metrics": TASKS_DATA, "query_data": TASKS_DATA,
    "read_git": TASKS_GIT, "blame": TASKS_GIT, "who_changed": TASKS_GIT, "recent_changes": TASKS_GIT,
}


# --- settings -------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class KafkaSettings:
    bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP"))
    security: str = field(default_factory=lambda: _env("KAFKA_SECURITY", "SASL_SSL"))
    sasl_mechanism: str = field(default_factory=lambda: _env("KAFKA_SASL_MECHANISM", "SCRAM-SHA-256"))
    username: str = field(default_factory=lambda: _env("KAFKA_USERNAME", "avnadmin"))
    password: str = field(default_factory=lambda: _env("KAFKA_PASSWORD"))
    ca_path: str = field(default_factory=lambda: _env("KAFKA_CA_PATH", "./ca.pem"))
    cert_path: str = field(default_factory=lambda: _env("KAFKA_CERT_PATH"))
    key_path: str = field(default_factory=lambda: _env("KAFKA_KEY_PATH"))


@dataclass
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_base_url: str = field(default_factory=lambda: _env("ANTHROPIC_BASE_URL"))
    kg_base_url: str = field(default_factory=lambda: _env("KG_BASE_URL", "http://localhost:8000"))
    kg_stub: bool = field(default_factory=lambda: _env("KG_STUB", "false").lower() == "true")
    kafka: KafkaSettings = field(default_factory=KafkaSettings)


def load() -> Settings:
    return Settings()

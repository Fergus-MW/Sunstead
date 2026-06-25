"""Shared glue for the agent subsystem: contracts, kafka, MCP, harness, sessions, config.

`mcp` is intentionally NOT eager-imported here — it lazy-loads anthropic/mcp so the
local no-creds Kafka path stays light.
"""

from . import config, contracts, harness, kafka, sessions

__all__ = ["config", "contracts", "harness", "kafka", "sessions"]

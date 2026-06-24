"""Shared glue for the agent subsystem: contracts, kafka, KG client, config."""

from . import config, contracts, kafka, kg_client

__all__ = ["config", "contracts", "kafka", "kg_client"]

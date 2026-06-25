"""Connectivity spike — the §11 de-risk. Starts the local Aiven MCP and dumps its tool
schemas (so we learn the exact `aiven_pg_read` / `aiven_kafka_topic_message_produce` args),
then runs one read against the knowledge graph.

    AIVEN_TOKEN=... uv run python scripts/spike.py

Needs AIVEN_TOKEN. Resolves the auth question by using the static-token local server.
"""

from __future__ import annotations

import asyncio
import json

from shared import config
from shared.mcp import AivenMCP


async def main() -> None:
    s = config.load()
    if not s.mcp.configured:
        raise SystemExit("set AIVEN_TOKEN (create at https://console.aiven.io/profile/tokens)")

    mcp = await AivenMCP(s.mcp).start()
    schemas = mcp.tool_schemas()
    print("Aiven MCP tools:", *(f"\n  - {n}" for n in schemas))
    print("\naiven_pg_read input schema:")
    print(json.dumps(schemas.get("aiven_pg_read"), indent=2))

    # Once we know the arg names from the schema above, a programmatic read looks like:
    #   rows = await mcp.pg_read("select count(*) from nodes", project="jq01", service="central-kg-pg")
    #   print("nodes:", rows)
    await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

"""Ingest arbitrary (non-code) knowledge into the KG via Aiven MCP `aiven_pg_write`.

Demonstrates the general ingestion path: any domain → nodes with JSONB properties, queryable by the
same agent that answers code questions. Here: a few company "policy" nodes (a brand-new node type).
Idempotent (upsert on the (type, lower(name)) key).

    uv run python scripts/ingest_kg.py
"""

from __future__ import annotations

import asyncio
import json

from shared import config
from shared.mcp import AivenMCP

KG_PROJECT, KG_SERVICE = "jq01", "central-kg-pg"

# (type, name, properties) — any structured payload goes in `properties` (JSONB).
NODES = [
    ("policy", "Remote work policy",
     {"text": "Employees may work remotely up to 3 days per week; core hours are 10:00-15:00 CET.", "owner": "People Ops"}),
    ("policy", "Expense policy",
     {"text": "Expenses under EUR 50 need no pre-approval; submit receipts within 30 days.", "owner": "Finance"}),
    ("policy", "On-call policy",
     {"text": "On-call rotates weekly; acknowledge pages within 15 minutes; a comp day is given for weekend pages.", "owner": "Engineering"}),
]


def _sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


async def main() -> None:
    mcp = await AivenMCP(config.load().mcp).start()
    values = ",\n  ".join(
        f"(gen_random_uuid(), {_sql_literal(t)}, {_sql_literal(n)}, {_sql_literal(json.dumps(p))}::jsonb)"
        for (t, n, p) in NODES
    )
    sql = (
        "INSERT INTO nodes (id, type, name, properties) VALUES\n  " + values +
        "\nON CONFLICT (type, lower(name)) DO UPDATE SET properties = EXCLUDED.properties"
    )
    print("writing", len(NODES), "policy nodes …")
    res = await mcp.call_tool(
        "aiven_pg_write",
        {"project": KG_PROJECT, "service_name": KG_SERVICE, "query": sql, "reasoning": "ingest demo non-code knowledge"},
    )
    print("write result:", res[-300:])
    check = await mcp.call_tool(
        "aiven_pg_read",
        {"project": KG_PROJECT, "service_name": KG_SERVICE,
         "query": "select name, properties->>'owner' owner from nodes where type='policy' order by name",
         "reasoning": "verify ingest"},
    )
    print("policy nodes now:\n", check[-500:])
    await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

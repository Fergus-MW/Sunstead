"""Create our Kafka topics on the Aiven Kafka service **via Aiven MCP** — the on-camera
autonomy moment: infra created through MCP tool calls, not a direct admin client.
(The aiokafka-direct equivalent is infra/kafka_admin.py, for local redpanda.)

    uv run python scripts/provision_topics.py                 # list only
    uv run python scripts/provision_topics.py --create        # create missing topics
    uv run python scripts/provision_topics.py --service kafka-254bd14f --create
"""

from __future__ import annotations

import argparse
import asyncio
import json

from shared import config

PROJECT = "jq01"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", default="kafka-254bd14f")
    ap.add_argument("--create", action="store_true")
    a = ap.parse_args()

    from shared.mcp import AivenMCP

    s = config.load()
    mcp = await AivenMCP(s.mcp).start()
    try:
        listed = await mcp.call_tool(
            "aiven_kafka_topic_list", {"project": PROJECT, "service_name": a.service}
        )
        # the response wraps JSON in untrusted-data boundaries; pull the JSON blob out
        blob = listed[listed.find("{"): listed.rfind("}") + 1]
        existing = set()
        try:
            data = json.loads(blob)
            for t in data.get("topics", data.get("kafka_topics", [])):
                existing.add(t["topic_name"] if isinstance(t, dict) else t)
        except Exception:
            existing = set()  # fall back to attempting all
        print(f"existing topics ({len(existing)}): {sorted(existing)}")

        for name in config.ALL_TOPICS:
            if name in existing:
                print(f"exists   {name}")
                continue
            if not a.create:
                print(f"missing  {name}  (run with --create)")
                continue
            try:
                r = await mcp.call_tool(
                    "aiven_kafka_topic_create",
                    {"project": PROJECT, "service_name": a.service, "topic_name": name},
                )
                print(f"created  {name}  {r[:80]}")
            except Exception as e:
                print(f"ERR      {name}: {str(e)[:200]}")
    finally:
        await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

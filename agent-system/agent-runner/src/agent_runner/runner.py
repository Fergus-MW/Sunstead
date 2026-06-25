"""The agent-runner main loop (docs/AGENT_SYSTEM.md §2, §3.5).

Warm clients are created once at startup; a long-lived Kafka consumer reads `agent.tasks.*`
and dispatches each task to a specialist via the shared harness, as a bounded-concurrency
`asyncio` task — so a slow web build never blocks a fast lookup.
"""

from __future__ import annotations

import asyncio
import logging

from shared import config
from shared.contracts import Envelope, TaskCreatePayload
from shared.harness import AgentContext, run_task
from shared.kafka import consume, make_producer

from .registry import resolve

log = logging.getLogger("agent_runner")


async def _process(env: Envelope[TaskCreatePayload], ctx: AgentContext, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            agent = resolve(env.payload.intent)
        except KeyError as e:
            async def agent(task, ctx, _e=e):  # surface "no agent" as a failed result
                raise _e
        await run_task(env, agent, ctx)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = config.load()

    producer = await make_producer(s)
    mcp = None
    anthropic_client = None

    if s.mcp.configured:
        from shared.mcp import AivenMCP
        log.info("starting local Aiven MCP (mcp-aiven)…")
        mcp = await AivenMCP(s.mcp).start()
        log.info("Aiven MCP tools: %s", list(mcp.tool_schemas()))
    else:
        log.warning("AIVEN_TOKEN unset — KG agents disabled; echo path still works")

    if s.anthropic_api_key:
        from anthropic import AsyncAnthropic
        kwargs = {"api_key": s.anthropic_api_key}
        if s.anthropic_base_url:
            kwargs["base_url"] = s.anthropic_base_url
        anthropic_client = AsyncAnthropic(**kwargs)
    else:
        log.warning("ANTHROPIC_API_KEY unset — LLM agents disabled; echo path still works")

    ctx = AgentContext(s, producer, mcp, anthropic_client)
    sem = asyncio.Semaphore(s.max_concurrency)
    tasks: set[asyncio.Task] = set()

    log.info("agent-runner up — consuming %s (group=%s, bootstrap=%s)",
             config.TASK_TOPICS, s.consumer_group, s.kafka.bootstrap)
    try:
        async for msg in consume(*config.TASK_TOPICS, group_id=s.consumer_group,
                                 settings=s, auto_offset_reset="latest"):
            try:
                env = Envelope[TaskCreatePayload].model_validate_json(msg.value)
            except Exception as e:
                log.warning("dropping unparseable message on %s: %s", msg.topic, e)
                continue
            t = asyncio.create_task(_process(env, ctx, sem))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
    finally:
        await producer.stop()
        if mcp:
            await mcp.stop()

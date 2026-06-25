"""The agent-runner main loop (docs/AGENT_SYSTEM.md §2, §3.5).

Warm clients are created once at startup; a long-lived Kafka consumer reads `agent.tasks.*`
and dispatches each task to a specialist via the shared harness, as a bounded-concurrency
`asyncio` task — so a slow web build never blocks a fast lookup.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from shared import config
from shared.contracts import ControlPayload, Envelope, TaskCreatePayload
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


async def _control_loop(running: dict[str, asyncio.Task], settings: config.Settings) -> None:
    """Tail `agent.control` and cancel the matching in-flight task (docs/DESIGN.md §7).

    A **broadcast** group (unique per process) so every runner instance sees every command —
    the runner that actually owns the task_id cancels it; others no-op. Cancellation lands at
    the agent's next await (LLM stream / tool call), and the harness emits a terminal result.
    """
    group = f"agent-control-{uuid.uuid4().hex[:8]}"
    while True:  # stay up across a flaky Kafka, like the gateway broadcast consumer
        try:
            async for msg in consume(config.CONTROL, group_id=group, settings=settings,
                                     auto_offset_reset="latest"):
                try:
                    env = Envelope[ControlPayload].model_validate_json(msg.value)
                except Exception as e:
                    log.warning("dropping unparseable control message: %s", e)
                    continue
                if env.payload.action == "cancel":
                    t = running.get(env.payload.task_id)
                    if t and not t.done():
                        log.info("cancelling task %s (reason=%s)", env.payload.task_id, env.payload.reason)
                        t.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("control loop error (%s) — retrying", e)
            await asyncio.sleep(2)


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
    running: dict[str, asyncio.Task] = {}                   # task_id -> task, so the control channel can cancel

    control = asyncio.create_task(_control_loop(running, s), name="agent-control")

    log.info("agent-runner up — consuming %s (group=%s, bootstrap=%s); control on %s",
             config.TASK_TOPICS, s.consumer_group, s.kafka.bootstrap, config.CONTROL)
    try:
        async for msg in consume(*config.TASK_TOPICS, group_id=s.consumer_group,
                                 settings=s, auto_offset_reset="latest"):
            try:
                env = Envelope[TaskCreatePayload].model_validate_json(msg.value)
            except Exception as e:
                log.warning("dropping unparseable message on %s: %s", msg.topic, e)
                continue
            tid = env.payload.task_id
            t = asyncio.create_task(_process(env, ctx, sem))
            running[tid] = t
            # Pop only if we're still the registered task for this id. Under at-least-once
            # redelivery a duplicate overwrites running[tid], skips fast via idempotency, and its
            # done-callback would otherwise evict the *original* still-running task — leaving it
            # uncancellable. The identity guard keeps the live task reachable by _control_loop.
            t.add_done_callback(lambda done, _tid=tid: running.get(_tid) is done and running.pop(_tid, None))
    finally:
        control.cancel()
        await producer.stop()
        if mcp:
            await mcp.stop()

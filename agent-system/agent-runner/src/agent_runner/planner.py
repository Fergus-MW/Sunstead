"""The planner / orchestration brain (docs/DESIGN.md §4 — the delegation seam).

The third long-lived process alongside the runner and the gateway. It tails
`meeting.transcript`, decides whether an utterance contains work the agent suite can
do, and—if so—publishes one or more `task.create` messages onto the right
`agent.tasks.*` topics. This is what turns the avatar from "answers questions itself"
into "listens, delegates, and supervises".

    meeting.transcript ──▶ [planner: LLM decides] ──▶ agent.tasks.* ──▶ runner ──▶ agent.results

It consumes from a brand-new group at **earliest** so a request said *before* the
planner connected is still picked up (a dropped request is a broken promise). Each
delegated task is also announced on `agent.activity` so the FE shows the planner
deciding — the visible orchestration, not chain-of-thought.

Run it (alongside the agent-runner, same image):  uv run python -m agent_runner.planner
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict, deque

from shared import config
from shared.contracts import (
    ActivityPayload,
    Envelope,
    TaskCreatePayload,
    TranscriptPayload,
)
from shared.harness import now_iso
from shared.kafka import consume, make_producer, publish

log = logging.getLogger("planner")

# The planner may delegate any real intent — but never `echo` (a no-op smoke intent).
PLANNABLE_INTENTS: list[str] = [i for i in config.TASK_TOPIC_BY_INTENT if i != "echo"]

# meeting-ops intents need the meeting so far, not just the triggering utterance — the planner
# keeps a rolling per-meeting transcript and attaches it to these tasks (the agent stays stateless).
OPS_INTENTS = {"recap", "action_items", "decisions"}
TRANSCRIPT_WINDOW = 80  # recent final utterances kept per meeting
MAX_MEETINGS = 64       # distinct meetings tracked (LRU) — bounds memory in a long-lived planner
SEEN_MAX = 4096         # transcript-id dedupe window (FIFO) — bounds memory, same reason

PLANNER_SYSTEM = f"""You are the planner for Sunstead — an AI employee that sits in a live meeting and \
delegates work to a suite of specialist agents. You read one utterance from the meeting transcript and \
decide whether it contains a concrete, actionable request the agents can fulfil right now.

Be conservative. Most utterances are ordinary conversation — for those, propose NO tasks. Only propose a \
task when someone is clearly asking for work to be done that maps to an agent below.

Agents and the intents they handle:
- web-agent — `build_website` (make a new one-page site), `update_website` (change an existing one).
    args: {{"brief": "<what the site is for>", "style": "<optional look & feel>"}}
- git-agent — `who_changed`, `blame`, `read_git`, `recent_changes` (questions about the codebase / its history,
    answered from a knowledge graph). args: {{"question": "<the natural-language question>"}}
- data-agent — `analyze`, `summarize_metrics`, `query_data` (questions about metrics / data — answered
    with a chart). args: {{"question": "<the natural-language question>"}}
- meeting-ops — `recap` (summarise the meeting so far), `action_items` (capture who-owns-what),
    `decisions` (record what was decided). Use when someone asks to recap / capture actions / note a
    decision. args: {{}} — the transcript is attached automatically; do not put it in args.
- research-agent — `research` (look something up on the live web — current facts, prices, news,
    docs, competitors). Use for anything needing up-to-date external info the codebase/graph wouldn't
    have. args: {{"question": "<the natural-language question>"}}. Prefer git-agent for questions about
    OUR codebase/meetings, and research-agent for the outside world.

One utterance may imply more than one task (e.g. "build a landing page and tell me who owns auth" → two tasks).
Rewrite each request into a clean, self-contained `question`/`brief` — the agent does not see the conversation."""

PLAN_TOOL = {
    "name": "propose_tasks",
    "description": "Propose zero or more tasks to delegate to the agent suite based on the utterance.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string", "enum": PLANNABLE_INTENTS},
                        "args": {
                            "type": "object",
                            "description": "Arguments for the agent (e.g. {'question': ...} or {'brief': ...}).",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One short phrase: why this task, for the visible activity feed.",
                        },
                    },
                    "required": ["intent", "args"],
                },
            }
        },
        "required": ["tasks"],
    },
}


async def plan_utterance(anthropic, model: str, text: str) -> list[dict]:
    """Ask the model to turn one utterance into zero or more tasks. Returns [] on nothing actionable."""
    resp = await anthropic.messages.create(
        model=model,
        max_tokens=1024,
        system=PLANNER_SYSTEM,
        tools=[PLAN_TOOL],
        tool_choice={"type": "tool", "name": "propose_tasks"},
        messages=[{"role": "user", "content": text}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "propose_tasks":
            tasks = block.input.get("tasks", []) if isinstance(block.input, dict) else []
            # keep only intents we can actually route (the enum should guarantee this, but be safe)
            return [t for t in tasks if t.get("intent") in config.TASK_TOPIC_BY_INTENT]
    return []


async def _delegate(producer, meeting_id: str, plan_id: str, spec: dict) -> str:
    """Publish one task.create (+ a 'delegated' activity so the FE shows the planner deciding)."""
    intent = spec["intent"]
    topic = config.TASK_TOPIC_BY_INTENT[intent]
    task_id = "tsk_" + uuid.uuid4().hex[:8]
    env = Envelope[TaskCreatePayload](
        type="task.create", meeting_id=meeting_id, ts=now_iso(),
        payload=TaskCreatePayload(
            task_id=task_id, intent=intent, args=spec.get("args") or {},
            idempotency_key=task_id, requested_by="planner",
            parent_task_id=plan_id, depth=1,                       # group an utterance's tasks under one plan
        ),
    )
    await publish(producer, topic, env, key=task_id)

    activity = Envelope[ActivityPayload](
        type="activity", meeting_id=meeting_id, ts=now_iso(),
        payload=ActivityPayload(task_id=task_id, status="delegated",
                                detail=spec.get("reason") or intent),
    )
    await publish(producer, config.ACTIVITY, activity, key=task_id)
    log.info("delegated %s -> %s (task %s): %s", intent, topic, task_id, spec.get("reason") or "")
    return task_id


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = config.load()

    if not s.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY unset — the planner needs an LLM to route utterances")

    from anthropic import AsyncAnthropic
    kwargs = {"api_key": s.anthropic_api_key}
    if s.anthropic_base_url:
        kwargs["base_url"] = s.anthropic_base_url
    anthropic = AsyncAnthropic(**kwargs)

    producer = await make_producer(s)

    # Both structures are bounded so a long-lived planner never grows without limit.
    seen: set[str] = set()           # envelope-id dedupe — a redelivered transcript mustn't double-delegate
    seen_order: deque[str] = deque()  # insertion order, to FIFO-evict `seen` past SEEN_MAX
    transcripts: OrderedDict[str, deque] = OrderedDict()  # per-meeting rolling window, LRU over meetings

    def first_time(env_id: str) -> bool:
        """True the first time we see an envelope id; evicts the oldest id past SEEN_MAX."""
        if env_id in seen:
            return False
        seen.add(env_id)
        seen_order.append(env_id)
        if len(seen_order) > SEEN_MAX:
            seen.discard(seen_order.popleft())
        return True

    def roll(meeting_id: str) -> deque:
        """The meeting's rolling transcript, kept as an LRU over meetings (cap MAX_MEETINGS)."""
        dq = transcripts.get(meeting_id)
        if dq is None:
            dq = transcripts[meeting_id] = deque(maxlen=TRANSCRIPT_WINDOW)
            if len(transcripts) > MAX_MEETINGS:
                transcripts.popitem(last=False)  # drop the least-recently-used meeting
        else:
            transcripts.move_to_end(meeting_id)
        return dq

    log.info("planner up — tailing %s (group=planner, bootstrap=%s)", config.TRANSCRIPT, s.kafka.bootstrap)
    try:
        async for msg in consume(config.TRANSCRIPT, group_id="planner",
                                 settings=s, auto_offset_reset="earliest"):
            try:
                env = Envelope[TranscriptPayload].model_validate_json(msg.value)
            except Exception as e:
                log.warning("dropping unparseable transcript: %s", e)
                continue

            # only act on settled, non-trivial speech — partials are noise, and they churn
            if env.type != "transcript.final" and not env.payload.is_final:
                continue
            text = (env.payload.text or "").strip()
            if len(text) < 8 or not first_time(env.id):
                continue

            # record the utterance in the meeting's rolling transcript before planning
            speaker = (env.payload.speaker.name if env.payload.speaker else None) or "Speaker"
            roll(env.meeting_id).append(f"{speaker}: {text}")

            try:
                tasks = await plan_utterance(anthropic, s.model_smart, text)
            except Exception as e:
                log.warning("planning failed for %r: %s", text[:60], e)
                continue
            if not tasks:
                continue

            plan_id = "plan_" + uuid.uuid4().hex[:8]
            for spec in tasks:
                # meeting-ops needs the whole meeting — attach the rolling transcript window
                if spec.get("intent") in OPS_INTENTS:
                    spec.setdefault("args", {})["transcript"] = "\n".join(transcripts[env.meeting_id])
                try:
                    await _delegate(producer, env.meeting_id, plan_id, spec)
                except Exception as e:
                    log.warning("delegation failed for %s: %s", spec.get("intent"), e)
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())

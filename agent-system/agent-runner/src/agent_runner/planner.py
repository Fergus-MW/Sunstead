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
import re
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
    The web-agent researches the live web ITSELF to ground factual copy, so if the site is about
    something real, put the real subject in the brief — don't split off a separate research task just
    to inform the site. args: {{"brief": "<what the site is for; name the real subject to cover>",
    "style": "<optional look & feel>"}}
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
Rewrite each request into a clean, self-contained `question`/`brief` — the agent does not see the conversation.

CRITICAL — never split research away from a site build. Tasks DON'T share results: a research task's answer \
goes to the speaker, it never reaches the web-agent. The web-agent researches the live web ITSELF. So when the \
work is "a site about X" / "look it up so the site is accurate", emit exactly ONE `build_website` task and fold \
the lookup INTO its brief — adding a separate research task would be wasted (its answer can't inform the site) AND \
the site would still need to ground itself. Two hard rules:
- A `build_website` brief MUST name the real subject and every factual requirement the speaker implied (e.g. \
"a site about the EU AI Act — research it and ground the copy in accurate, current facts"). NEVER emit a build \
with a generic brief ("a landing page") when the speaker named a concrete subject — a bare brief is what makes \
the site come out ungrounded.
- Emit a SEPARATE research/git task only when the speaker wants that answer delivered to THEM, not merely baked \
into the site. "Build a site about X and look X up" is ONE build task, not two.

Worked examples — what to propose for a range of utterances. Backchannel and ordinary chatter map to NO tasks;
a clear ask maps to one task; a compound ask maps to two. Study the boundary between conversation and a real request:

1. "yeah totally, that makes sense" → no tasks. (Backchannel / agreement — nothing to do.)
2. "haha nice, anyway where were we" → no tasks. (Smalltalk — no actionable request.)
3. "I think the rollout went fine yesterday" → no tasks. (A statement of opinion, not a request for work.)
4. "let's circle back on the pricing thing later" → no tasks. (Deferral — no work to start now.)
5. "can you build a landing page for the new launch?" → one task:
     web-agent `build_website`, args {{"brief": "A landing page for the new product launch"}}.
6. "who was the last person to touch the auth module?" → one task:
     git-agent `who_changed`, args {{"question": "Who last changed the auth module?"}}.
7. "what's the current pricing for the Aiven Kafka managed tier?" → one task:
     research-agent `research`, args {{"question": "Current pricing for Aiven's managed Kafka tier"}}.
8. "can you recap the meeting so far and capture the action items?" → two tasks:
     meeting-ops `recap` (args {{}}) and meeting-ops `action_items` (args {{}}).
9. "build a quick dashboard site and tell me who owns the metrics pipeline" → two tasks:
     web-agent `build_website`, args {{"brief": "A quick dashboard site for the metrics"}}; and
     git-agent `who_changed`, args {{"question": "Who owns the metrics pipeline?"}}.
10. "let's ship it 🚀" → no tasks. (Enthusiasm, not a delegable request.)
11. "build us a site about the new EU AI Act and look it up so the details are right" → ONE task:
     web-agent `build_website`, args {{"brief": "A site explaining the EU AI Act — research it and ground \
     the copy in accurate, current facts"}}. (The web-agent does its own web research; the lookup only \
     serves the site, so do NOT add a separate research task.)

When in doubt about whether an utterance is conversation or a request, prefer NO tasks — a spurious task is worse \
than a missed backchannel, and a genuinely-needed request will usually be restated more explicitly.

For each task you DO propose, also pick an `effort` — how much depth the request warrants, read from the speaker's \
wording. "quick" = a fast lookup or ballpark ("just check…", "quick question", "roughly"). "standard" = a normal \
request with no explicit depth signal (use when unsure). "deep" = an explicit ask for thoroughness ("dig into…", \
"research properly", "compare them all", "in depth"). Spend the least that satisfies the request; only choose \
"deep" when the speaker clearly asked for thoroughness."""

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
                        "effort": {
                            "type": "string",
                            "enum": ["quick", "standard", "deep"],
                            "description": "How much depth/cost this task warrants, from the speaker's wording. Default standard.",
                        },
                    },
                    "required": ["intent", "args"],
                },
            }
        },
        "required": ["tasks"],
    },
}


def _cached_system(text: str) -> list[dict]:
    """The system prompt as a single cacheable block — Opus 4.8 / Haiku 4.5 cache prefixes >= 4096 tokens,
    so the fattened few-shot prompt is what makes caching engage. Keep this byte-stable: no timestamps /
    meeting_id in here (those would invalidate the prefix every utterance). The per-utterance text rides in
    `messages`, after the breakpoint."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# Zero-LLM keyword pre-filter — words/phrases that plausibly signal a request. Tuned HIGH-RECALL: unsure → True.
# This only ever *skips* obvious non-requests (backchannel); anything ambiguous falls through to the gate/Opus.
_REQUEST_WORDS = {
    "build", "make", "create", "update", "change", "edit", "fix", "add", "remove", "set",
    "find", "look", "search", "lookup", "research", "check", "investigate", "pull",
    "who", "what", "when", "where", "why", "how", "which", "whose",
    "recap", "summary", "summarize", "summarise", "analyze", "analyse", "analysis",
    "show", "draft", "generate", "write", "compile", "list", "compare", "review",
    "tell", "explain", "describe", "recall", "remember", "capture", "record", "note",
    "chart", "graph", "plot", "diagram", "report", "decision", "decisions", "action", "actions",
    "recent", "changed", "owns", "owner",
}
_REQUEST_PHRASES = ("can you", "could you", "would you", "will you", "please", "pull up",
                    "look up", "let me know", "i need", "we need", "i want", "we want")


def _maybe_actionable(text: str) -> bool:
    """High-recall, zero-LLM gate: could this utterance plausibly contain a request? Unsure → True.
    Only returns False for utterances that clearly carry no request signal (e.g. 'yeah totally')."""
    low = text.lower()
    if "?" in low:
        return True
    if any(p in low for p in _REQUEST_PHRASES):
        return True
    words = set(re.findall(r"[a-z']+", low))
    return bool(words & _REQUEST_WORDS)


GATE_TOOL = {
    "name": "classify_utterance",
    "description": "Report whether this meeting utterance could be an actionable request for one of our agents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "actionable": {
                "type": "boolean",
                "description": (
                    "True if the utterance could be a request our agents can fulfil — build/update a site, "
                    "a question about our codebase/history, a data/metrics chart, a meeting recap / action-items / "
                    "decisions capture, or live web research. False for backchannel, smalltalk, opinions, or chatter."
                ),
            }
        },
        "required": ["actionable"],
    },
}

GATE_SYSTEM = """You are a fast pre-filter for Sunstead's planner. You see one utterance from a live meeting \
transcript (with a little preceding context) and decide ONE thing: could it be an actionable request for one of \
our specialist agents — build/update a website, a question about our codebase or its history, a data/metrics chart, \
a meeting recap / action-items / decisions capture, or live web research?

Answer True if it plausibly could be such a request; answer False for ordinary conversation: backchannel \
("yeah totally"), smalltalk, opinions, agreement, or thinking out loud. When genuinely unsure, answer True — a \
heavier model downstream makes the real decision, so a false True is cheap but a false False drops a real request."""


async def gate_utterance(anthropic, model_fast: str, text: str, recent_context: str = "") -> bool:
    """Cheap Haiku forced-tool classify: could this utterance be an actionable request? Returns a bool.

    Fails OPEN: any exception, or an unreadable/ambiguous response, returns True so the utterance still
    reaches the Opus planner. Haiku does NOT support output_config.effort, so we don't pass it."""
    user = text if not recent_context else f"Recent context:\n{recent_context}\n\nUtterance to classify:\n{text}"
    try:
        resp = await anthropic.messages.create(
            model=model_fast,
            max_tokens=256,
            system=_cached_system(GATE_SYSTEM),
            tools=[GATE_TOOL],
            tool_choice={"type": "tool", "name": "classify_utterance"},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        log.warning("gate failed for %r (failing open to Opus): %s", text[:60], e)
        return True
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "classify_utterance":
            if isinstance(block.input, dict) and isinstance(block.input.get("actionable"), bool):
                return block.input["actionable"]
    # couldn't read a clear answer — fail open to Opus
    return True


async def plan_utterance(anthropic, model: str, text: str) -> list[dict]:
    """Ask the model to turn one utterance into zero or more tasks. Returns [] on nothing actionable."""
    resp = await anthropic.messages.create(
        model=model,
        max_tokens=1024,
        system=_cached_system(PLANNER_SYSTEM),
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
    # the model picks effort under an enum, but coerce defensively — an off-enum value must not
    # fail the whole delegation; fall back to the capable middle tier.
    effort = spec.get("effort") if spec.get("effort") in ("quick", "standard", "deep") else "standard"
    env = Envelope[TaskCreatePayload](
        type="task.create", meeting_id=meeting_id, ts=now_iso(),
        payload=TaskCreatePayload(
            task_id=task_id, intent=intent, args=spec.get("args") or {},
            idempotency_key=task_id, requested_by="planner",
            parent_task_id=plan_id, depth=1,                       # group an utterance's tasks under one plan
            effort=effort,
        ),
    )
    await publish(producer, topic, env, key=task_id)

    activity = Envelope[ActivityPayload](
        type="activity", meeting_id=meeting_id, ts=now_iso(),
        payload=ActivityPayload(task_id=task_id, status="delegated",
                                detail=spec.get("reason") or intent),
    )
    await publish(producer, config.ACTIVITY, activity, key=task_id)
    log.info("delegated %s [effort=%s] -> %s (task %s): %s", intent, effort, topic, task_id, spec.get("reason") or "")
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

            # record the utterance in the meeting's rolling transcript before planning — even non-actionable
            # utterances are meeting-ops context (recap/action-items see them), so this must run first.
            speaker = (env.payload.speaker.name if env.payload.speaker else None) or "Speaker"
            dq = roll(env.meeting_id)
            dq.append(f"{speaker}: {text}")

            # Two cheap gates before the expensive Opus call, each failing OPEN to Opus:
            # 1) zero-LLM keyword pre-filter — skip utterances with no request signal at all (e.g. "yeah totally").
            if not _maybe_actionable(text):
                continue
            # 2) Haiku fast-classify — only spend Opus when this plausibly is an actionable request. The last
            #    1-2 buffered utterances are passed as context so the gate sees the immediate conversation.
            recent_context = "\n".join(list(dq)[-2:])
            if not await gate_utterance(anthropic, s.model_fast, text, recent_context):
                continue

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

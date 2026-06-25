"""Mock meeting — simulate the whole Google-Meet pipe in one terminal, no Recall/LiveKit.

Type what someone "says" in the meeting; each line is published as a `meeting.transcript`
(exactly what the avatar's STT would emit). The **planner** reads it, decides what's
actionable, and delegates to the agent suite; this script also tails `agent.activity` +
`agent.results` and prints them — so you see the planner delegating and the agents
returning, live, without opening the FE.

    # Terminal 1: make up && make topics        (redpanda + topics)
    # Terminal 2: make run                       (agent-runner — the specialists)
    # Terminal 3: make planner                   (the delegation brain)
    # Terminal 4: make mock                       (this — speak + watch)
    #   (optional) make gateway + the FE dashboard for the visual version

    you> build us a dark landing page for Sunstead and tell me who last touched auth

Needs: ANTHROPIC_API_KEY (planner) (+ AIVEN_TOKEN for git/data questions). This is the
local stand-in for the realtime avatar — same Kafka seam, same planner, just text I/O.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from shared import config
from shared.contracts import Envelope, Speaker, TranscriptPayload
from shared.harness import now_iso
from shared.kafka import consume, make_producer, publish


def _fmt(raw: bytes) -> str | None:
    """Render an agent.activity / agent.results envelope as one readable line."""
    try:
        env = json.loads(raw)
    except ValueError:
        return None
    p = env.get("payload", {}) or {}
    typ = env.get("type", "")
    task = p.get("task_id", "?")
    if typ == "activity":
        return f"  · [{task}] {p.get('status','')}" + (f" — {p.get('detail')}" if p.get("detail") else "")
    if typ in ("task.completed", "task.failed"):
        mark = "✓" if typ == "task.completed" else "✗"
        bits = [f"  {mark} [{task}] {p.get('status', typ.split('.')[-1])}"]
        for a in p.get("artifacts", []) or []:
            bits.append(f"      → {a.get('kind','')}: {a.get('value','')}")
        if p.get("error"):
            bits.append(f"      ! {p['error']}")
        return "\n".join(bits)
    return None


async def _watch(meeting_id: str, settings) -> None:
    """Tail results + activity for this meeting and print them as they arrive."""
    group = f"mock-meeting-{uuid.uuid4().hex[:8]}"  # unique group → broadcast (see everything)
    async for msg in consume(config.RESULTS, config.ACTIVITY, group_id=group,
                             settings=settings, auto_offset_reset="latest"):
        try:
            env = json.loads(msg.value)
        except ValueError:
            continue
        if env.get("meeting_id") not in (meeting_id, None):
            continue
        line = _fmt(msg.value)
        if line:
            print(line, flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Speak into a mock meeting and watch the agents work.")
    ap.add_argument("--meeting", default="mtg_dev", help="meeting_id (match the FE filter to see it there)")
    ap.add_argument("--speaker", default="Operator")
    a = ap.parse_args()

    settings = config.load()
    producer = await make_producer(settings)
    watcher = asyncio.create_task(_watch(a.meeting, settings))

    print(f"mock meeting '{a.meeting}' — type what's said; blank line or Ctrl-C to leave.\n")
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                text = (await loop.run_in_executor(None, input, "you> ")).strip()
            except EOFError:
                break
            if not text:
                break
            env = Envelope[TranscriptPayload](
                type="transcript.final", meeting_id=a.meeting, ts=now_iso(),
                payload=TranscriptPayload(speaker=Speaker(name=a.speaker), text=text, is_final=True),
            )
            await publish(producer, config.TRANSCRIPT, env, key=a.meeting)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        watcher.cancel()
        await producer.stop()
        print("\nleft the meeting.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

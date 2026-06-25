"""Speak a line into a meeting — publish a `meeting.transcript` (stands in for the avatar's STT).

Lets you drive the whole delegation loop today, before the avatar is merged:

    # Terminal 1: make run     (agent-runner)
    # Terminal 2: make planner  (the orchestration brain)
    # Terminal 3: make gateway  (so the FE / WS sees results)
    uv run python scripts/say.py --text "can you build us a dark landing page for Sunstead?"
    uv run python scripts/say.py --text "and who last touched the auth module?"
    make say T="build a site and tell me who owns the messages client"

The planner reads it, decides what's actionable, and delegates to the agent suite.
"""

from __future__ import annotations

import argparse
import asyncio

from shared import config
from shared.contracts import Envelope, Speaker, TranscriptPayload
from shared.harness import now_iso
from shared.kafka import make_producer, publish


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="what was said")
    ap.add_argument("--meeting", default="mtg_dev")
    ap.add_argument("--speaker", default="Operator")
    a = ap.parse_args()

    env = Envelope[TranscriptPayload](
        type="transcript.final", meeting_id=a.meeting, ts=now_iso(),
        payload=TranscriptPayload(speaker=Speaker(name=a.speaker), text=a.text, is_final=True),
    )

    s = config.load()
    producer = await make_producer(s)
    await publish(producer, config.TRANSCRIPT, env, key=a.meeting)
    await producer.stop()
    print(f"said ({a.speaker} @ {a.meeting}): {a.text!r} -> {config.TRANSCRIPT}")


if __name__ == "__main__":
    asyncio.run(main())

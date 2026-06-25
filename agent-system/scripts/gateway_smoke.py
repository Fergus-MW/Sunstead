"""No-Kafka smoke test for the gateway's WS fan-out (agent_runner.gateway.Hub).

Proves the three properties the dashboard depends on, in-process, with no broker and no server:
  1. REPLAY   — a subscriber that connects *after* a result was produced still receives it (ring buffer).
  2. NO DUP   — a message in the snapshot/live overlap window is delivered exactly once (envelope-id dedupe).
  3. FILTER   — the per-socket `meeting_id` filter drops other meetings on both replay and live.

    uv run python scripts/gateway_smoke.py        # exits non-zero on failure
"""

from __future__ import annotations

import asyncio
import json

from agent_runner.gateway import Hub
from shared import config


def _item(meeting_id: str, eid: str, text: str | None = None):
    """A Hub fan-out item: (meeting_id, envelope_id, raw_json_text)."""
    return (meeting_id, eid, text or json.dumps({"id": eid, "meeting_id": meeting_id}))


async def _collect(gen, n: int, timeout: float = 1.0) -> list[str]:
    """Pull up to n frames from an async generator, stopping early on timeout."""
    out: list[str] = []
    for _ in range(n):
        try:
            out.append(await asyncio.wait_for(gen.__anext__(), timeout))
        except (asyncio.TimeoutError, StopAsyncIteration):
            break
    return out


def _ids(frames: list[str]) -> list[str]:
    return [json.loads(f)["id"] for f in frames]


async def test_replay_and_no_dup() -> None:
    hub = Hub(config.Settings())

    # A result is produced BEFORE anyone is listening — it lands in the ring.
    hub._fanout(_item("m1", "a"))

    gen = hub.stream_for("m1")            # subscriber connects late
    # Drive the first step so the generator registers its queue + replays the ring.
    first = await asyncio.wait_for(gen.__anext__(), 1.0)
    assert json.loads(first)["id"] == "a", "REPLAY failed: late subscriber missed the buffered result"

    # Now produce two live ones; 'b' is brand new, and we re-emit 'a' to simulate snapshot/live overlap.
    hub._fanout(_item("m1", "b"))
    hub._fanout(_item("m1", "a"))         # duplicate of the replayed id → must be dropped once

    live = _ids(await _collect(gen, 5))
    assert live == ["b"], f"NO-DUP failed: expected ['b'], got {live}"
    await gen.aclose()
    print("ok  replay + no-dup")


async def test_meeting_filter() -> None:
    hub = Hub(config.Settings())
    hub._fanout(_item("m1", "x"))
    hub._fanout(_item("m2", "y"))         # other meeting — must never reach an m1 socket

    gen = hub.stream_for("m1")
    frames = _ids(await _collect(gen, 3))
    assert frames == ["x"], f"FILTER failed: expected ['x'], got {frames}"
    await gen.aclose()
    print("ok  meeting_id filter")


async def test_fanout_to_many() -> None:
    hub = Hub(config.Settings())
    g1, g2 = hub.stream_for(None), hub.stream_for(None)
    # Starting a collect task drives each generator to its first `await q.get()`, registering the queue.
    t1 = asyncio.create_task(_collect(g1, 1))
    t2 = asyncio.create_task(_collect(g2, 1))
    await asyncio.sleep(0.05)             # let both register
    hub._fanout(_item("m1", "z"))        # one broadcast reaches every socket
    r1, r2 = await t1, await t2
    assert _ids(r1) == ["z"] and _ids(r2) == ["z"], f"FANOUT failed: {r1=} {r2=}"
    await g1.aclose()
    await g2.aclose()
    print("ok  fan-out to many sockets")


async def main() -> None:
    await test_replay_and_no_dup()
    await test_meeting_filter()
    await test_fanout_to_many()
    print("\nPASS - gateway WS fan-out behaves (replay, no-dup, filter, fan-out)")


if __name__ == "__main__":
    asyncio.run(main())

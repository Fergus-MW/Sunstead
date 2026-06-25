"""Stream an Anthropic completion to Kafka as reasoning/output deltas (docs/AGENT_SYSTEM.md §3.5).

The design goal §3.5 calls for is "stream LLM output … so the first useful token reaches the
human as early as possible." This helper does that: it runs a streaming `messages.stream()` call,
forwards both **adaptive-thinking** deltas (summarized chain-of-thought) and **output text** deltas
to `agent.trace` via `ctx.trace(phase, delta)`, and returns the final assistant text.

Deltas are coalesced into ~CHUNK_CHARS-sized chunks before publishing — a Kafka message per token
would flood the bus and the per-socket queues for no visible benefit. We flush on a size threshold,
on a phase change (thinking↔text), and at each content-block boundary.

Model note: the agents run on Claude Opus 4.8, so thinking is **adaptive** (`{"type": "adaptive"}`)
with `display="summarized"` to actually surface reasoning text — `budget_tokens` is rejected (400)
on 4.7/4.8, and the default `display="omitted"` would stream empty thinking blocks.
"""

from __future__ import annotations

from .harness import TaskCtx

CHUNK_CHARS = 48  # flush a trace chunk once the buffer crosses this many chars


async def stream_completion(
    ctx: TaskCtx,
    *,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 16000,
    effort: str = "medium",
    thinking: bool = True,
) -> str:
    """Stream a single-shot completion, emitting trace deltas; return the final text."""
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "output_config": {"effort": effort},
    }
    if thinking:
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

    buf: list[str] = []
    buf_phase: str | None = None

    async def flush() -> None:
        nonlocal buf, buf_phase
        if buf and buf_phase:
            await ctx.trace(buf_phase, "".join(buf))
        buf = []

    async with ctx.anthropic.messages.stream(**kwargs) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                d = event.delta
                if d.type == "thinking_delta":
                    phase, chunk = "thinking", d.thinking
                elif d.type == "text_delta":
                    phase, chunk = "text", d.text
                else:
                    continue
                if phase != buf_phase:        # don't mix reasoning and answer in one chunk
                    await flush()
                    buf_phase = phase
                buf.append(chunk)
                if sum(len(x) for x in buf) >= CHUNK_CHARS:
                    await flush()
            elif event.type == "content_block_stop":
                await flush()
                buf_phase = None
        await flush()
        final = await stream.get_final_message()

    return "".join(b.text for b in final.content if b.type == "text")

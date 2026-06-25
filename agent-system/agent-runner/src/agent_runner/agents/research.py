"""research agent — answer a question from the live web with Claude's server-side tools.

Unlike the KG agent (which reads the Sunstead graph) this agent reaches the open internet via
Anthropic's **server-side** web_search + web_fetch tools (docs/AGENT_SYSTEM.md §9). Claude writes
its own queries, reads results, follows up with fetches, and synthesises a cited answer — the
search loop runs server-side, so there's no client-side tool execution here. We just stream the
reasoning/output to agent.trace and surface the cited sources as URL artifacts.

Model note: web_search_20260209 / web_fetch_20260209 (dynamic filtering) require Opus 4.8/4.7/4.6
or Sonnet 4.6 — we run on model_smart (Opus 4.8). A long server loop returns stop_reason
"pause_turn"; we re-send to continue (bounded by MAX_TURNS).
"""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx

RESEARCH_SYSTEM = """You are the research agent for Sunstead — an AI employee in a live meeting. \
You answer a question using the live web. Search with precise, well-chosen queries; read the most \
credible sources; fetch a page when the snippet isn't enough. Cross-check important claims across \
sources. Then answer in a few clear sentences (or a short list), grounded ONLY in what the sources \
say — cite them. If the web doesn't settle it, say so plainly rather than guessing."""

# Server-side tools (no beta header). web_fetch only fetches URLs already surfaced (e.g. by search).
WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5, "citations": {"enabled": True}},
]

CHUNK_CHARS = 48
MAX_TURNS = 6  # pause_turn continuations before we stop


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("research agent requires ANTHROPIC_API_KEY")

    question = (
        task.args.get("question") or task.args.get("brief")
        or task.args.get("q") or task.args.get("text") or task.intent
    )
    await ctx.activity("researching", question)

    messages: list[dict] = [{"role": "user", "content": question}]
    answer = ""
    sources: list[dict] = []

    for _ in range(MAX_TURNS):
        buf: list[str] = []
        buf_phase: str | None = None

        async def flush() -> None:
            nonlocal buf, buf_phase
            if buf and buf_phase:
                await ctx.trace(buf_phase, "".join(buf))
            buf = []

        async with ctx.anthropic.messages.stream(
            model=ctx.settings.model_smart,
            max_tokens=8000,
            system=RESEARCH_SYSTEM,
            tools=WEB_TOOLS,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "high"},
            messages=messages,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "server_tool_use":
                        await flush()
                        buf_phase = None
                        await ctx.activity("searching the web", getattr(block, "name", None))
                elif event.type == "content_block_delta":
                    d = event.delta
                    if d.type == "thinking_delta":
                        phase, chunk = "thinking", d.thinking
                    elif d.type == "text_delta":
                        phase, chunk = "text", d.text
                    else:
                        continue
                    if phase != buf_phase:
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

        # The synthesis is the LAST text block; earlier ones are "let me search…" preamble.
        text_blocks = [b.text for b in final.content if b.type == "text" and b.text.strip()]
        if text_blocks:
            answer = text_blocks[-1].strip()
        for b in final.content:                     # gather cited sources (web search auto-cites)
            for c in getattr(b, "citations", None) or []:
                url = getattr(c, "url", None)
                if url and not any(s["url"] == url for s in sources):
                    sources.append({"url": url, "title": getattr(c, "title", None) or url})

        if final.stop_reason == "pause_turn":         # server loop not done — continue it
            messages.append({"role": "assistant", "content": final.content})
            continue
        break

    result: dict = {"question": question, "answer": answer}
    if sources:
        result["sources"] = sources
        result["artifacts"] = [{"kind": "url", "value": s["url"]} for s in sources[:5]]
    return result

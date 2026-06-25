"""Shared server-side web-search loop — grounding via Claude's `web_search` / `web_fetch` tools.

Two agents need the *same* capability: the **research** agent answers a question from the live web,
and the **web** agent grounds the copy it writes in real facts (so "build a site about X" doesn't
hallucinate X). Rather than duplicate the gnarly bits — the `pause_turn` continuation loop, the
HTTP-200-embedded tool errors, and citation/source harvesting — they live here once.

`grounded_stream()` runs a streaming completion that MAY call the server-side web tools, streams
thinking/text deltas to `agent.trace` (docs/AGENT_SYSTEM.md §3.5), follows the server loop across
`pause_turn`s, and returns `(final_text, sources, timed_out)`. `final_text` is the LAST text block
(the synthesis, or the HTML the web-agent emits); `sources` are the cited-or-fetched URLs. The web
tools require Sonnet/Opus (never Haiku) — the caller picks the model.
"""

from __future__ import annotations

import asyncio

CHUNK_CHARS = 48  # coalesce trace deltas to ~this many chars before publishing (cf. streaming.py)


def web_tools(searches: int, fetches: int) -> list[dict]:
    """Server-side tools (no beta header), budgeted by effort. web_fetch only fetches URLs already
    surfaced (e.g. by search)."""
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches, "citations": {"enabled": True}},
    ]


def block_error(block) -> str | None:
    """Server tools return HTTP 200 with an embedded error: a web_search/web_fetch_tool_result block
    whose .content is (or carries) an error/error_code (e.g. max_uses_exceeded). SDK shapes vary, so
    probe defensively and return the error_code (or "error") if this block is an error result."""
    if getattr(block, "type", None) not in ("web_search_tool_result", "web_fetch_tool_result"):
        return None
    content = getattr(block, "content", None)
    err = getattr(content, "error", None) or getattr(content, "error_code", None)
    code = getattr(content, "error_code", None) or getattr(err, "error_code", None)
    if err is not None or code is not None or (content is not None and getattr(content, "type", "").endswith("_error")):
        return code or getattr(content, "type", None) or "error"
    return None


def _fetch_url_map(content) -> tuple[dict[int, str], dict[str, str]]:
    """Map web_fetch_tool_result blocks → urls, keyed by document_index (position) and document_title,
    so we can resolve the answer's char_location citations (which carry index/title but no .url)."""
    by_index: dict[int, str] = {}
    by_title: dict[str, str] = {}
    idx = 0
    for b in content:
        if getattr(b, "type", None) != "web_fetch_tool_result":
            continue
        result = getattr(b, "content", None)
        url = getattr(result, "url", None) or getattr(b, "url", None)
        doc = getattr(result, "document", None)
        title = getattr(doc, "title", None) or getattr(result, "title", None)
        if url:
            by_index[idx] = url
            if title:
                by_title[title] = url
        idx += 1
    return by_index, by_title


def result_urls(content) -> list[dict]:
    """Fallback grounding: the URLs the model actually searched/fetched. With dynamic filtering the
    synthesis text often carries NO citations, so harvesting citations alone yields nothing."""
    out: list[dict] = []

    def _add(url, title) -> None:
        if url and not any(s["url"] == url for s in out):
            out.append({"url": url, "title": title or url})

    for b in content:
        t = getattr(b, "type", None)
        result = getattr(b, "content", None)
        if t == "web_fetch_tool_result":
            doc = getattr(result, "document", None)
            _add(getattr(result, "url", None) or getattr(b, "url", None),
                 getattr(doc, "title", None) or getattr(result, "title", None))
        elif t == "web_search_tool_result" and isinstance(result, list):
            for r in result:
                _add(getattr(r, "url", None), getattr(r, "title", None))
    return out


def harvest(content, sources: list[dict]) -> None:
    """Collect cited sources from a final message into `sources` (deduped by url). PRIMARY: text-block
    citations (web_search → url; web_fetch → char_location resolved via the fetch map). FALLBACK: if
    nothing was cited at all, use the URLs the model actually searched/fetched — better a grounded
    source list than an empty one. Defensive getattr throughout (SDK shapes vary)."""
    by_index, by_title = _fetch_url_map(content)
    cited = False
    for b in content:
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if not url:                                   # char_location → resolve via fetch map
                di = getattr(c, "document_index", None)
                if di is not None:
                    url = by_index.get(di)
                if not url:
                    url = by_title.get(getattr(c, "document_title", None))
            if url and not any(s["url"] == url for s in sources):
                cited = True
                title = getattr(c, "title", None) or getattr(c, "document_title", None) or url
                sources.append({"url": url, "title": title})
    if not cited and not sources:                         # nothing cited → use searched/fetched URLs
        sources.extend(result_urls(content))


async def grounded_stream(
    ctx,
    *,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
    thinking_effort: str = "medium",
    max_tokens: int = 8000,
    max_turns: int = 4,
    timeout: float | None = None,
) -> tuple[str, list[dict], bool]:
    """Stream a (possibly web-grounded) completion to `agent.trace`, following the server loop across
    `pause_turn`s. Returns (final_text, sources, timed_out). `messages` is mutated (assistant turns are
    appended on continuation). On timeout the best answer/sources gathered so far are returned — the
    call never hangs or raises out (the realtime tempo rule, §3.5)."""
    sources: list[dict] = []
    state = {"answer": ""}

    async def _loop() -> None:
        for _ in range(max_turns):
            buf: list[str] = []
            buf_phase: str | None = None

            async def flush() -> None:
                nonlocal buf, buf_phase
                if buf and buf_phase:
                    await ctx.trace(buf_phase, "".join(buf))
                buf = []

            async with ctx.anthropic.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": thinking_effort},
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

            # The synthesis (or the HTML) is the LAST text block; earlier ones are "let me search…".
            text_blocks = [b.text for b in final.content if b.type == "text" and b.text.strip()]
            if text_blocks:
                state["answer"] = text_blocks[-1].strip()
            harvest(final.content, sources)

            # Web tools fail silently as HTTP-200 error result blocks (e.g. max_uses_exceeded). On any
            # such error, stop continuing rather than burning the remaining turns re-hitting the wall.
            errored = next((e for e in (block_error(b) for b in final.content) if e), None)
            if errored:
                await ctx.activity("search issue", errored)
                break

            if final.stop_reason == "pause_turn":         # server loop not done — continue it
                messages.append({"role": "assistant", "content": final.content})
                continue
            break

    if timeout:
        try:
            await asyncio.wait_for(_loop(), timeout=timeout)
        except asyncio.TimeoutError:
            await ctx.activity("web search timed out", f"{timeout:g}s")
            return state["answer"], sources, True
    else:
        await _loop()
    return state["answer"], sources, False

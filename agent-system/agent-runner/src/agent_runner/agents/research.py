"""research agent — answer a question from the live web with Claude's server-side tools.

Unlike the KG agent (which reads the Sunstead graph) this agent reaches the open internet via
Anthropic's **server-side** web_search + web_fetch tools (docs/AGENT_SYSTEM.md §9). Claude writes
its own queries, reads results, follows up with fetches, and synthesises a cited answer — the
search loop runs server-side, so there's no client-side tool execution here. We just stream the
reasoning/output to agent.trace and surface the cited sources as URL artifacts.

Model note: web_search_20260209 / web_fetch_20260209 (dynamic filtering) require Opus 4.8/4.7/4.6
or Sonnet 4.6 — so we run the mid tier (Sonnet 4.6) for quick/standard effort and escalate to the
smart tier (Opus 4.8) only for deep. The search budget, thinking effort, and turn limit all scale
with the task's effort (shared.effort). A long server loop returns stop_reason "pause_turn"; we
re-send to continue (bounded by the effort's max_turns).
"""

from __future__ import annotations

import asyncio

from shared.contracts import TaskCreatePayload
from shared.effort import policy_for
from shared.harness import TaskCtx, first_arg

RESEARCH_SYSTEM = """You are the research agent for Sunstead — an AI employee in a live meeting. \
You answer a question using the live web. Search with precise, well-chosen queries; read the most \
credible sources; fetch a page when the snippet isn't enough. Cross-check important claims across \
sources. Then answer in a few clear sentences (or a short list), grounded ONLY in what the sources \
say — cite them. If the web doesn't settle it, say so plainly rather than guessing."""

CHUNK_CHARS = 48
RUN_TIMEOUT_S = 120  # whole-run ceiling across all pause_turn continuations


def _web_tools(searches: int, fetches: int) -> list[dict]:
    """Server-side tools (no beta header), budgeted by effort. web_fetch only fetches URLs already
    surfaced (e.g. by search)."""
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches, "citations": {"enabled": True}},
    ]


def _block_error(block) -> str | None:
    """Server tools return HTTP 200 with an embedded error: a web_search/web_fetch_tool_result block
    whose .content is (or carries) an error/error_code (e.g. max_uses_exceeded). SDK shapes vary, so
    probe defensively and return the error_code (or "error") if this block is an error result."""
    if getattr(block, "type", None) not in ("web_search_tool_result", "web_fetch_tool_result"):
        return None
    content = getattr(block, "content", None)
    err = getattr(content, "error", None) or getattr(content, "error_code", None)
    code = getattr(content, "error_code", None) or getattr(err, "error_code", None)
    # An error content is an object (not the success list); treat a bare error_code/type as the signal.
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


def _result_urls(content) -> list[dict]:
    """Fallback grounding: the URLs the model actually searched/fetched. With dynamic filtering the
    synthesis text often carries NO citations (the code_execution path summarises filtered results),
    so harvesting citations alone yields nothing. web_fetch_tool_result.content has a .url; a success
    web_search_tool_result.content is a LIST of web_search_result (each with .url/.title) — an error
    content is an object, which has no .url, so it's naturally skipped here."""
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


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("research agent requires ANTHROPIC_API_KEY")

    question = first_arg(task.args, "question", "brief", "q", "text") or task.intent
    # Scale the web-research budget to the effort the planner picked (shared.effort): a "quick" price
    # check spends 2 searches on Sonnet at low thinking; a "deep" dive spends 8 on Opus at high
    # thinking. The web tools require Sonnet/Opus (never Haiku), so quick/standard run the mid tier
    # and only "deep" escalates to the smart tier.
    pol = policy_for(task.effort)
    model = ctx.settings.model_smart if task.effort == "deep" else ctx.settings.model_mid
    web_tools = _web_tools(pol.web_max_searches, pol.web_max_fetches)
    await ctx.activity("researching", f"{question} ({task.effort})")

    messages: list[dict] = [{"role": "user", "content": question}]
    answer = ""
    sources: list[dict] = []
    timed_out = False

    def _harvest(content) -> None:
        """Collect cited sources from a final message. PRIMARY: text-block citations — web_search
        citations are web_search_result_location (have .url); web_fetch citations are char_location
        (carry document_index/document_title but NO .url), resolved against the web_fetch_tool_result
        urls. FALLBACK: with dynamic filtering the synthesis text often carries no citations at all,
        so if nothing was cited, fall back to the URLs the model actually searched/fetched — better
        a grounded source list than an empty one. Defensive getattr throughout (SDK shapes vary)."""
        by_index, by_title = _fetch_url_map(content)
        cited = False
        for b in content:
            for c in getattr(b, "citations", None) or []:
                url = getattr(c, "url", None)
                if not url:                                  # char_location → resolve via fetch map
                    di = getattr(c, "document_index", None)
                    if di is not None:
                        url = by_index.get(di)
                    if not url:
                        url = by_title.get(getattr(c, "document_title", None))
                if url and not any(s["url"] == url for s in sources):
                    cited = True
                    title = getattr(c, "title", None) or getattr(c, "document_title", None) or url
                    sources.append({"url": url, "title": title})
        if not cited and not sources:                        # nothing cited → use searched/fetched URLs
            sources.extend(_result_urls(content))

    async def _inner() -> None:
        nonlocal answer, messages
        for _ in range(pol.max_turns):
            buf: list[str] = []
            buf_phase: str | None = None

            async def flush() -> None:
                nonlocal buf, buf_phase
                if buf and buf_phase:
                    await ctx.trace(buf_phase, "".join(buf))
                buf = []

            async with ctx.anthropic.messages.stream(
                model=model,
                max_tokens=8000,
                system=RESEARCH_SYSTEM,
                tools=web_tools,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": pol.thinking_effort},
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
            _harvest(final.content)                          # gather cited sources

            # Web tools fail silently as HTTP-200 error result blocks (e.g. max_uses_exceeded). On
            # any such error, stop continuing the pause_turn loop and synthesise from what we have
            # rather than burning the remaining turns re-hitting the same wall.
            errored = next((e for e in (_block_error(b) for b in final.content) if e), None)
            if errored:
                await ctx.activity("search issue", errored)
                break

            if final.stop_reason == "pause_turn":            # server loop not done — continue it
                messages.append({"role": "assistant", "content": final.content})
                continue
            break

    try:
        await asyncio.wait_for(_inner(), timeout=RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        # Never hang or raise out of run() — return the best answer/sources gathered so far.
        timed_out = True
        await ctx.activity("research timed out", f"{RUN_TIMEOUT_S}s")

    result: dict = {"question": question, "answer": answer}
    if timed_out:
        result["timed_out"] = True
    if sources:
        result["sources"] = sources
        result["artifacts"] = [{"kind": "url", "value": s["url"]} for s in sources[:5]]
    return result

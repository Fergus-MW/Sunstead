"""research agent — answer a question from the live web with Claude's server-side tools.

Unlike the KG agent (which reads the Sunstead graph) this agent reaches the open internet via
Anthropic's **server-side** web_search + web_fetch tools (docs/AGENT_SYSTEM.md §9). The search loop
runs server-side; we just stream the reasoning/output to agent.trace and surface the cited sources
as URL artifacts. The loop itself — pause_turn continuation, HTTP-200 tool errors, and citation
harvesting — lives in `shared.websearch` (shared with the web-agent's grounded build).

Model note: web_search_20260209 / web_fetch_20260209 require Opus 4.8/4.7/4.6 or Sonnet 4.6 — so we
run the mid tier (Sonnet 4.6) for quick/standard effort and escalate to the smart tier (Opus 4.8)
only for deep. The search budget, thinking effort, and turn limit all scale with effort (shared.effort).
"""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.effort import policy_for
from shared.harness import TaskCtx, first_arg
from shared.ingest import Source, persist
from shared.websearch import grounded_stream, web_tools

RESEARCH_SYSTEM = """You are the research agent for Sunstead — an AI employee in a live meeting. \
You answer a question using the live web. Search with precise, well-chosen queries; read the most \
credible sources; fetch a page when the snippet isn't enough. Cross-check important claims across \
sources. Then answer in a few clear sentences (or a short list), grounded ONLY in what the sources \
say — cite them. If the web doesn't settle it, say so plainly rather than guessing."""

RUN_TIMEOUT_S = 120  # whole-run ceiling across all pause_turn continuations


async def _write_back(ctx: TaskCtx, meeting_id: str, question: str, answer: str,
                      sources: list[dict]) -> tuple[int, int]:
    """Best-effort: persist the finding into the KG so a live web lookup becomes durable team
    memory (the flywheel — docs/DESIGN.md §6/§7), via the universal ingestor's `persist()` so
    keying + entity-resolution live in ONE place. Builds a `meeting` node, one `research_finding`
    (an episode → scope-keyed `{meeting_id}::{slug}` so re-asking elsewhere stays distinct), a
    `source_document` per cited URL (an entity → canonicalised by URL, so two findings citing the
    same page share one node), and `in_meeting` / `derived_from` edges. Returns (nodes, edges).

    `uri` is left unset on the Source because research cites *several* URLs, not one — so we pass
    those source_documents explicitly rather than letting persist() invent a single anchor."""
    cited = [s for s in (sources or []) if s.get("url")][:8]
    nodes: list[dict] = [
        {"type": "meeting", "name": meeting_id, "properties": {"title": meeting_id}},
        {"type": "research_finding", "name": question, "properties": {
            "title": question[:200], "question": question,
            "answer": (answer or "")[:2000], "source_urls": [s["url"] for s in cited]}},
    ]
    edges: list[dict] = [
        {"source": ("research_finding", question), "target": ("meeting", meeting_id),
         "type": "in_meeting", "properties": {}},
    ]
    for s in cited:
        nodes.append({"type": "source_document", "name": s["url"],
                      "properties": {"title": s.get("title") or s["url"], "url": s["url"]}})
        edges.append({"source": ("research_finding", question), "target": ("source_document", s["url"]),
                      "type": "derived_from", "properties": {}})

    res = await persist(ctx, Source(kind="research", scope=meeting_id), nodes, edges)
    return res["nodes"], res["edges"]


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
    await ctx.activity("researching", f"{question} ({task.effort})")

    messages: list[dict] = [{"role": "user", "content": question}]
    answer, sources, timed_out = await grounded_stream(
        ctx, model=model, system=RESEARCH_SYSTEM, messages=messages,
        tools=web_tools(pol.web_max_searches, pol.web_max_fetches),
        thinking_effort=pol.thinking_effort, max_tokens=8000, max_turns=pol.max_turns,
        timeout=RUN_TIMEOUT_S,
    )

    result: dict = {"question": question, "answer": answer}
    if timed_out:
        result["timed_out"] = True
    if sources:
        result["sources"] = sources
        result["artifacts"] = [{"kind": "url", "value": s["url"]} for s in sources[:5]]

    # Flywheel: write the finding back into the KG so this lookup becomes durable team memory
    # (and more MCP write surface). Best-effort and gated on a real answer — never persist an
    # empty finding, and never let a write hiccup fail the already-emitted research result.
    if ctx.mcp is not None and answer.strip():
        await ctx.activity("writing finding to the graph")
        nodes_written, edges_written = await _write_back(ctx, ctx.meeting_id, question, answer, sources)
        if nodes_written or edges_written:
            result["nodes_written"] = nodes_written
            result["edges_written"] = edges_written
            await ctx.activity("graph updated", f"{nodes_written} node(s), {edges_written} edge(s)")

    return result

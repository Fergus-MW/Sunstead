"""Knowledge-graph agent — answers questions from the Sunstead KG via Aiven MCP (docs/AGENT_SYSTEM.md §9).

Despite the filename (it's registered for the git/* intents), this is a **general** graph-query agent: the
graph holds code *and* meeting/decision/people knowledge, and the same `aiven_pg_read` loop answers both.
Target project/service/db are env-driven (KG_PROJECT / KG_SERVICE / KG_DB) so it isn't pinned to one repo's graph.

Fast path: one streamed model turn that writes SQL and calls `aiven_pg_read`. (Later optimization: programmatic
templated SQL for the common intents, skipping the LLM for retrieval — §3.5.)
"""

from __future__ import annotations

import os

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx

KG_PROJECT = os.getenv("KG_PROJECT", "jq01")
KG_SERVICE = os.getenv("KG_SERVICE", "central-kg-pg")
KG_DB = os.getenv("KG_DB", "defaultdb")

KG_SYSTEM = f"""You answer questions from the Sunstead knowledge graph — a property graph in Aiven PostgreSQL — \
using the `aiven_pg_read` tool. It holds both a seeded codebase AND meeting/decision knowledge; answer either.

aiven_pg_read target: project "{KG_PROJECT}", service_name "{KG_SERVICE}", database "{KG_DB}".

Schema (a generic property graph):
  nodes(id uuid, type text, name text, properties jsonb, embedding vector, source_id, created_at, updated_at)
  edges(id uuid, source_node_id uuid, target_node_id uuid, type text, properties jsonb, weight)
  -- nodes UNIQUE(type, lower(name)); join edges to nodes via source_node_id / target_node_id for names.
  -- extra fields live in properties (JSONB) — read with properties->>'key'.

Node types present: code_module, commit, package (code) · person, meeting, utterance, topic, decision,
source_document (knowledge). Edge types: touches, imports, calls, part_of, re_exports, authored (code) ·
attended, in_meeting, said, mentions, relates_to, rationale_for, derived_from (knowledge).

Examples:
  - "who last touched X"   : person -authored-> commit -touches-> code_module (filter name ILIKE '%X%')
  - "what was decided about Y" : decision nodes + relates_to/mentions/rationale_for; read properties for text
  - "who attended meeting Z"   : person -attended-> meeting
If unsure of exact types, discover first: `select distinct type from nodes` / `select distinct type from edges`.

Write one SQL SELECT (recursive CTEs allowed), call `aiven_pg_read`, then answer in 1-3 sentences.
Only state what the rows actually show — never invent people, files, decisions, or meetings."""


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.mcp is None or ctx.anthropic is None:
        raise RuntimeError("kg-agent requires AIVEN_TOKEN + ANTHROPIC_API_KEY")

    question = task.args.get("question") or task.args.get("q") or task.intent
    await ctx.activity("querying the knowledge graph")

    runner = ctx.anthropic.beta.messages.tool_runner(
        model=ctx.settings.model_fast,
        max_tokens=2048,
        system=KG_SYSTEM,
        tools=ctx.mcp.llm_tools(),
        messages=[{"role": "user", "content": question}],
    )

    answer = ""
    async for message in runner:
        for block in message.content:
            if block.type == "text" and block.text.strip():
                answer = block.text

    return {"question": question, "answer": answer}

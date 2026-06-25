"""git/codebase agent — answers from the knowledge graph via Aiven MCP (docs/AGENT_SYSTEM.md §9).

Fast path: a single streamed model turn that issues `aiven_pg_read` against the seeded
codebase graph. (Later optimization: programmatic templated SQL for the common intents,
skipping the LLM for retrieval — see §3.5.)
"""

from __future__ import annotations

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx

GIT_SYSTEM = """You are the git/codebase agent for Sunstead. Answer questions about a codebase \
using the Aiven PostgreSQL knowledge graph via the `aiven_pg_read` tool.

Aiven Postgres target: project "jq01", service "central-kg-pg", database "defaultdb".

Schema:
  nodes(id uuid, type text, name text, properties jsonb, embedding vector, ...)  -- UNIQUE(type, lower(name))
  edges(id uuid, source_node_id uuid, target_node_id uuid, type text, ...)
Code node types: file, function, class, commit, person.
Code edge types: DEFINES, IMPORTS, CALLS, AUTHORED (person->commit), TOUCHES (commit->file).

Write one SQL SELECT (recursive CTEs allowed), call `aiven_pg_read`, then answer in 1-2 sentences.
Only state what the rows actually show — never invent commits, people, or files."""


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.mcp is None or ctx.anthropic is None:
        raise RuntimeError("git-agent requires AIVEN_TOKEN + ANTHROPIC_API_KEY")

    question = task.args.get("question") or task.args.get("q") or task.intent
    await ctx.activity("querying code graph")

    runner = ctx.anthropic.beta.messages.tool_runner(
        model=ctx.settings.model_fast,
        max_tokens=2048,
        system=GIT_SYSTEM,
        tools=ctx.mcp.llm_tools(),
        messages=[{"role": "user", "content": question}],
    )

    answer = ""
    async for message in runner:
        for block in message.content:
            if block.type == "text" and block.text.strip():
                answer = block.text

    return {"question": question, "answer": answer}

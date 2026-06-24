"""web-agent (Agent B) — the demo money-shot: returns a live deployed URL.

Worker loop: consume(TASKS_WEB, group="web-agent") -> run Claude agent per task -> RESULTS.
Intents: build_website, update_website.
Tools: codegen (SDK agent_toolset) + vercel_deploy (Vercel API/CLI). May route LLM calls
through the Vercel AI Gateway (anthropic/claude-*).

TODO:
  - [ ] consume(config.TASKS_WEB, group_id="web-agent") -> Envelope[TaskCreatePayload]
  - [ ] generate site from args.brief (+ KG context via context_refs)
  - [ ] deploy via VERCEL_TOKEN -> capture URL
  - [ ] emit TaskResult(artifacts=[{kind:url, value:...}]) -> Kafka(RESULTS)
"""

import asyncio


async def main() -> None:
    print("web-agent: TODO — consume agent.tasks.web -> build + deploy -> agent.results (url)")


if __name__ == "__main__":
    asyncio.run(main())

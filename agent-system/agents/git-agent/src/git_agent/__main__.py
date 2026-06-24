"""git-agent (Agent D) — build this one first (simplest; often answers from the KG).

Worker loop: consume(TASKS_GIT, group="git-agent") -> run Claude agent per task -> RESULTS.
Intents: read_git, blame, who_changed, recent_changes.
Commits/people/files are already in the KG from seeding, so most answers come from kg_query;
fall back to `git` via bash only when needed.

TODO:
  - [ ] consume(config.TASKS_GIT, group_id="git-agent") -> Envelope[TaskCreatePayload]
  - [ ] resolve via KGClient.query; fallback to git bash
  - [ ] emit TaskResult(status=completed|failed) -> Kafka(RESULTS)
"""

import asyncio


async def main() -> None:
    print("git-agent: TODO — consume agent.tasks.git -> answer (KG-first) -> agent.results")


if __name__ == "__main__":
    asyncio.run(main())

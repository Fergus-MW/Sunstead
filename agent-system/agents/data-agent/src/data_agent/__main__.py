"""data-agent (Agent C) — analysis worker.

Worker loop: consume(TASKS_DATA, group="data-agent") -> run Claude agent per task -> RESULTS.
Intents: analyze, summarize_metrics, query_data.
Tools: python/pandas execution; returns an answer + optional chart artifact.

TODO:
  - [ ] consume(config.TASKS_DATA, group_id="data-agent") -> Envelope[TaskCreatePayload]
  - [ ] run analysis (pandas/numpy); render chart -> image artifact
  - [ ] emit TaskResult(result=..., artifacts=[{kind:image,...}]) -> Kafka(RESULTS)
"""

import asyncio


async def main() -> None:
    print("data-agent: TODO — consume agent.tasks.data -> analyze -> agent.results")


if __name__ == "__main__":
    asyncio.run(main())

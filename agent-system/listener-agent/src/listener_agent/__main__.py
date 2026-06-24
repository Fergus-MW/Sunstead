"""listener-agent (Agent A) entrypoint — the centerpiece.

Two-tier loop over meeting.transcript:
  Tier 1 (cheap, every final utterance): classify actionable = question|task|claim|reference|none
  Tier 2 (Opus, on trigger): ground in KG -> answer | delegate(task) | fact_check | clarify

The graph is the memory: keep only a short rolling window; summarize into kg.updates.

TODO:
  - [ ] consume(TRANSCRIPT, group_id="listener") -> Envelope[TranscriptPayload]
  - [ ] Tier-1 structured classifier (Haiku/Sonnet, strict JSON)
  - [ ] rolling window + periodic summarize -> Kafka(KG_UPDATES)
  - [ ] Tier-2 decision (Opus) with tools: kg_query, kg_semantic_search, quicksearch, emit_task, speak, write_fact
  - [ ] emit TaskCreate -> config.TASK_TOPIC_BY_INTENT[intent]
"""

import asyncio


async def main() -> None:
    # from shared import config
    # from shared.kafka import consume, make_producer
    # from shared.kg_client import KGClient
    # from shared.contracts import Envelope, TranscriptPayload, TaskCreatePayload
    print("listener-agent: TODO — Tier-1 classify + Tier-2 decide over meeting.transcript")


if __name__ == "__main__":
    asyncio.run(main())

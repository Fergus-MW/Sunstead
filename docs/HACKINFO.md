# Hack Info — Sunstead

Quick reference for the hackathon context: the challenge we're attacking, who the sponsors are,
how we're judged, and what we must submit. **Read this before optimizing what to build** — the
scoring rubric directly shapes our priorities (see [§ Strategy](#strategy-how-sunstead-maps-to-the-rubric)).

Event platform: **Junction**. Anthropic's challenge is judged remotely **only** from the Junction Platform submission.

---

## Our main challenge — Aiven: "The Autonomous Data Operator"

> Build a multi-agent system that natively **controls, streams, or queries** open-source data
> infrastructure using the **Aiven Model Context Protocol (MCP)**.

The thesis: traditional apps need thousands of lines of backend boilerplate just to let an LLM talk to a
database or a queue. **MCP rewrites that.** Agents get *direct, native* access to the data layer — provision
Postgres, spin up Kafka, run raw SQL, trigger pipelines — all via MCP tool calls in natural language.

**The brief explicitly penalises building backend boilerplate between an agent and a DB/queue.** That is the
single most important sentence in the whole challenge for us — it's why the team's [PLAN.md §3.5 MCP-native
realignment](PLAN.md) is binding: **agents talk to data via Aiven MCP; the FastAPI (`central-kg-api`) is for the
FE only.**

### What the Aiven MCP gives our agents ("the arsenal")
- **Core infra management** — agents spin up / scale / configure / delete real cloud services on the fly.
- **PostgreSQL** — execute SQL + pgvector similarity; agents read/write their own long-term memory & artifacts.
- **Apache Kafka** — publish + listen to live event streams → true agent-to-agent collaboration via pub/sub.
- **OpenSearch** (not 100% guaranteed available) — fast context caching + deep search.

MCP server + docs: **https://aiven.io/docs/tools/mcp-server** — connectable straight into Cursor, Claude Code,
or LangChain. Every team gets Aiven cloud credits (create a fresh Aiven account to claim).

### Inspiration directions (for framing the pitch)
1. **No-Backend App Swarm** — agents pass tasks via Kafka streams + store history in Postgres, all via MCP.
2. **Self-Driving Data Engineer** — user describes a need; agent provisions/configures DB + Kafka via MCP.
3. **Intelligent Data Detective** — agent watches streams/metrics; on anomaly, runs queries / scales infra via MCP.

→ Sunstead is closest to **#1 (No-Backend App Swarm)** with a live-meeting twist, and can *show* **#2** by
provisioning the remaining Aiven services (Kafka, OpenSearch) live via MCP during the build.

---

## Judging criteria (Aiven)
| Weight | Criterion | What it means for us |
|---|---|---|
| **34%** | **Depth of MCP integration** | How effectively agents leverage Aiven MCP tools. → Route Kafka pub/sub + Postgres/pgvector reads + OpenSearch through MCP, not client libraries. |
| **33%** | **Workflow autonomy** | Did the system abstract away manual backend coding? → Demo agents provisioning/streaming/querying with **no hand-written backend API** in the agent path. |
| **33%** | **Creativity & impact** | Original / useful / cool. → A live video-call agent swarm that delegates real work (deploys a site, answers from a code graph, speaks back) is the wow factor. |

The rubric is ~even thirds: **MCP depth is the tiebreaker we control most directly** — make the MCP usage
visible and central, not incidental.

---

## Sponsors & tech challenges we're stacking

| Sponsor | Track | How Sunstead uses it |
|---|---|---|
| **Aiven** | Main challenge (ours) | Kafka bus, Postgres+pgvector KG, OpenSearch — all via **Aiven MCP** |
| **Anthropic** | Tech challenge (stackable) | Claude powers the listener + every worker agent. **Must submit on Junction Platform** (judged remotely). |
| **ElevenLabs** | Tech challenge (stackable) | TTS for the listener's voice-out into the call (the "Can speak TTS" feature) |
| **Tangled** | Other main challenge | **Mutually exclusive with Aiven** — we are NOT doing this one |

**Rules that matter:**
- You can enter **only one** main challenge (Aiven **or** Tangled) → we're Aiven.
- You can stack **both/either** tech challenges (ElevenLabs, Anthropic) **on top** → target both: Claude agents
  (Anthropic) + ElevenLabs voice-out. That's three prize surfaces from one build.
- Anthropic winner is chosen **remotely, only from the Junction Platform submission** → the written submission
  quality matters independently of the live pitch.

---

## Submission checklist
- [ ] **GitHub repo** (this one)
- [ ] **Demo video**
- [ ] **Description** (the written submission — carries the Anthropic judging on its own)
- [ ] Possible additional info
- [ ] **Junction Platform** submission filled in (required for Anthropic)

**Pitch logistics:** top 3 of each main challenge (Aiven, Tangled) + top 2 ElevenLabs pitch **tomorrow 20:15**.
**4 minutes, no Q&A.** Selected teams are **not** told in advance — everyone prepares a pitch and may be called
immediately after announcement. → Have a tight 4-min pitch + working demo ready regardless.

---

## Strategy: how Sunstead maps to the rubric

1. **Make MCP the spine, not a side-call (34%).** Per [PLAN.md §3.5]: agent→data goes through Aiven MCP
   (`postgres_query`, `kafka_consume`/publish, `opensearch_*`). Our `agent-system/shared/kafka.py` +
   `kg_client.py` direct clients are the *pre-realignment* approach — the agent path should move to MCP; keep
   real clients only where latency forces it (e.g. the high-rate transcript stream from `call-gateway`).
2. **Show autonomy on camera (33%).** Provision the *next* Aiven service (Kafka, OpenSearch) via MCP tool calls
   during the build and capture it; note the tool calls in commit messages. Judges look for visible evidence that
   backend wiring was abstracted away.
3. **Lead the pitch with the wow (33%).** "Someone in a standup says *build us a landing page and tell me who last
   touched the auth module* — the agent deploys a real URL and answers from the code graph, then says it out loud."
   One sentence, whole system exercised.
4. **Bank the stacked prizes.** Ensure Claude usage is front-and-center (Anthropic) and wire ElevenLabs voice-out
   (ElevenLabs) — both are low marginal cost on top of the Aiven build.

---

## Open logistics to confirm
- Aiven credits claimed on a fresh account? Who holds the org / MCP credentials for the team?
- OpenSearch availability via our credits (brief says "not 100%") — confirm early; have the Postgres-only fallback ready.
- Demo recording slot + who narrates the pitch.

# Sunstead — Pitch Brief

> Everything you'd want on hand for the 4-minute pitch (no Q&A) + the written Junction submission.
> Sources of truth: [OVERVIEW.md](OVERVIEW.md) (current state), [DESIGN.md](DESIGN.md) (vision),
> [HACKINFO.md](HACKINFO.md) (rubric). This doc is the *pitch-facing* distillation — what to say, in what order,
> and the facts to back each claim. Last synced 2026-06-25.

---

## 0. The one breath (lead with this)

> **Sunstead is an AI employee that sits in your meetings.** Someone in a standup says *"build us a landing page
> and tell me who last touched the auth module"* — and a photorealistic avatar deploys a real URL and answers
> from a live graph of your codebase, while a watched swarm of specialist agents does the work behind it. It's
> not a notetaker that summarizes after the call. It's a coworker that acts *during* it.

That single sentence exercises the whole system: avatar → planner → web-agent (deploys) + git/KG-agent (answers)
→ dashboard. Open on it; everything else is proof.

## 1. The problem / why it matters

- Meetings generate decisions and action items that die in a notes doc nobody re-reads.
- The knowledge to act ("who owns this?", "what did we decide?", "ship a page") is scattered across the codebase,
  past meetings, and people's heads — and an LLM normally needs *thousands of lines of backend glue* to reach any
  of it.
- **Aiven MCP removes that glue.** Agents talk to Postgres, pgvector, and Kafka *natively* via MCP tool calls — so
  the interesting work (a watched, graph-grounded agent swarm doing real tasks live) is what's left to build.

## 2. The demo (what a judge sees)

1. **The face is in the call.** Aino (the avatar) is a participant — listens, converses (STT→Claude→TTS).
2. **A spoken request becomes work.** The utterance hits the planner, which decomposes it into delegated tasks.
3. **Real deliverables appear on the dashboard:**
   - **web-agent** → a *clickable live URL* of a generated site.
   - **git/KG-agent** → "who last touched auth" answered from the live graph (verified: top authors 387/32/23
     commits) with a **green "grounded ✓ N rows"** badge from the verifier.
   - reasoning **streams** into a collapsible panel per task; a **stop button** can cut any task short.
4. **The graph grows from the meeting itself** — meeting-ops writes recap / action-items / decisions back into the
   same graph the agents read.

**Fallback demo (lower risk):** drive the same pipeline without Recall/LiveKit via `make stack` +
`make say T="build a landing page and tell me who owns auth"`. Identical path, no avatar dependency. The avatar
is the wow but the riskiest to deploy live — have the headless path ready.

## 3. Architecture (the diagram to draw)

```
 REALTIME (<1s, human-facing)            ASYNC (heavy work, sec–min)
 ┌───────────────────────────┐  delegate ┌──────────────────────────────────────────┐
 │ avatar-agent (Aino)       │ ───────▶  │ agent-runner container (one image)         │
 │ Recall + LiveKit + Anam   │           │ Kafka consumer → harness → SPECIALISTS:    │
 │ STT → Claude Sonnet → TTS │  meeting. │ git · web · data · meeting-ops · research  │
 │ reads graph over HTTP ────┼─┐ transcript│  + planner (transcript→tasks)             │
 └───────────────────────────┘ │HTTP     │ emits results/activity/trace ─────────────┼─┐
        ▲ speaks / shows        ▼          │ all data ops via Aiven MCP                │ │ Kafka
        │                 ┌──────────┐     └──────────────────┬─────────────────────────┘ │
  FRONTEND (Vercel) ◀─WS─ │ gateway  │ ◀── Kafka tail ── AIVEN: Postgres+pgvector · Kafka · OpenSearch
  /dashboard /graph       │POST /tasks│
  + ask-box               │ WS /stream│   central-kg-api (FastAPI): seed + FE/avatar read bridge
                          └──────────┘
```

**Two tempos, one seam.** The realtime avatar can't afford an MCP detour for a sub-second lookup, so it reads the
graph over HTTP — and the **34% MCP depth lives in the async worker suite**, where every data op is a `aiven_pg_read`
/ `aiven_pg_write` / Kafka MCP call. Both are honestly true at once; don't claim the avatar is MCP-native.

## 4. Components & who does what

| Component | Role | Stack |
|---|---|---|
| **avatar-agent** | The face in the call — listens, speaks, delegates | LiveKit · Recall.ai · Anam (talking-face) · Soniox STT · Claude Sonnet 4.6 · Cartesia/ElevenLabs TTS · Terraform |
| **agent-system** (planner + 6 agents + gateway) | The brain & hands — decompose, execute, broadcast | Python · `aiokafka` · Anthropic SDK · **Aiven MCP** (`mcp-aiven`, warm stdio session) · matplotlib · FastAPI gateway |
| **central-kg-api** | The graph + the seeder | FastAPI · SQLAlchemy async · Aiven Postgres 17 + pgvector + pg_trgm · tree-sitter · OpenSearch (BM25) · Mangum/Lambda |
| **meet-joiner** | The window in — bot launcher, graph explorer, mission-control dashboard | Next.js (Vercel) · zero-dep canvas force-graph · WebSocket stream |

**The six specialists** (all run in one container as `asyncio` tasks, dispatched by intent):

| Agent | Does | Model | Data path |
|---|---|---|---|
| **git / KG** | answers code + knowledge questions from the graph (canned fast-path SQL **or** agentic) | Haiku (phrasing) | `aiven_pg_read` |
| **web** | generates & serves a one-file site → live URL artifact; revisioned workspace | Opus 4.8 | filesystem (Vercel = swap-in) |
| **data** | strict-tool SQL → matplotlib chart PNG artifact | Haiku | `aiven_pg_read` |
| **meeting-ops** | recap / action-items / decisions → **writes back to the graph** (idempotent) | Opus 4.8 | `aiven_pg_write` |
| **research** | live web search/fetch → answer + sources | Sonnet/Opus | Claude server-side tools |
| **echo** | no-creds smoke test of the loop | — | — |

## 5. Frameworks & companies to name-drop (and why)

- **Aiven** *(main challenge)* — Postgres + pgvector + Kafka + OpenSearch, all reached **via Aiven MCP**. Our 34%
  spine. *Evidence to show:* the Aiven Kafka cluster `kafka-254bd14f` is **RUNNING with topics created via MCP**
  (`aiven_kafka_topic_create`) — the exact autonomy evidence judges want.
- **Anthropic** *(stackable tech prize)* — Claude powers the avatar and *every* agent. Opus 4.8 for hard
  extractions/builds, Sonnet 4.6 realtime, Haiku 4.5 for fast/cheap turns. Judged remotely from the written
  submission — so the written description must stand alone.
- **ElevenLabs** *(stackable tech prize)* — TTS voice-out for the avatar.
- **LiveKit · Recall.ai · Anam** — the realtime video/avatar stack.
- **Soniox / Deepgram** — streaming STT. **tree-sitter** — code → graph parsing.

> Three prize surfaces from one build: Aiven (main) + Anthropic + ElevenLabs (stacked).

## 6. Rubric mapping (34 / 33 / 33) — say these explicitly

- **34% MCP depth** → every agent data op is an Aiven MCP call; git-agent verified end-to-end on real data via
  `aiven_pg_read`. Warm local `mcp-aiven` session, no per-call auth overhead.
- **33% autonomy** → Kafka cluster + topics provisioned *via MCP tool calls*, not a console. No hand-written
  backend API sits between an agent and the data layer.
- **33% creativity/impact** → a live-meeting avatar that delegates real work to a *watched* swarm: deploys a site,
  answers from a code graph, and grows that graph from the meeting. The oversight layer (grounding verifier +
  mission-control + stop button) is itself a differentiator most teams lack.

## 7. The killer lines (memorize)

- "Not a notetaker that summarizes after the call — a coworker that acts *during* it."
- "Every data operation an agent makes is a native Aiven MCP call. We wrote zero backend boilerplate between the
  agent and the database or the queue — that's the whole point of the challenge."
- "The cost of a wrong answer here isn't a failed test — it's a false statement said out loud to a client. So
  every factual answer is grounded against the exact rows it was built from, and badged on screen."
- "All of it — the dashboard, the verifier, the stop button — is just another Kafka consumer. The bus *is* the
  oversight surface."

## 8. Risks to pre-empt before you walk on stage

See the full list in the [vision-gaps snapshot](../reviewmd/2026-06-25T12-30Z-vision-gaps-snapshot.md). The three
that would hurt most on camera:
1. **No single recorded happy-path run yet** — record `make stack` + `make say …` end-to-end as the backup.
2. **Avatar deploy is the riskiest** — keep it local; deploy the clickable loop (FE + gateway + KG + Aiven Kafka).
3. **Runner/gateway not yet pointed at Aiven Kafka** — works on local redpanda; the cloud switch is env-only but
   unproven. Decide whether the demo runs on local or cloud Kafka *before* the pitch.

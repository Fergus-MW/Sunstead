# Sunstead — Vision-Gaps Snapshot

**Timestamp:** 2026-06-25T12:30Z
**Branch:** `main` @ `7424210`
**Reviewer:** full-repo code-grounded pass (agent-system + central-kg-api + avatar-agent + meet-joiner)
**Purpose:** Hold the *stated vision* (an overseen, Kafka-native agent employee with a self-growing knowledge graph)
against what the **code actually does today**, and name every gap that stands between the two. Companion to the
pitch-facing [OVERVIEW §1](../docs/OVERVIEW.md) and [PITCH.md](../docs/PITCH.md).

---

## 0. TL;DR

The system is **real and impressively wired** — far past the "three disconnected pillars" of the earlier
snapshots. The seams (avatar → planner → tasks → runner → results → dashboard) are closed *in code* and verified
locally. The gaps now are of two kinds:

- **Proof/deploy gaps** — things that work but aren't *banked* (no recorded happy-path; not pointed at Aiven Kafka).
- **Vision-vs-reality gaps** — places where the pitch narrative outruns the implementation. These are the ones to
  be careful about claiming on stage. Four matter most:
  1. **"All agents see each other's message stream"** — *not literally true.* Agents are stateless and don't
     subscribe to each other; the shared stream exists at the **gateway/dashboard** layer (everything is broadcast)
     and as **shared conclusions in the KG**, not as agent-to-agent consumption.
  2. **"An overseer agent that cuts reasoning short / verifies outputs"** — *half-built.* Output **verification**
     is real (grounding verifier). The **"cut reasoning short" tripwire** and the **autonomous conductor** are
     **not** a backend agent yet — the conductor lives client-side in the dashboard; cancel is human-triggered.
  3. **"The KB constantly ingests codebase, live meetings, online lookup, previous notes & stays synced"** —
     *mostly true now.* Codebase ingest is real; meeting write-back is real; **online research is now persisted to
     the graph** (`research_finding` + `source_document` nodes — shipped 2026-06-25, see LOG). Remaining: OpenSearch
     is **seed-time only** (goes stale); the `kg.updates` async sync consumer is **not implemented**.
  4. **"Connect it to integrations"** — *aspirational.* No integrations (Gmail, Calendar, Slack, etc.) are wired
     today. The MCP+Kafka architecture *makes them pluggable*, but none exist. Say "designed for", not "has".

None of these sink the pitch — they sharpen it. The fix is to **claim the architecture's potential explicitly and
the implemented slice precisely**, and to close the highest-leverage gaps first.

---

## 1. Vision claim → reality ledger

| Vision claim | Reality in code | Verdict |
|---|---|---|
| Lives in meetings **+ other access points** | Avatar emits `meeting.transcript`; dashboard **ask-box** + avatar `delegate()` both POST the gateway `/tasks`. Two real doors. | ✅ **True** |
| **Intelligent agent suite powered by Kafka** | Planner + 6 specialists, `asyncio` in one container; bus topics `meeting.transcript`, `agent.tasks.{web,data,git,ops,research,dev}`, `agent.results`, `agent.activity`, `agent.trace`, `agent.control`. | ✅ **True** |
| **All agents can view each other's message stream** | Everything is published & **broadcast to the gateway → dashboard**; agents themselves are **stateless and do not consume each other's results**. Shared context is via the **KG** (conclusions as nodes) + planner injecting context. | 🟡 **Reframe** — observable to humans/overseer, not agent-to-agent. |
| **Overseer agent to verify outputs** | `shared/verify.py` — grounding verifier (Haiku, fail-open, **off critical path**), gates git/data answers, badged on FE. | ✅ **True** (output verify) |
| **Overseer to cut reasoning short** | `agent.control` cancel exists (runner broadcast-group → cancels asyncio task); FE stop button triggers it. But **auto-stop/conflict detection lives in the FE digest**, not a backend conductor; no reasoning **tripwire**. | 🟡 **Partial** — manual stop yes, autonomous "cut short" no. |
| **Graph KB: ingest codebase** | `seed/` tree-sitter + git → ~6,183 nodes / 26,179 edges (`anthropic-sdk-python`). Live. | ✅ **True** |
| **Graph KB: ingest live meeting** | meeting-ops agent extracts recap/action-items/decisions → `aiven_pg_write` (idempotent). Demo meetings also seedable. | ✅ **True** (via the agent) |
| **Graph KB: ingest online lookup** | research agent does live web search/fetch **and now writes findings back** (`research_finding` + `source_document` nodes via `aiven_pg_write`, best-effort, gated on a real answer). | ✅ **Shipped** (2026-06-25) |
| **Graph KB: ingest previous meeting notes** | Only hand-crafted demo JSON (`infra/demo_meetings/`). No general notes-ingest pipeline. | 🟡 **Demo-only** |
| **Constantly sync & update the KB** | meeting-ops write-back is the live path. The `central-kg-api` `kg.updates` Kafka consumer is **not implemented**; OpenSearch is **mirrored at seed time only** (not re-synced on writes). | 🟡 **Partial** |
| **Fast advanced lookup (hybrid vector + keyword)** | `/query` hybrid pgvector+trigram (~125 ms); `/search` OpenSearch BM25. **But `embeddings.py` is a no-op** (Anthropic ships no embeddings API) → vector half is NULL → **degrades to trigram/BM25 only.** | 🟡 **Keyword-only today** |
| **Connect to integrations** | None wired. Architecture (MCP tools + Kafka topics + gateway front door) supports it. | 🔴 **Aspirational** |
| **MCP-native data layer (34%)** | Warm `mcp-aiven` stdio session; `aiven_pg_read`/`aiven_pg_write` real and verified; Kafka cluster + topics created via MCP. | ✅ **Strong** |

---

## 2. Gaps that block the *demo*, ranked

1. **No single recorded happy-path run.** Every seam is wired and verified *in pieces*; nothing proves the whole
   sentence end-to-end on tape. **Highest leverage action before the pitch** — record `make stack` + `make say …`.
2. **Runner/gateway still on local redpanda.** Aiven Kafka is provisioned (`kafka-254bd14f`, RUNNING) but the
   runner/gateway aren't pointed at it (needs bootstrap + SASL creds, and the bus has grown to ~13 topics since the
   9 were created — `agent.trace`/`agent.control` were added after). Cloud switch is env-only but **unproven**.
   Decide local-vs-cloud for the demo *now*.
3. **web-agent is local-serve only.** Generates and serves a real site to a URL, but no Vercel deploy — the
   "clickable live URL" needs the serve port (`:8810`) publicly reachable, or it's localhost-only on camera.
4. **Avatar is the riskiest to deploy** (its own LiveKit + Recall + Anam Terraform stack). Keep it local; deploy
   the clickable loop (FE + gateway + KG + Aiven Kafka) which a judge can actually click.
5. **In-call transcript overlay missing** in the FE — the dashboard shows tasks/reasoning but not the live
   transcript alongside.

## 3. Gaps that weaken the *vision narrative* (be precise on stage)

- **Agent-to-agent stream sharing.** The pitch-friendly truth: *"every agent's status, reasoning, and result is
  published to one Kafka bus, so the overseer and the dashboard see the whole swarm in real time, and agents share
  durable conclusions through the knowledge graph."* That is real. Avoid the literal "every agent reads every other
  agent's stream" — they don't subscribe to each other (good for latency/debuggability; see AGENT_SYSTEM §3.5).
  *If you want the literal claim:* a small addition — let the harness tail `agent.results` for `parent_task_id`
  children — would make web-agent→data-agent chaining real (already designed, depth-limit ≤2).
- **Grounded site-building — SHIPPED (2026-06-25).** A "build a site about X" used to hallucinate X (the web-agent
  never saw the parallel research task). The web-agent's build path now **researches the live web itself** (Claude
  server-side `web_search`, effort-budgeted) and grounds the copy before writing — and the planner folds "build +
  look it up" into ONE grounded task. So *agent-to-agent chaining isn't needed for the build-grounding case* — the
  web-agent self-grounds. Safe claim: *"it researches before it builds, so the site's facts are real, with sources."*
- **The "overseer agent."** Three guards are *designed* (tripwire / verifier / conductor — DESIGN §7); only the
  **verifier** ships as code, and the **conductor** is a client-side digest, not a backend service. The honest
  framing: *"oversight is a first-class layer — output grounding ships today; the reasoning tripwire and an
  autonomous conductor are the next consumers on the same bus."*
- **Self-growing KB.** True for meeting outcomes **and now research findings** (shipped). Still **not** general docs,
  and **not** re-synced to OpenSearch (so `/search` slowly diverges from `/query`). Safe claim: "meeting decisions
  *and* web research flow back into the graph live" — a real, demoable flywheel; just don't claim OpenSearch stays
  in lockstep.
- **Vector search.** "Advanced semantic lookup" is currently **keyword/BM25 + trigram**, because embeddings are a
  no-op (no Anthropic embeddings API; column is reserved for Voyage/OpenAI swap-in). Either wire an embeddings
  provider (Voyage/OpenAI — ~an afternoon) before claiming semantic search, or describe it as "hybrid keyword +
  graph traversal," which is accurate and still strong.

## 4. Reliability / correctness flaws worth knowing

- ~~**In-memory idempotency.**~~ ✅ **Fixed (2026-06-25).** Harness dedupe is now journaled to
  `sessions_dir/seen.log` and reloaded at boot (`AgentContext.claim()`), so a **runner restart no longer re-runs
  delivered tasks** under at-least-once redelivery. Best-effort, compacted at `SEEN_MAX`, fail-safe to in-memory.
- **Avatar gets only the final result, not the stream.** Trace deltas reach the FE, but TTS still receives just
  the final wall of text → the meeting sits silent then dumps. A second consumer on `agent.trace`'s `text` phase →
  speech path is the real payoff of streaming (and the DESIGN §3 "spoken results" edge).
- **Autonomous conductor judgement is client-side.** Stuck/conflict/duplicate detection that *should* auto-issue a
  cancel lives in the dashboard digest (recomputed on a 2 s tick), not the backend — so it can't actually *act*
  without a human clicking stop. Promoting it server-side is the remaining conductor work.
- **`kg.updates` consumer absent.** meeting-ops writes directly via `aiven_pg_write` (fine), but the designed async
  fact-sync path into `central-kg-api` doesn't exist — so anything that emits `kg.updates` is dropped.
- **OpenSearch staleness.** Mirrored once at seed time; live `/ingest`/`/extract`/meeting write-backs do **not**
  re-index → `/search` slowly diverges from `/query`. Fine for a fixed-corpus demo; a real flaw for "constantly
  synced."

## 5. The shortest path to make the vision *land* (recommended order)

1. **Record the happy-path** (`make stack` + `make say`) — converts "wired" into "proven". *(highest leverage)*
2. **Decide & lock the Kafka target** (local redpanda for safety, or finish the Aiven SASL switch for the autonomy
   story). Don't leave it ambiguous at pitch time.
3. **Make one live URL publicly clickable** (expose `:8810`, or one real Vercel deploy) — the single most tangible
   "it did real work" artifact.
4. **Tighten the script to claims you can defend** — use the §3 reframings verbatim; they're still impressive and
   they're true.
5. *(If time)* wire an embeddings provider (Voyage) → real semantic search; and/or tail `parent_task_id` children
   in the harness → real agent-to-agent chaining. Either upgrades a 🟡 to a ✅.

---

_Cross-refs: [OVERVIEW.md](../docs/OVERVIEW.md) (current state), [DESIGN.md](../docs/DESIGN.md) §6–§7 (oversight
roadmap & decisions), [AGENT_SYSTEM.md](../docs/AGENT_SYSTEM.md) §3.5 (why agents don't subscribe to each other),
[CENTRAL-KG-API.md](../docs/CENTRAL-KG-API.md) §7 (KG gaps)._

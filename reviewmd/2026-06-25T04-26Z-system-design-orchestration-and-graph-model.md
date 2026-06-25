# Sunstead — System Design: Orchestration + Graph Data Model

**Timestamp:** 2026-06-25T04-26Z
**Branch:** `main` @ `be5927e` (working tree dirty)
**Scope:** A forward design pass covering two coupled questions: (1) how the system understands intent, routes, scales effort, and shares information across agents; (2) how the knowledge graph should be **modeled** — one graph vs many, entity vs episodic data, JSONB vs columns vs tables — so the agent surface can exploit it. The two are coupled: every orchestration mechanism (grounding, chaining, cross-agent memory) sits on the graph, so the data model is load-bearing.
**Companions:** [`2026-06-25T03-23Z-snapshot.md`](2026-06-25T03-23Z-snapshot.md) (full-system), [`2026-06-25T03-40Z-agent-suite-scoping.md`](2026-06-25T03-40Z-agent-suite-scoping.md) (agent suite). This doc is the design layer those two implied but didn't draw.
**Status:** design only — no code changed this pass.

---

## 0. TL;DR

Two root findings, each of which dissolves a cluster of symptoms:

1. **There is no orchestration layer — only a stateless router.** The planner routes one context-free utterance to one agent, fire-and-forget ([`planner.py:101`](../agent-system/agent-runner/src/agent_runner/planner.py#L101)). Every felt problem — "it made up what Sunstead is," no quick-vs-deep control, no follow-up questions, no research→website chaining, no cross-agent memory — is a symptom of the same missing layer: a **per-meeting brain that holds state, grounds briefs, scales effort, decomposes into a task DAG, and consumes results**. The fix is one keystone change: *the planner also consumes `agent.results` and holds a plan registry.* Everything else hangs off that.

2. **Episodic data is masquerading as entity data in one over-merged table.** The graph is a single polymorphic `nodes`/`edges` pair keyed by `(type, lower(name))` ([`schema.sql:30`](../central-kg-api/schema.sql#L30)). That natural key is *correct for entities* (a file, a person — seeing them twice should merge) and *wrong for episodes* (two different action items, two meetings' decisions — must NOT merge). meeting-ops writes `action_item`/`decision` nodes under exactly this key ([`meeting.py:88`](../agent-system/agent-runner/src/agent_runner/agents/meeting.py#L88)), so **"follow up with Alex" in meeting A and meeting B collapse into one node.** The schema already ships the right home for episodes — the **`events` table** ([`schema.sql:47`](../central-kg-api/schema.sql#L47)) — but it's barely used. The answer to "merge or separate graphs" is: **one graph, but split the entity layer from the episodic layer.**

Both are completion-and-correction along the existing grain, not redesigns. The architecture anticipated both (DESIGN §4 specs `parent_task_id`/depth chaining; §7 specs "KG as cross-agent substrate" and "durable verdicts → KG"); they're unbuilt.

---

## Part A — Orchestration: from router to brain

### A.1 The reframe

```
TODAY:   transcript ─▶ [planner: route ONE utterance] ─▶ tasks ─▶ runner ─▶ results ─▶ FE
                        stateless · no context · no depth · no chaining · no follow-up

TARGET:  transcript ─▶ [ROUTER]  (keep: cheap, stateless "is this work? which capability?")
                           │
                           ▼
                  [ORCHESTRATOR: per-meeting brain]  ◀── also consumes agent.results
                    • grounds the brief against the KG          → fixes hallucination
                    • picks effort from intent signals          → fixes quick vs deep
                    • emits a task DAG (needs:[...]) not a list  → fixes chaining
                    • injects finished results into dependents   → fixes research→website
                    • clarifies OR states-and-assumes            → fixes follow-ups
                           │
                           ▼
                  tasks (effort + context_refs + needs) ─▶ runner ─▶ harness
                           ▲                                            │
                           └──────── results feed back to orchestrator ─┘
                  KG = shared memory: agents read prior conclusions, write their own
```

The orchestrator **is the planner process plus a `agent.results` consumer and a small per-meeting plan registry**. That one addition is the keystone.

### A.2 Coverage — every concern → mechanism → cost

| Concern | Root cause | Mechanism | Cost |
|---|---|---|---|
| Quick vs extensive | depth is a per-agent constant | **`effort` field** on the plan, threaded into each agent (search budget · turns · model tier · templated-SQL vs LLM) | low |
| "Made up Sunstead" | router sees one context-free utterance; web-agent has no grounding channel | **Brief-grounding**: orchestrator resolves entities against the KG, passes `context_refs`; agents run the skipped harness *fetch-context* stage | med |
| Follow-up questions | only `{0 tasks \| dispatch}` — no third branch | **Clarify-or-assume**: gate real clarification (FE card / avatar voice) behind cost×ambiguity; else state assumption + proceed | med |
| Cross-agent info | agents are pure Kafka *producers* | **KG as substrate** (not stream-reading): agents write conclusions as graph records, read meeting conclusions back | med |
| Research → running website | no chaining; `parent_task_id`/`depth` unused | **Task DAG + result injection**; in-flight case → a **revision** via existing `update_website`/`workspace_id` | high |
| Minimal wasted tokens, adapt to user | one gear per agent | falls out of `effort` defaulting to cheap, escalating only on user signal | (subsumed) |

### A.3 Mechanisms, concretely

**1 — Effort/depth (cheap; do first).** Add `effort: "quick" | "standard" | "deep"` to the plan tool ([`planner.py:71`](../agent-system/agent-runner/src/agent_runner/planner.py#L71)) and to `TaskCreatePayload`. Infer from linguistic signal ("just check / ballpark" → quick; "dig in / properly research / thorough" → deep) and intent defaults. Then **model + budget become a function of effort, not hardcodes**:
- research: `max_uses` 2→8, `effort` low→high, `MAX_TURNS` 2→6, model Haiku→Opus ([`research.py:26`](../agent-system/agent-runner/src/agent_runner/agents/research.py#L26)).
- KG/git: quick → **templated SQL, no LLM**; deep → the LLM tool-runner.
- web: quick → single pass; deep → grounded multi-step.

Add `model_mid` (Sonnet 4.6) to [`config.py`](../agent-system/shared/src/shared/config.py#L101) for the middle tier — today only `model_smart`/`model_fast` exist. This is also exactly the frugal-default-with-opt-in-escalation you asked for.

**2 — Brief-grounding (the real Sunstead fix).** "Feed the transcript to the router" is necessary but *insufficient* — the window won't contain a definition of "Sunstead" nobody spoke. The actual fix is the **`fetch context` stage every agent currently skips** (AGENT_SYSTEM §4; `context_refs` exists in [`contracts.py:63`](../agent-system/shared/src/shared/contracts.py#L63), unused). The orchestrator resolves entity mentions against the KG (trigram name match is enough: `name ILIKE '%sunstead%'`) and either inlines a grounded brief or attaches `context_refs` node ids the agent expands. The KG becomes the disambiguation memory — which is the data-model point in Part B.

**3 — Clarify-or-assume (follow-ups, tuned for a live meeting).** Blocking a meeting on a question is expensive, so gate it: **clarify synchronously only when ambiguity is high AND the task is costly/irreversible** (a 30 s web build earns one question; a 1 s lookup does not). The cheaper path that covers most cases: the agent **grounds (mech. 2), states its assumption, and proceeds** — *"Building a Sunstead site (the hackathon project — say so if you meant otherwise)."* Surfaces: an FE prompt card, or — the stronger beat — **the avatar voices it** (DESIGN §3 spoken-notify edge, reversed; §7 "oversight is also content"). That is what makes it read as an employee, not a form.

**4 — KG as the cross-agent substrate (NOT Kafka stream-reading).** Recommend steering off "all agents tail the Kafka stream": replaying topic history into an LLM context is the opposite of token-frugal, and ordering/replay make it fragile. DESIGN §7 already chose right: *"agents share conclusions (nodes), not raw traces."* Every agent writes its conclusion as a graph record linked to the meeting; the fetch-context stage reads "what does this meeting already know." For *live ambient* awareness, the **orchestrator** (already tailing results) keeps a per-meeting blackboard — **one shared reader, not N tailers**. (How to store those conclusions correctly is Part B.)

**5 — Decomposition + chaining (the deep one).** The orchestrator emits a small **DAG**: a task may declare `needs:[local_id]`. "Build a website for Sunstead" with an unfamiliar entity → `[research/KG: "what is Sunstead"] → [build_website needs it]`. The orchestrator holds the plan, consumes `agent.results`, and when a task's deps land **injects their output into the dependent's args and dispatches** — depth-capped at 2 (§4). Your "inject into the *already-started* builder" is best realized **not** as a mid-flight mutation (an agent's `run()` is a single stream with no pause point — the control channel can cancel, not amend) but as: **quick build now, research in parallel, then auto-`update_website` with the findings** — URL-stable because update keys by `workspace_id` ([`web.py:67`](../agent-system/agent-runner/src/agent_runner/agents/web.py#L67)). The "injection" becomes a *revision* — easier and more correct. (True in-flight `redirect` is DESIGN §7's named next verb — future, not now.)

---

## Part B — The graph data model (the part to get right)

### B.1 What actually exists

```sql
sources (id, kind, uri, title, content, metadata jsonb, created_at)        -- provenance/raw
nodes   (id, type, name, properties jsonb, embedding vector(1536),         -- the graph entities
         source_id, created_at, updated_at)   UNIQUE (type, lower(name))
edges   (id, source_node_id, target_node_id, type, properties jsonb,       -- relationships
         weight, source_id, created_at)        UNIQUE (source, target, type)
events  (id, node_id, source_id, kind, occurred_at, payload jsonb)         -- episodic sidecar
```
Indexes: `nodes(type)`, **trigram GIN on `name`** (powers `ILIKE`), **ivfflat on `embedding`**, unique `(type,lower(name))`; `edges` on source/target/type. ([`schema.sql`](../central-kg-api/schema.sql))

Reality check from the code:
- **Code and knowledge already share one graph.** The seed maps graphify `code / document / concept / rationale` into the *same* `nodes`/`edges` via a type map ([`graphify_adapter.py:15`](../central-kg-api/seed/graphify_adapter.py#L15)). So "merge or separate?" is already answered *merged* — the live question is whether that's right and how to keep it from becoming a junk drawer.
- **Embeddings are dead.** `embed_one`/`embed_texts` return `None` ([`embeddings.py`](../central-kg-api/app/embeddings.py)) — no Anthropic embeddings API; OpenAI optional and unset. So the ivfflat index is empty and `hybrid_search` degrades to pure trigram. pgvector is a doc claim, not a running path (snapshot §5 already flags this).
- **`events` is built but barely used** — only `/update` writes it ([`update.py:19`](../central-kg-api/app/routers/update.py#L19)); no agent does. It's a designed-in episodic layer sitting idle.
- **`sources` is provenance**, written by the seed and `/ingest`. Healthy. Keep.

### B.2 The core finding: three populations, two of them conflated

There are (at least) three *kinds* of datapoint, with different identity semantics:

| Population | Identity is… | Dedup on write? | Examples | Right home |
|---|---|---|---|---|
| **Entities** | a stable name | **yes** — merge | `person`, `code_module`, `package`, `topic`, `meeting`, `website` | **`nodes`** (keep `(type,lower(name))`) |
| **Episodes** | an *occurrence* | **no** — append | `utterance`, `action_item`, `decision`, `research_finding`, `agent_run`, `oversight_verdict` | **`events`** (or scoped nodes — see B.3) |
| **Provenance/raw** | a document | yes | transcript, repo, web page | **`sources`** (already) |

The bug: meeting-ops writes **episodes as entities**. `INSERT INTO nodes ... ON CONFLICT (type, lower(name)) DO UPDATE` with `name = description` ([`meeting.py:86`](../agent-system/agent-runner/src/agent_runner/agents/meeting.py#L86)) means two distinct action items with the same text — across different meetings — **merge into one node, the second silently overwriting the first's properties.** Same for `decision`. This is data loss that grows with every meeting, and it's invisible until you query "what did meeting X decide" and get meeting Y's text.

### B.3 Recommendation: one graph, two layers

**Do NOT split into separate graphs.** The value is in the joins — `person —authored→ commit —touches→ code_module` and `person —attended→ meeting —decided→ decision` meet at shared `person`/`topic` entities. Separate graphs would sever exactly the cross-domain links that make this an *employee's* memory rather than two databases. Keep one graph.

**DO split entity from episodic within it.** Two clean rules:

1. **Stable, identity-by-name things stay `nodes`** with the natural key: `person`, `code_module`, `package`, `topic`, `meeting` (keyed by `meeting_id`), `website`/`artifact`. Merge-on-conflict is correct here.

2. **Occurrence-by-instance things become events or *scoped* nodes:**
   - **Pure observations** (raw utterances, agent activity, oversight verdicts, task-lifecycle records) → **`events`**. They have `occurred_at` (you *want* time-ordering), a `kind`, a `payload`, and a `node_id` pointing at the entity they're about (the meeting, the answer, the task). They never need to be edge endpoints. This is the table's exact purpose.
   - **Episodes you must traverse in the graph** (an `action_item` you link `person —owns→`, a `decision` you link `—rationale_for→`) → keep them as **nodes but give them a non-colliding name**. The minimal correct fix to meeting-ops: `name = f"{meeting_id}:{sha1(description)[:8]}"` (or a UUID), with the human text in `properties.description`. Distinct instances stop merging; same-meeting re-runs still idempotently upsert. *One-line change, removes the data-loss bug.*

This directly answers your "save in JSON sometimes, or a table?" — **a different *kind* of record gets its own table (`events`/`sources`); attributes *of* a record stay in `properties` JSONB.** Episodes are a different kind of record, not attributes of an entity.

### B.4 JSONB vs columns vs promoted keys — the rule

Three tiers, by how the field is queried:

1. **First-class columns** — needed for integrity/perf or filtered on *every* type: `id, type, name, source_id, created_at, updated_at, embedding`, edge `weight`. Keep as is.
2. **Promote hot cross-cutting filter keys out of JSONB.** Today meeting-scoped reads do `properties->>'meeting_id'` with **no index → sequential scan** over 7 k+ nodes. The clean Postgres move is a **generated column** — zero app/SQL-write changes, instant index:
   ```sql
   ALTER TABLE nodes ADD COLUMN meeting_id text
     GENERATED ALWAYS AS (properties->>'meeting_id') STORED;
   CREATE INDEX nodes_meeting_idx ON nodes(meeting_id);
   ```
   Same candidate for a `scope`/`world` discriminator (B.6). Everything else stays in JSONB.
3. **JSONB `properties`** — the type-specific long tail: `source_location, community, confidence, graphify_id, owner, rationale, status, label`. Correct as-is; the flexible bag is the whole point of a polymorphic graph. If you start filtering on many different keys, add one GIN index: `CREATE INDEX nodes_props_idx ON nodes USING gin (properties jsonb_path_ops);` — don't promote them all to columns.

Litmus test: *"Do I filter/join on this across many types?"* → column (or generated column). *"Is this an attribute of one node type?"* → JSONB. *"Is this a different kind of record with its own lifecycle?"* → its own table (`events`/`sources`).

### B.5 Embeddings: decide, don't drift

The dead pgvector path caps retrieval quality. Two honest options:
- **(A) Commit to BM25 + trigram + graph expansion.** The snapshot already recommends leading with this; `/search` wires BM25→CTE. For the orchestrator's *entity resolution* ("Sunstead" → node) trigram is sufficient. Lowest effort; fix the docs to stop claiming semantic search.
- **(B) Wire a real embedder** (Voyage AI / OpenAI / local sentence-transformers — the adapter slot is already there in [`embeddings.py`](../central-kg-api/app/embeddings.py)) and backfill. This is the single biggest lever for "agents execute *great* queries" — semantic recall ("what relates to pricing-ish"), research-finding dedup, and grounding beyond exact names. Cost: one embed call per node on write + a backfill job; then `ANALYZE` and tune ivfflat `lists`.

Recommendation: **(A) now** (it unblocks grounding for the demo), **(B) as the top retrieval upgrade** when there's time. Don't half-claim semantic search meanwhile.

### B.6 Scope / multi-source correctness (know when it bites)

Everything merges by **global** `(type, lower(name))`. One repo, one meeting series: fine. The moment you ingest a *second* source-world — another repo, another org — `auth.ts:login` or a `person` named "Alex" collide across worlds. The `source_id` column records provenance but **isn't in the key**, so it can't prevent the merge. If multi-source is ever real, add a `scope` discriminator to the key (`UNIQUE (scope, type, lower(name))`) via the same generated-column trick. **Explicitly not needed for the current single-graph demo** — flagged so it's a known boundary, not a future surprise.

---

## Part C — Maximizing the agent/tool surface over this graph

The unifying idea: **every agent both reads grounding from and writes its conclusion to the graph.** That turns the KG into shared working memory (your cross-agent goal, done right) and makes the graph **grow live on camera** (a strong demo beat).

| Lever | What | Why it pays |
|---|---|---|
| **Templated SQL for known intents** | `who_changed`, `recent_changes`, meeting-scope reads → parameterized SQL, no LLM ([`git.py`](../agent-system/agent-runner/src/agent_runner/agents/git.py) docstring already wants this) | faster, cheaper, **less hallucination surface**, no live CTE authoring on stage (both reviews flag) |
| **Fetch-context stage** | orchestrator/agents resolve entities + pull meeting conclusions before acting | the grounding that fixes Sunstead; the shared-memory read |
| **Write conclusions back** | research→`research_finding` event, data→`insight` event, web→`website` node, all linked to the meeting | cross-agent substrate + live graph growth + more MCP write surface (rubric) |
| **Oversight → KG** | verdicts as `events` on the answer/task (DESIGN §7 "durable verdicts → KG") | queryable audit trail, more MCP surface, the conductor's memory |
| **Vector recall (when B.5-B lands)** | "what relates to X" for grounding + research dedup | the leap from exact-name to semantic memory |
| **Prompt-cache the KG schema** | the big, identical schema block in every agent's system prompt → cache it (§3.5 #3) | lower TTFT + cost on every repeat call |

Note the clean read-path split the scoping doc already drew, and keep it: **avatar = fast/synchronous/spoken via `central-kg-api` HTTP; suite = slow/async/artifact via Aiven MCP.** Don't merge those; they're different latency budgets.

---

## Part D — Streamline / optimize (reliability the orchestrator amplifies)

Chaining makes existing soft spots sharper — fix these alongside Part A:

- **At-least-once + durable dedupe.** Runner is at-most-once (`auto_offset_reset="latest"`, fire-and-forget commit) and dedupe is in-memory (`ctx._seen`, [`harness.py:40`](../agent-system/shared/src/shared/harness.py#L40)). A restart re-runs delivered tasks → **duplicate site builds**. With chaining, a re-run can re-fire a whole sub-DAG. Commit after `run()`; persist the dedupe set (a small Postgres table is on-rubric and survives restarts).
- **Round-trip budget.** Templated SQL (Part C) + prompt-cache (Part C) are the two biggest per-task savings; both are also accuracy wins.
- **Indexes for the new hot paths.** `meeting_id` generated column + index (B.4) is required the moment the orchestrator filters meeting memory; without it every grounding read is a seq scan.
- **ivfflat hygiene.** Harmless while empty; once embeddings land, `ANALYZE` and tune `lists` (~√N) or recall silently drops.

---

## Part E — Phased plan (ties orchestration + data model together)

Sequenced by leverage-per-hour, cheapest first. Each phase is independently shippable.

| Phase | Contents | Effort | Fixes |
|---|---|---|---|
| **0 — data-model correctness** | meeting-ops episode-key fix (B.3); `meeting_id` generated column + index (B.4) | ~1–2 h | the silent cross-meeting merge **bug**; meeting-scoped reads |
| **1 — context-grounded routing + effort** | transcript into the router; KG entity grounding (mech. 2); `effort` field threaded into research/KG/web (mech. 1); `model_mid` | ~½ day | hallucination + quick-vs-deep; no new process; demo-safe |
| **2 — the orchestrator** | planner consumes `agent.results`; per-meeting plan registry; DAG decomposition + result injection (mech. 5); research→`update_website` | ~1 day | chaining + cross-agent flow; the structural win |
| **3 — conclusions → KG + oversight → KG** | every agent writes its conclusion as an event/scoped node; verdicts as events (Part C) | ~½ day | live graph growth; shared memory; audit trail |
| **4 — clarify/assume + avatar voice** | the clarification surface (mech. 3) | ~½ day | the conversational "employee" polish |
| **(later)** | embeddings + backfill (B.5-B); `scope` key (B.6); templated SQL for demo intents | — | semantic recall; multi-source; on-stage reliability |

**Hackathon-aware caveat (carried from both snapshots):** the critical path to a judge is *operational* — re-auth the expired `AIVEN_TOKEN` (the whole MCP read path, and all of Phase 1's grounding, is red without it), commit the dirty tree, deploy the clickable loop. **Phase 0 is a genuine bug worth fixing now; Phase 1 ships tonight; Phases 2–4 are post-pitch** unless the demo specifically needs chaining. Confirm the token is green before building anything that reads the KG live.

---

## F. Decisions to confirm

1. **One graph, two layers** (entity `nodes` + episodic `events`/scoped-nodes) — agreed? This is the load-bearing modeling call.
2. **Episode-key fix now** (Phase 0) — the meeting-merge bug is live data loss; fix independent of everything else?
3. **Embeddings: option (A) BM25/trigram now, (B) real embedder later** — or invest in (B) up front?
4. **Orchestrator location** — fold into the planner process (recommended, lowest friction) vs a separate "conductor" service (DESIGN §7's longer-term shape)?
5. **Effort vocabulary** — `quick/standard/deep` (3) vs a finer scale? 3 is enough to start.

— Drafted by Claude (Opus 4.8), design pass over the agent suite + KG

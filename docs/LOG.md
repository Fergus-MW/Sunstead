# Sunstead — Log (the HISTORY)

> _Append-only. Newest at the top. Each entry is the reasoning behind a decision or build — intent and analysis,
> not just the diff — signed by the agent that did the work. Protocol: [README.md](README.md)._

---

## 2026-06-25 — Added `/search`: BM25→graph retrieval (the smart read path)

**What:** added `GET /search` to `central-kg-api` — OpenSearch BM25 over the mirrored `kg-nodes` index → node ids →
the existing recursive-CTE neighborhood (`subgraph_bfs`). Same `QueryResult` shape as `/query`, so it's a **drop-in
upgrade** for the avatar's `lookup_context` and the FE: change the path from `/query` to `/search`. New `app/search.py`
(BM25 client) + `app/routers/search.py`; `opensearch_url`/`opensearch_index` added to settings; one `include_router`
line in `main.py`. Verified: imports, OpenAPI shows `/search` resolving.

**Why:** the live `/query` was **trigram-only** — fuzzy *string* match on node names, weak for the natural-language
questions the avatar fields ("what's blocking the memory refactor?"). The strong path — OpenSearch BM25 → 2-hop CTE,
which the retrieval bench measured at ~125 ms — existed *only in the benchmark*, used by no product code. This wires
that proven path into a real endpoint so the on-stage avatar answers feel smart, not fuzzy. Architecturally clean:
the avatar is a human-facing HTTP client by design (DESIGN §2 two-layer framing), so a smart `central-kg-api`
endpoint upgrades it **without** touching the agents'-via-MCP mandate.

**Analysis / consequences:** built as a **strict upgrade, never worse** — if `OPENSEARCH_URL` is unset or OpenSearch
is unreachable, `bm25_node_ids` returns `None` and the route falls back to the same trigram `hybrid_search` `/query`
uses, so nothing breaks. Field boosts mirror the bench (`name^3, source_file^2, label, text`); `text` is the
utterance/commit body once the demo-meeting layer is mirrored (still on `feat/demo-data-layers`), absent fields are
ignored. Reuses only public graph functions (`subgraph_bfs`, `hybrid_search`) — **did not touch** the hot in-flight
files (`graph.py`, `routers/subgraph.py`). Follow-ups: point the avatar's `backend.py` `lookup_context` at `/search`
(one line, owner's file); ensure the OpenSearch mirror has run so the index exists; optional blended scoring
(BM25 ⊕ trigram) later. To verify once `OPENSEARCH_URL` is set:
`curl "$KG/search?q=streaming+retry+policy&hops=2"`.

**Touches:** `central-kg-api/app/{search.py (new), routers/search.py (new), config.py, main.py}`, `docs/LOG.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Built the planner: the transcript → delegation brain (the missing upstream)

**What:** added `agent_runner.planner` — a third long-lived process alongside the runner and gateway. It tails
`meeting.transcript`, runs one LLM turn (a forced `propose_tasks` tool call on `MODEL_SMART`) to decide whether an
utterance is actionable, and publishes the resulting `task.create`(s) onto the right `agent.tasks.*` topics. One
utterance can fan out to **N tasks** ("build a page *and* tell me who owns auth" → two), grouped under one `plan_id`
via the existing-but-unused `parent_task_id`/`depth` fields. Each delegation also emits an `agent.activity`
(`status="delegated"`, `detail=reason`) so the dashboard renders the brain deciding, live. Added `scripts/say.py`
(+ `make say T=…`, `make planner`) which publishes a `meeting.transcript` exactly as the avatar's STT will — so the
**entire delegation loop is demoable today, before the avatar branch merges**. Verified: both files compile, the
module imports against the real `shared` package, and the intent vocab resolves (echo excluded, nine real intents).

**Why:** the gap analysis kept naming "the avatar doesn't delegate" as the one open seam — but the real missing
piece was never another agent, it was an **orchestration brain**. Delegation was `intent string → one topic → one
agent`; a spoken sentence is not a clean intent. Without a component that listens to the transcript and *decomposes*
speech into tasks, `meeting.transcript` had no consumer and `parent_task_id`/`depth` sat unused. This is the spine
that turns the avatar from "answers questions itself" (bypassing the suite) into "listens, delegates, supervises" —
which is also the stronger "AI employee" demo. Built as a **peer consumer**, deliberately touching none of the
proven runner→results loop.

**Analysis / consequences:** three judgment calls. (1) The planner reads its own group at **`auto_offset_reset=
earliest`** so a request spoken before it connected isn't dropped — a dropped request is a broken promise. This is
the at-least-once-style delivery improvement applied where it's *new and safe*; I deliberately did **not** flip the
runner's `latest→earliest`, because on a fresh group that would replay every historical task (rebuilding old sites)
and correct manual-commit under the runner's bounded concurrency is a real change, not a one-liner — it deserves its
own reviewed, env-gated, default-off entry. (2) Transcripts are processed **sequentially with an envelope-id
dedupe**, so a redelivered line can't double-delegate (no duplicate websites); the trade-off is no fan-out on the
planner itself, fine for demo throughput. (3) Acts only on `transcript.final`/`is_final` and ignores sub-8-char
lines — partials are noise. The system prompt is intentionally conservative (ordinary conversation → zero tasks);
the obvious next tuning is multi-line aggregation (a request spanning two utterances) and a per-meeting context
window. Natural follow-ups, unchanged by this entry: close the `kg.updates` loop so delegated work flows back into
the graph (the flywheel), and point the avatar's `delegate()` at the same path (or let it just publish transcripts
and let the planner do the rest — this makes option (b) viable without the gateway).

**Touches:** `agent-system/agent-runner/src/agent_runner/planner.py` (new), `agent-system/scripts/say.py` (new),
`agent-system/Makefile`, `docs/LOG.md`. (No infra committed; no changes to `shared/**`, the runner, or the gateway.)

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Hardened the gateway WS feed (shared consumer + replay buffer)

**What:** rewrote `agent_runner.gateway`'s `WS /stream` from a per-connection consumer to **one shared broadcast
consumer + a bounded replay ring + per-socket queues** (`Hub`). On connect, a browser replays the recent ring (last
200 envelopes), then streams live; replay/live overlap is de-duped by envelope id. Added `scripts/gateway_smoke.py`
(+ `make gateway-smoke`) — a no-Kafka, no-server in-process check of replay / no-dup / meeting-filter / fan-out; all
pass.
**Why:** the dashboard is being wired to the real Aiven Kafka now, and the old design had three latent demo-day
flakes: (1) a *fresh consumer group per connection* with `auto_offset_reset=latest` could miss a fast
`agent.results` produced in the gap before the group finished joining → "I clicked ask and nothing appeared"; (2) one
malformed frame threw inside the consume loop and tore down the socket for all later messages; (3) the FE's
auto-reconnect spun a new Kafka consumer group per reconnect (group sprawl). The ring buffer makes the feed
deterministic for a recorded demo — you can open the dashboard *after* asking and still see the result.
**Analysis / consequences:** the WS endpoint is now a trivial `async for text in hub.stream_for(meeting_id)`; the
testable logic lives on `Hub`. A unique consumer group per process keeps every gateway instance a full-stream
broadcaster (not work-sharing). Per-socket queues are bounded (drop on a stalled browser rather than back up the
consumer). The contract the FE depends on is unchanged: `/tasks`, `/health`, `/stream?meeting_id=`, raw envelope
JSON frames. Verified: import OK, routes intact, smoke passes. Left uncommitted alongside in-flight FE/KG work; did
not touch the hot files (`graph.py`, `meet-joiner/graph/**`, `config.py`/`mcp.py`).
**Touches:** `agent-system/agent-runner/src/agent_runner/gateway.py`, `agent-system/scripts/gateway_smoke.py`,
`agent-system/Makefile`, `docs/LOG.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — MCP toggle ON → 34% slice live; created Kafka topics via MCP

**What:** the org enabled "Allow MCP connection," and `aiven_pg_read` immediately returned real data
(`code_module` 5236, `decision` 841, `commit` 500, …). `make ask` now answers genuine questions off the live graph
— e.g. *top authors: stainless-app[bot] (387), dtmeadows (32), Robert Craigie (23); latest commit touched
`src/anthropic/_version.py`*. **The 34% MCP-depth showcase is proven end-to-end on real data.** Also discovered
**Aiven Kafka already exists** (`kafka-254bd14f`, RUNNING — the snapshot's "not provisioned" was stale), and
created all 9 of our topics on it via `scripts/provision_topics.py` (MCP `aiven_kafka_topic_create` — infra stood
up through MCP tool calls, the 33%-autonomy flavor).
**Why:** this turns the architecture from "designed" to "demonstrated" — an agent reaching live infra natively via
Aiven MCP, both read (Postgres) and control-plane (Kafka topics).
**Analysis / next:** the dispatch bus now exists on real Aiven Kafka; the remaining wire is connecting the
agent-runner/gateway to it (bootstrap + SASL/mTLS creds — fetchable via MCP `aiven_service_get` with
`AIVEN_ALLOW_SECRETS`). web-agent (the deliverable) is being built in parallel. Once both land + Ferg adds the
`delegate()` tool, the full mid-call flow is wired.
**Touches:** `agent-system/scripts/provision_topics.py`, `docs/LOG.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Ran the whole local stack end-to-end; made the web-agent real

**What:** brought the full local system up and verified every seam with real round-trips, then implemented the
web-agent. Proven live: Kafka smoke; runner echo loop; gateway `POST /tasks` → Kafka → runner → `WS /stream`; the FE
dashboard's `/api/tasks` proxy → gateway; **git-agent** answering off the live ~6k-node graph via `aiven_pg_read`
(top authors 387/32/23 commits) once the operator enabled the org's *Allow MCP connection* toggle; **central-kg-api**
against the seeded Aiven PG so `/graph` renders real `code_module` nodes; and a **new web-agent** that asks Claude for
a one-file HTML site, writes it to `SITES_DIR/<task_id>/`, and returns a `url` artifact — fetched back at 200, 16 KB,
shown on the dashboard task board. Added `scripts/serve_sites.py` (+ `make sites`/`make web`, `SITES_*` env).

**Why:** the operator flagged that *nothing had been run locally or deployed*. The gap analysis said the binding
risk was integration, not capability — so the highest-value move was to actually run it and close the cheapest
remaining seam (the web-agent deliverable, the 33%-creativity "wow"). Chose a **self-contained static server over a
Vercel deploy** so the demo needs no extra token and runs fully offline; the write+url step is a clean swap-point for
a real Vercel deploy later.

**Analysis / consequences:** the worker half of the delegation flow is now *proven*, not aspirational — the only
piece left for the headline "delegate mid-call" is the avatar's `delegate()` → gateway `POST /tasks` call. Two
operational facts worth keeping: (1) the runner must have `ANTHROPIC_API_KEY`+`AIVEN_TOKEN` in its **process env**
(sourced from the repo-root `.env`; an empty `agent-system/.env` value shadows it — already addressed in the loader),
and (2) `central-kg-api` reaches PG over a **direct asyncpg** connection (`postgresql+asyncpg://…?ssl=require`), which
is *not* gated by the Aiven MCP toggle — so `/graph` works independently of the 34% MCP path. A credential-fetch
helper that scraped the PG password via MCP was correctly blocked by the safety classifier; the operator supplied the
URL instead. The Aiven MCP org toggle remains the single switch gating both the 34% (KG reads) and 33% (provisioning).

**Touches:** `agent-system/agent-runner/.../agents/web.py`, `agent-system/scripts/serve_sites.py`,
`agent-system/{Makefile, .env.example, .gitignore}`, `docs/OVERVIEW.md`. (Local-run only; no infra committed.)

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Thin vertical slice runs end-to-end; only blocker is an Aiven org toggle

**What:** built `scripts/ask.py` (question → git-agent → Aiven MCP → live KG → answer; **no Kafka, no Docker**) and
ran it. `mcp-aiven` launched on Windows with the static `AIVEN_TOKEN`, the Haiku tool-runner called `aiven_pg_read`,
and the agent produced a coherent answer. **The §11 hosted-MCP auth risk is retired in practice.**
**The one blocker:** `aiven_pg_read` / `aiven_service_get` return `403 — MCP connections are disabled by your
organization administrator`. Control-plane *list* ops work; per-service access is gated by **Aiven Console → Admin
settings → Authentication → Allow MCP connection**. That single toggle gates both the KG queries (34%) and service
provisioning (33% autonomy).
**Also fixed (config robustness):** the `.env` loader now merges cwd→root `.env` nearest-wins, **skips empty and
comment-only values**, and ignores a `base_url` that isn't a real URL. Two real bugs surfaced: a blank
`agent-system/.env` (a `cp .env.example .env` copy) was *shadowing* the real keys in the root `.env`, and an
inline-comment-as-value had become a junk `ANTHROPIC_BASE_URL` the SDK picked up from the env and failed to connect
to. Cleaned `.env.example` (comments on their own lines — inline comments corrupt dotenv values).
**Analysis:** every layer — MCP launch, the LLM, tool-calling, the agent loop — is verified; the slice is one
console toggle away from a real answer off the live ~6k-node graph. This is the 34% showcase, proven up to the gate.
**Touches:** `agent-system/{scripts/ask.py, shared/src/shared/config.py, .env.example, Makefile}`, `docs/LOG.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Built the FE admin surface: KG graph explorer + agent dashboard

**What:** turned the thin `meet-joiner` bot-launcher into a real admin surface, in three commits. (1) A
**knowledge-graph explorer** (`/graph`): search → `central-kg-api` `/subgraph` → an interactive force graph; click a
node → `/entity/{id}` → expand neighbors. (2) An **agent dashboard** (`/dashboard`): an auto-reconnecting WS client
on the gateway's `WS /stream` rendering a live `agent.results`/`agent.activity` feed + a per-`task_id` status board,
plus an **ask box** that dispatches `task.create` via `POST /api/tasks` → gateway → Kafka. Server-side proxy routes
(`/api/graph/*`, `/api/tasks`) keep backend URLs + CORS off the browser; the WS connects directly.

**Why:** the project's strongest work (MCP-native git-agent, the 6k-node live graph, the Kafka swarm) is **invisible**
— it lives in logs. The Aiven challenge is judged remotely from a video + written submission, so a surface that
*renders* the graph and the live bus converts existing depth into something a judge can see. The operator chose
"graph explorer first," so it shipped standalone before the gateway-dependent panels.

**Analysis / consequences:** chose a **zero-dependency hand-rolled canvas force graph** over a library (react-force-
graph / Cytoscape) — `meet-joiner/AGENTS.md` warns the Next is modified, so avoiding SSR/dep landmines and matching
the existing inline-SVG aesthetic won. Built deliberately **resilient**: the WS hook backs off and reconnects, so
the dashboard is usable *before* the gateway/Kafka exist and self-heals when they come up — this matters because
**nothing here has been run e2e or deployed yet** (operator flagged it). Reverted npm-install's platform-specific
`package-lock.json` churn from each commit. Discovered mid-build that a teammate landed the real
`agent_runner.gateway` (`POST /tasks` + `WS /stream`, port 8800) — the dashboard targets that exact contract; those
agent-system edits were left for their author, not committed here. Open practicalities to patch once the bus is live:
gateway CORS/auth, `wss://` in prod, the WS `meeting_id` filter, and whether `/api/tasks` should carry auth.

**Touches:** `meet-joiner/src/app/{graph,dashboard}/**`, `meet-joiner/src/app/api/{graph,tasks}/**`,
`meet-joiner/src/app/page.tsx`, `meet-joiner/{.env.example,README.md}`, `docs/OVERVIEW.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Built the FE / delegation gateway

**What:** added `agent_runner.gateway` — a small FastAPI app: `POST /tasks` (produce `agent.tasks.*`) and
`WS /stream` (tail `agent.results` + `agent.activity` to the browser). One warm Kafka producer; a per-connection
consumer group for the WS.
**Why:** the roadmap needs this front door *twice* — it's the bridge for the avatar→worker delegation seam
(option a: the avatar's `delegate()` tool POSTs here) **and** the FE result/status feed. Building it first unblocks
both the demo wire and the overlay.
**Analysis / consequences:** HTTP at the edge, Kafka in the core — the gateway is the only translation point, which
keeps Ferg's avatar HTTP-native and the worker suite Kafka-internal (DESIGN §3). Verified locally (routes construct,
intent→topic mapping, deps resolve). Next wires: point the avatar's tool at `POST /tasks`; make web-agent real so
the delivered thing is worth showing.
**Touches:** `agent-system/agent-runner/{gateway.py, pyproject.toml}`, `agent-system/Makefile`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Established the OVERVIEW / DESIGN / LOG doc system

**What:** split the docs by tense — OVERVIEW (now / HEAD), DESIGN (future / target), LOG (this, the why) — with a
governing README and a sign-off protocol. Existing PLAN / AGENT_SYSTEM / HACKINFO / CENTRAL-KG-API become deep
references to be folded into DESIGN over time.
**Why:** the project had ~2,000 lines of excellent but *fragmented* planning that described a system more wired
together than the code is. New agents (and teammates) had no single "where are we / where are we going" entrypoint,
and decisions lived only in chat + git, losing the intent. Adopted from the operator's other repos (the `log.md`
pattern: a history with more insight than `git log`).
**Analysis / consequences:** DESIGN is now the forward source-of-truth (it *wins* over PLAN/AGENT_SYSTEM on
conflict), which is what lets us consolidate the fragments without a big-bang rewrite. OVERVIEW must be kept honest
(it currently says, plainly, that the pillars don't connect). This entry is the first sign-off.
**Touches:** `docs/README.md`, `docs/OVERVIEW.md`, `docs/DESIGN.md`, `docs/LOG.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Snapshot review: reframed the work as integration, not building

**What:** reviewed an automated repo/plan/git snapshot. It confirmed the architecture analysis and added three
facts: the KG is **already seeded live in Aiven** (~6,183 nodes, ~125 ms retrieval); **Aiven Kafka is not
provisioned** (no free tier); the best code (avatar, demo-data) is **unmerged**. Adopted its "three wires and a
merge" framing into DESIGN §Roadmap.
**Why:** the binding constraint is the pitch deadline + integration latency, not capability. The snapshot made the
priority order concrete and rubric-aligned: merge → provision Kafka on camera → web-agent → one delegation wire.
**Analysis / consequences:** two refinements on top of the snapshot — (1) the "who touched auth" *read* needs no
delegation (the avatar can answer it itself), so the seam's only real job is heavy work (web-agent); (2) the MCP
contradiction is resolved by the two-layer framing rather than re-pointing the avatar at MCP. The un-provisioned
Kafka is reframed from a gap into the 33%-autonomy moment.
**Touches:** `docs/DESIGN.md` (roadmap, two-layer framing), `reviewmd/…-snapshot.md` (input).

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Found the avatar-agent off-architecture; proposed the two-layer seam

**What:** reviewed `ferg/avatar-agent` (LiveKit + Recall + Anam, STT→Sonnet→TTS, full Terraform — strong and nearly
done). Key finding: it reaches the KG **over HTTP to `central-kg-api`**, emits **no Kafka**, and **does not delegate**
to the agent suite. So the agent suite has no upstream producer.
**Why it matters:** our whole PLAN seam ("the listener produces `agent.tasks.*`") has no producer; the demo's
headline flow is two halves with no bridge. The avatar also contradicts the "agents via MCP only" mandate.
**Decision/analysis:** resolved with a **two-layer architecture** — realtime avatar (HTTP, human-facing, latency-
bound) + async worker suite (Kafka + MCP, the 34% depth). The bridge is a single `delegate()` tool; recommended
**(a) HTTP→gateway→Kafka** over (b) avatar-produces-Kafka, because the gateway is needed for the FE anyway and (a)
keeps Ferg's finished container untouched. The a/b choice only affects one external edge — worker↔worker stays Kafka.
**Touches:** analysis only (no code yet); to be encoded in DESIGN §3.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Rebuilt agent-system as one container + local-first foundation

**What:** realigned the scaffold to the container model. `shared/`: contracts (envelope + `workspace_id`/
`idempotency_key`/`ActivityPayload`), `kafka` (PLAINTEXT/SASL), `mcp` (warm local `mcp-aiven` stdio; programmatic +
LLM-exposed), `harness` (validate→dedupe→act→emit, direct-Kafka emits), `sessions`. `agent-runner/`: long-lived
consumer → harness → `agents/{echo,git,web,data}` with bounded asyncio concurrency. Dropped `kg_client`; removed
call-gateway/listener (teammate's). Local dev: redpanda compose, scripts, Makefile. Verified: `uv sync`, imports,
envelope round-trip.
**Why:** the realtime requirement made a long-lived container strictly better than Lambda (web builds need durable
FS + minutes-long loops; a container can hold a real Kafka consumer and a warm MCP session). Running `mcp-aiven`
locally with a static `AIVEN_TOKEN` also **dissolved the hosted-MCP OAuth risk**.
**Analysis / consequences:** `shared/kafka.py` went from "drop it" (Lambda plan) to central (the consumer). The
git-agent + echo prove the loop; web/data are stubs. Foundation is committed and merged to `main`.
**Touches:** `agent-system/**`, `docs/AGENT_SYSTEM.md`, `docs/PLAN.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Pivoted to a container + pure-Kafka + realtime-first model

**What:** moved the agent suite from Lambda-workers to **one long-lived container**, with **pure-Kafka dispatch**
and an explicit **realtime latency budget** (§3.5 of AGENT_SYSTEM): the LLM tool-loop is only for reasoning; the
harness never spends an LLM round-trip on plumbing (emits are direct Kafka).
**Why:** Lambda's ephemeral FS + 900 s ceiling fight web builds; a container is one deploy to debug and removes the
hosted-MCP auth dependency. Pure Kafka keeps the "No-Backend App Swarm" framing the Aiven rubric rewards.
**Analysis / consequences:** the one honest exception — the container's task-ingest is a direct consumer (a Lambda
couldn't run a consumer loop; a container should). Every *data op* still goes through MCP, which is what the 34%
measures.
**Touches:** `docs/AGENT_SYSTEM.md` (rewrite), `docs/PLAN.md` (§3.6, §8).

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Adopted the Aiven MCP-native mandate (§3.5)

**What:** made it binding that agents reach data **via Aiven MCP** (`aiven_pg_read`/`_write`, Kafka, provisioning),
not via hand-written backend HTTP. `central-kg-api` demoted to ingestion sidecar + FE bridge. Verified the real
tool names (`aiven_pg_read`, `aiven_kafka_topic_message_produce`, …) against the Aiven MCP server — the original
plan's placeholder names were wrong.
**Why:** the challenge explicitly penalizes "backend boilerplate between an agent and a DB/queue." MCP depth is the
34% we control most directly, so it has to be the spine, not a side-call.
**Analysis / consequences:** this is the doctrine the later two-layer framing refines (the *async* suite is MCP-
native; the *realtime* avatar is a human-facing HTTP client). Set the direction for everything after.
**Touches:** `docs/PLAN.md` §3.5, `docs/HACKINFO.md`.

— Claude (Opus 4.8), signed off

# Sunstead — Log (the HISTORY)

> _Append-only. Newest at the top. Each entry is the reasoning behind a decision or build — intent and analysis,
> not just the diff — signed by the agent that did the work. Protocol: [README.md](README.md)._

---

## 2026-06-25 — Oversight phase one: grounding verifier, control channel, mission-control UI

**What:** built the first three pieces of the oversight design (DESIGN §7). (1) **Grounding verifier** — a pre-emit
gate in the harness: a specialist that wants its answer checked returns `_verify={claim, evidence}`; the harness runs
a fast-model JSON-schema judge (`shared/verify.py`) and attaches a `Verdict{grounded, confidence, evidence_count,
note}` to the result. The KG/git agent captures the rows each `aiven_pg_read` returns via a transparent
`_RecordingSession` proxy (`shared/mcp.py`) — the evidence was otherwise discarded inside the tool-runner. (2)
**Control channel** — new `agent.control` topic + `ControlPayload`; the gateway gained `POST /control` (FE
`/api/control` proxy + a per-card "stop" button), and the runner now tracks running tasks by `task_id` and runs a
**broadcast** control consumer that `task.cancel()`s the owner; the harness catches `CancelledError` and emits a
terminal "cancelled" result. (3) **Mission-control dashboard** — a live digest bar (active/stuck/grounded/flagged on a
2s tick — a client-side conductor), a tiled agent board with verdict badges + collapsible reasoning + stop buttons.

**Why:** the operator asked to build the oversight work well, then overhaul the UI. The verifier was first by the §7
priority (highest trust-per-effort: it *enforces* the KG agent's standing promise to "never invent people, files,
decisions", which nothing did before — and the cost of a bad answer here is a false claim spoken in a live meeting).
The control channel is the one structural addition that turns oversight from observe-only into *act*. The dashboard
overhaul makes the whole thing legible — and the digest *is* the human it replaces.

**Analysis / consequences:** verification is **annotate-only / fail-open** — it never blocks and any verifier error
returns a neutral verdict, so a flaky check can't fail a good answer; it only runs when an agent supplies evidence (web
builds cost nothing). The recording proxy captures evidence per-task without coupling to tool-runner internals.
Cancellation rides asyncio: agents are cancellable at every await (stream/tool boundary) for free — no per-agent flag
checks. Control is a **broadcast** group so any runner instance that owns the task honours the stop. Verified: backend
compiles + `Verdict`/`ControlPayload` round-trip through the envelope; FE `tsc` + `eslint` clean. **Still FE-side, not
backend:** the conductor's *judgement* (stuck/conflict → auto-cancel) is computed in the digest, not a service — that
promotion, plus a `redirect` control verb and the swimlane view, is the remaining §7 work.

**Touches:** `agent-system/shared/src/shared/{config,contracts,harness,verify,mcp}.py`,
`agent-system/agent-runner/src/agent_runner/{runner,gateway}.py`, `agent-runner/.../agents/git.py`,
`meet-joiner/src/app/dashboard/{page.tsx,types.ts}`, `meet-joiner/src/app/api/control/route.ts`, `docs/{DESIGN,LOG}.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Shipped reasoning streaming; set the oversight direction (gate, not steer)

**What:** added `agent.trace` — agents now stream **adaptive-thinking + output deltas** over Kafka (new `TracePayload`,
a per-task `ctx.trace()` with a monotonic `seq`, and a `shared/streaming.py` helper the web-agent uses; the git/KG
agent forwards per-turn blocks since its tool-runner returns whole messages). The gateway tails `agent.trace`; the FE
folds deltas into a per-task **reasoning panel** (thinking above the streamed answer, seq-ordered + replay-idempotent,
kept out of the events ring so it can't evict task state). Then analyzed where oversight fits and recorded the
direction in **DESIGN §7**.

**Why:** the operator asked whether agents should see each other's reasoning and whether an *overseer* fits this build.
Streaming reasoning is the enabler for both watching and judging. On oversight: a coding-harness *steering* overseer is
a weak fit — worker tasks are seconds long, so there's nothing to steer before they finish. A **verifier** (grounding
gate) + **conductor** (fleet coherence) fit strongly, *because* the cost of a bad step here is a false claim **spoken
in a live meeting**, and the git/KG agent already promises not to invent facts but nothing enforces it.

**Analysis / consequences:** oversight here = **gate + flag, not steer**; three guards by altitude — *tripwire*
(reasoning/process), *verifier* (output/product), *conductor* (fleet) — each just another Kafka consumer, so zero
structural change. The realtime/async tempo split (DESIGN §2) bars a slow verifier from the **speech path** — gate
async/FE results, keep speech cheap or post-hoc. Fail-open by default (a wrong overseer suppressing good answers is
worse than an occasional hedge); project verdicts into the KG for a durable, queryable audit trail + extra MCP surface.
Cross-agent context wants *conclusions* (KG nodes), not raw traces. Surfaced adjacent gaps: in-memory idempotency
(`_seen`) re-runs tasks on restart; trace doesn't reach TTS yet (the §3.5 speech payoff); the KG agent LLMs even known
retrievals. The one structural addition implied is an `agent.control` topic — the prerequisite to *act* rather than
only flag. Full roadmap in DESIGN §7.

**Touches:** `agent-system/shared/src/shared/{config,contracts,harness,streaming}.py`,
`agent-system/agent-runner/src/agent_runner/{gateway.py,agents/{web,git}.py}`,
`meet-joiner/src/app/dashboard/{types.ts,useStream.ts,page.tsx}`, `docs/{DESIGN,LOG}.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Made the planner the single delegation brain; the avatar emits transcript

**What:** resolved the "two brains" hazard (DESIGN §6) by deciding **the planner is the single delegation brain**,
and wired it. (1) The gateway gained **`POST /transcript`** — it publishes one utterance to `meeting.transcript`
(and the broadcast Hub now also tails `TRANSCRIPT`, so the FE feed sees it). (2) The avatar's `GatewayClient` gained
**`publish_transcript`**, and `agent.py` now hooks LiveKit's **`user_input_transcribed`** event to POST each *final*
user utterance to the gateway (best-effort, off the hot path — failures are logged, never break the call). (3)
`delegate()` is **no longer always-on**: tools split into `BASE_TOOLS` (read tools, always) and an opt-in `delegate`,
gated by **`AVATAR_DELEGATES`** (default false); the system prompt switches between a "the team picks it up from the
meeting" clause and a "use the delegate tool" clause to match.

**Why:** two independent brains (the avatar's `delegate()` and the planner tailing `meeting.transcript`) would
double-delegate the same utterance. Choosing the planner makes the **same path serve `mock_meeting` and the real
avatar** — the local mock now literally exercises production routing — and keeps the avatar a thin HTTP client (it
emits transcript over the gateway, never speaks Kafka). It also lights up the FE transcript feed for free.

**Analysis / consequences:** the avatar stays an HTTP edge (no Kafka deps); the gateway is now the single ingress for
*both* tasks and transcripts. `AVATAR_DELEGATES=true` remains for an avatar-routes-itself deployment, but then the
planner must be run OFF. Verified: avatar config defaults to planner-brain, `delegate` is excluded from `BASE_TOOLS`,
`publish_transcript` degrades when the gateway is unconfigured, and the flag flips the tool set. Still open: point
runner/gateway at Aiven Kafka; land `demo-data`.

**Touches:** `agent-system/agent-runner/src/agent_runner/gateway.py`, `avatar-agent/src/avatar_agent/{agent,gateway,config,tools}.py`,
`avatar-agent/{.env.example,README.md,tests/test_tools.py}`, `docs/{DESIGN,AVATAR_DELEGATION}.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Landed the avatar, wired the delegation seam, and made the whole pipe testable locally

**What:** brought the realtime layer onto `main` and closed the seams that turn three pillars into one system.
(1) **Landed `avatar-agent/`** by checking out only that directory from `origin/ferg/avatar-agent` — *not* a
wholesale merge (the branch is 1 commit past the fork while `main` is 23 ahead, and its `central-kg-api`/`meet-joiner`
are pre-fork; a merge would have clobbered newer code). (2) **Wired the avatar→worker delegation seam** (DESIGN §3
option a): a `delegate(intent, brief)` `@function_tool` + a `GatewayClient` + `GATEWAY_URL` config; intents map to the
arg key each agent reads via `DELEGATE_ARG_KEY` (web→`brief`, git/data→`question`). (3) **Settled the `meeting_id`
seam** as one source of truth in `dispatch.py` (`meeting_id_for`/`_for_room`/`_room_for`), threaded room→delegate→
gateway→FE so a delegated result correlates to the call. (4) **Soniox** is now the default STT (`STT_PROVIDER`,
`stt-rt-v5`); Deepgram is the fallback. (5) **Anam wired**: config loads the repo-root `.env` vault, accepts
`ANAM_API_TOKEN` (alias of `ANAM_API_KEY`), and defaults `ANAM_AVATAR_ID`. (6) **FE**: `/api/join` dispatches to
`AVATAR_DISPATCH_URL` (degrades gracefully) and the dashboard scopes by `?meeting_id=`. (7) **`mock_meeting`** +
`make mock`: a one-terminal Google-Meet stand-in (speak → `meeting.transcript` → planner → agents → watch results),
no Recall/LiveKit. (8) **`SINGLE_EC2.md`** runbook consolidating the avatar onto one box. (9) A **full-repo review**
fixed the FE `INTENT_TEMPLATES` (4 arg-key bugs — `update_website`/`read_git`/`blame`/`recent_changes` sent keys the
agents never read) and reconciled the docs to reality (this entry + DESIGN/OVERVIEW/DEPLOY/CENTRAL-KG-API edits;
replaced the obsolete `AVATAR_DELEGATION.md` handoff with a "shipped" pointer).

**Why:** the gap was never capability — it was integration. The avatar already worked; the one missing wire was a
tool that hands work to the suite. Reusing the avatar's native HTTP-tool idiom (option a) over a Kafka producer in the
realtime container keeps Kafka internal and adds zero deps. The `mock_meeting`/planner path means the entire pipe is
testable today with only `ANTHROPIC_API_KEY` + `AIVEN_TOKEN` — the realtime trio (Recall/LiveKit/Cartesia) is the
*only* thing it stubs, which is also the costliest to stand up.

**Analysis / consequences:** there are now **two delegation brains** — the avatar's `delegate()` and the `planner`
(tails `meeting.transcript`). They must not both fire on one utterance; the open call (DESIGN §6) is to make the
**planner the single brain** (avatar emits transcript) so the mock literally exercises the production path. Still
open: point runner/gateway at Aiven Kafka (bootstrap + SASL), land `demo-data`.

**Touches:** `avatar-agent/**`, `agent-system/scripts/mock_meeting.py`, `agent-system/Makefile`,
`meet-joiner/src/app/{api/join,page.tsx,dashboard}/**`, `docs/{OVERVIEW,DESIGN,DEPLOY,CENTRAL-KG-API,AGENT_SYSTEM,AVATAR_DELEGATION}.md`.

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Proved the delegation loop end-to-end in Docker; killed the empty-base_url footgun

**What:** ran the full spoken-sentence → delegation flow on the containerized stack and verified it from the bus,
not the logs. Brought up `docker compose up` (redpanda + topics + runner + planner + gateway + sites), injected a
transcript via `scripts/say.py`, and traced it through Kafka: the **planner turned one utterance into two tasks
under a single `parent_task_id` (`plan_…`), correctly routed** (`build_website` → `agent.tasks.web`, `who_changed`
→ `agent.tasks.git`) with cleanly rewritten args, both stamped `requested_by:"planner"`. The **runner executed
both** (LLM calls `200 OK`); the **web-agent completed the full happy path** — a real site published to
`http://localhost:8810/tsk_…/` (~17 KB) returned as a `url` artifact on `agent.results`. The git-agent ran and the
LLM answered, but its `aiven_pg_read` came back "auth token expired" — an **Aiven credential issue, not a pipeline
issue** (`AIVEN_TOKEN` needs refreshing / the org MCP toggle re-enabled). Verified each hop by reading topic
watermarks and message bodies with `rpk` (echo round-trip, the two `task.create`s, the matching results).

**Why:** "it builds" and "it works" are different claims; the operator asked to actually test e2e, and the only way
to trust the loop is to watch a message traverse every topic. The no-creds **echo** round-trip proved the bus
(publish → `agent.tasks.dev` → runner → `agent.results`) independent of any LLM, then the planner/web/git path
proved the real thing on top of it.

**Analysis / consequences:** the test surfaced a genuine deployment bug worth recording. The agents failed at first
with a misleading `APIConnectionError: Connection error.` whose real cause was `UnsupportedProtocol: Request URL is
missing an 'http://' protocol` — because **`agent-system/.env` carried an empty `ANTHROPIC_BASE_URL=`**, and the
Anthropic SDK reads that env var directly and treats `""` as the base URL. This never bites on the host (our `.env`
loader *skips* empty values) but **docker compose's `env_file` passes empties through** — a host/container parity
gap. Fixes, defense-in-depth: (1) `shared/config.py` `__post_init__` now **scrubs** a non-URL/empty
`ANTHROPIC_BASE_URL` from `os.environ` so the SDK can't pick it up (verified in-image: with `ANTHROPIC_BASE_URL=""`
set, settings clear it and the env var is removed); (2) `.env.example` no longer ships an empty assignment — the key
is commented out with a warning. Image rebuilt so the guard is baked in; stack recreated onto it. Also confirmed a
real operational fact: containers **cannot** see the repo-root `.env` (only `agent-system/.env`), so creds the host
loader merges up the tree must be present in the file the container reads. **Status:** delegation + web path proven
in Docker; the lone red is the stale Aiven token (operator action). Not changed: runner at-least-once, registry,
contracts.

**Touches:** `agent-system/{.env.example}`, `agent-system/shared/src/shared/config.py` (env scrub; co-edited),
`docs/LOG.md`. (Verification only — no infra deployed; the Docker image/compose from the prior entry unchanged
except the rebuild.)

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Made the KG explorer intuitive: lands populated, reads as structure, has view controls

**What:** turned the graph explorer from "type a query at a blank canvas" into a surface a judge can read on
arrival, across two passes. **Backend:** added `GET /overview` to `central-kg-api` (`graph.overview_centers` seeds
from the highest-degree hub nodes, falls back to most-recently-updated when there are no edges) so the FE has a
no-query landing view; a Next proxy (`/api/graph/overview`) fronts it. **FE (`meet-joiner/graph`):** the page now
**auto-loads the overview on mount** (with an "Overview" reset + example chips); a redesigned **NodePanel** surfaces
humanized properties (raw JSON behind a toggle) and — the key move — a **clickable connections list** that doubles
as graph navigation; the canvas gained **directional arrowheads, focused-edge relationship labels, a hover tooltip,
on-canvas zoom/fit/reset, and an interactive type-filter legend with counts**. Second pass fixed the lived-in
complaints: replaced the jarring auto-fit **snap** with a **smooth eased camera** (split `view`/`target` + a settle-
follow window that glides the frame out as the layout expands), retuned the force constants and added **hub emphasis
+ connectivity-based sizing** so structure reads even zoomed out, added **view modes** (color by Type / Links /
Minimal) and a **Display settings menu** (label density, edge-label mode, hide-unconnected, spacing), and fixed a
**property-panel text-clipping bug** (flex children lacked `min-w-0`, so shas/emails overflowed instead of wrapping).
Verified end-to-end via headless-Chrome screenshots against the live Aiven graph (140-node overview, panel, color
modes, settings).

**Why:** the Aiven challenge is judged remotely from a video + written submission, so the explorer's job is to make
the project's strongest invisible asset — the live ~6k-node graph — *legible at a glance*. Three operator complaints
drove the work and each was a real UX failure, not a preference: (1) "I have to produce a query to see anything" —
a graph tool that opens empty teaches the viewer nothing; the hub-seeded overview is the fix. (2) "nodes are hard to
make value out of" — a dot with a raw-JSON dump isn't insight; the connections-as-navigation panel turns a node into
a place you can walk from. (3) "it snaps to a zoomed-out version" — the hard one-shot fit *looked broken*, which on
a demo reads as low quality. Clean, professional, intuitive **is** the deliverable here.

**Analysis / consequences:** a few decisions worth recording. (1) **Kept the zero-dependency hand-rolled canvas**
(per `meet-joiner/AGENTS.md`'s "this is not the Next you know" warning) — every feature, including the eased camera
and color modes, is plain canvas + refs, no react-force-graph/Cytoscape, so no SSR/dep landmines. (2) The **eased
camera** (rendered `view` chases a `target`; pan/zoom write both to avoid fighting the ease; a frame-counted follow
re-fits only while settling and any interaction cancels it) is the load-bearing fix — it also makes spacing changes
and "fit" feel intentional rather than abrupt. (3) **Connectivity is now the visual signal**: connected nodes are
larger/brighter, isolated ones shrink and recede, hubs glow with persistent outlined labels. This is also an honest
mirror of the data's real shape — the seeded graph is overwhelmingly `code_module` with sparse edges, so the explorer
truthfully shows a hub-and-spoke of files around commits rather than faking density. (4) The **`/overview` seeding is
degree-based with a recency fallback**, so it degrades gracefully on a fresh/edgeless graph. The standing limitation,
unchanged by this entry: the UI can only render what's ingested — the biggest remaining unlock for "feels like a
knowledge graph" is richer source ingestion (people/meetings/tasks/decisions via `/ingest`+`/extract`), which the
panel and edge-labels are already built to display. Follow-ups: the FE changes (everything except the already-
committed `/overview` backend) are **uncommitted in the working tree** and need a commit; optional "center & expand"
(re-seed the subgraph around a clicked neighbor) and a recency color mode (needs `updated_at` plumbed onto the FE
node shape).

**Touches:** committed in `6711f78` — `central-kg-api/app/{graph.py, routers/subgraph.py}`,
`meet-joiner/src/app/api/graph/overview/route.ts`. Uncommitted (working tree) —
`meet-joiner/src/app/graph/{ForceGraph.tsx, NodePanel.tsx, page.tsx, types.ts, SettingsMenu.tsx (new)}`,
`docs/LOG.md`. (No infra changed.)

— Claude (Opus 4.8), signed off

---

## 2026-06-25 — Containerized the stack: one image, one-command local, real deploy path

**What:** turned "works on my four terminals" into a buildable, deployable stack. Added `agent-system/Dockerfile`
— **one image, four entrypoints** (runner / planner / gateway / sites), with **Node.js + `mcp-aiven` baked in**
because the git/data agents launch the Aiven MCP server over stdio. Added `central-kg-api/Dockerfile.server` (a
uvicorn variant beside the existing Lambda/Mangum `Dockerfile`). Rewrote `docker-compose.yml` so
`docker compose up --build` (= `make stack`) brings up **redpanda + a one-shot topics creator + runner + planner +
gateway + sites** with health-gated ordering and shared volumes for web-agent output; a `full` profile adds
`central-kg-api`. Added `.dockerignore`s (keep `.venv`/`.env` out of the context), `make stack*`/`build` targets,
and `.env.example` notes (compose overrides `KAFKA_BOOTSTRAP→redpanda:9092`; `DATABASE_URL` for the full profile).
Wrote **`docs/DEPLOY.md`** (the operator runbook) and reconciled OVERVIEW §5/§7 + the docs README to it.
**Verified for real:** `docker compose config` validates; the agent image **builds**; and inside it
`import agent_runner{,.planner,.gateway}` succeeds, `node --version` = v20, `mcp-aiven` is on PATH.

**Why:** the operator hit the wall directly — `make` isn't on Windows, and running each service by hand from the
wrong directory failed four ways ("is this all kafka? do we run these every time? is there auto?"). The honest
answers: Kafka is only the *internal* bus (edges are HTTP/WS/MCP); locally these are long-lived services so yes you
start them each session — **unless** they're containerized, which is also exactly what makes them deployable. So one
change ("one image, run four ways" + compose) answers both the "is there auto" pain *and* the "how do we deploy"
question. The planner I built last pass cost **zero** new infra here — it's just the fourth command on the same
image, which is the payoff of the one-image design.

**Analysis / consequences:** a few decisions worth recording. (1) **One image, not four** — identical deps, one
build/push, processes differ only by `command`; this is why adding the planner was free and why the deploy table
collapsed from "two processes" to "N processes, one artifact". (2) **Node in the image is non-negotiable** — the
34% MCP path runs `mcp-aiven` via stdio, so the runtime needs npm; pre-installing it globally avoids a per-task npx
download. (3) Kept the KG API's **Lambda Dockerfile** and added a *server* one rather than replacing it — both
deploy targets stay open (DESIGN keeps Lambda as the default; a VM/container host now works too). (4) `central-kg-api`
is **opt-in** in compose (`--profile full`) because it needs live PG creds and has an independent deploy path —
default `up` stays the self-contained agent bus. Two things flagged in DEPLOY as demo-day traps: the gateway WS and
the KG API both need **public `wss://`/HTTPS + TLS** (Caddy in front on a VM), and secrets stay in the host env
(the `.dockerignore` enforces it). Not done here (deliberately, unchanged scope): pushing images to a registry,
the avatar's delegation seam, and the runner at-least-once commit. Local→Aiven Kafka remains a pure env flip.

**Touches:** `agent-system/{Dockerfile (new), docker-compose.yml, .dockerignore (new), Makefile, .env.example}`,
`central-kg-api/{Dockerfile.server (new)}`, `docs/{DEPLOY.md (new), OVERVIEW.md, README.md, LOG.md}`. (No infra
deployed; no app code changed.)

— Claude (Opus 4.8), signed off

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

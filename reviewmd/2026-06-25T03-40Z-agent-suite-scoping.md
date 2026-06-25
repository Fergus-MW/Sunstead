# Sunstead — Agent Suite: Review, Testing & Scoping

**Timestamp:** 2026-06-25T03-40Z
**Branch:** `main` @ `68769cf` (working tree dirty)
**Scope:** a focused pass on **the agent suite only** — how it works, what's wired, what's broken, how to test agents in isolation, and a scoped roadmap for the agents we actually want.
**Companion to:** [`2026-06-25T03-23Z-snapshot.md`](2026-06-25T03-23Z-snapshot.md) (full-system deep pass). That doc still holds for KG/FE/avatar/deploy; this one drills into the worker suite and turns the "agents need to work well" goal into a plan.
**Changes made this pass (code):**
- Testing: fixed `scripts/ask.py` (trace bug), added `scripts/try_agent.py` + `make try` (isolated per-agent runner). Verified: echo agent runs in isolation.
- **data-agent — now real** (`data.py`): NL question → structured LLM turn (SQL + chart spec) → `aiven_pg_read` → matplotlib PNG (product palette) → image artifact + streamed insight. Verified live on the KG (commits-per-author barh, real data). Added `pandas`/`matplotlib` deps.
- **`update_website` — now real** (`web.py`): sites keyed by a stable `workspace_id` (falls back to latest site); `update_website` reopens the HTML, patches it, rewrites the same path → **URL never changes**; revision history in `meta.json`. Verified build→update keeps the URL and applies the edit.
- **FE**: dashboard now renders `image` artifacts inline (`<img>`), so data-agent charts show on the card. `tsc` clean.
- **meeting-ops — new agent** (`meeting.py`, intents `recap`/`action_items`/`decisions`): one structured LLM turn extracts recap + owned action items + decisions-with-rationale from the transcript, then **writes `action_item`/`decision` nodes back to the KG via `aiven_pg_write`** (best-effort; the graph grows). Wired: new `agent.tasks.ops` topic, registry, and the **planner now keeps a rolling per-meeting transcript** and injects it into ops tasks (the agent stays stateless). Verified live: full write→read→delete round-trip on the KG.

- **research agent — new agent** (`research.py`, intent `research`): answers from the **live web** via Claude's server-side `web_search_20260209` + `web_fetch_20260209` tools (no beta header; Opus 4.8 supports them; dynamic filtering built in). Streams reasoning/answer to the dashboard, handles `pause_turn` continuations, surfaces cited URLs as artifacts. New `agent.tasks.research` topic + planner routing (git = our codebase/graph, research = the outside world). **Verified live**: a real search produced an accurate, cited answer about Aiven.

> **Note — parallel work landed** in `shared/contracts.py`/`config.py`: a `Verdict` (grounding verifier → `TaskResultPayload.verdict`, the FE renders it) and a `ControlPayload` + `agent.control` topic (cancel channel). This is the §2 "no verify gate" finding being built by someone else — meeting-ops/data-agent/research stay compatible (verdict is optional).

> **Discovered:** `aiven_pg_read` requires args `query`, `service_name`, `project`, `database`, **`reasoning`** (not `service`); returns JSON (`meta.fields` + `rows`, values as strings) inside an `<untrusted-aiven-response-…>` guard. The live KG is the Anthropic SDK repo parsed to a graph: code_module 5236, decision 841, commit 500, person 26.

---

## 0. TL;DR

The suite's **plumbing is real and the entrypoint you want is already the default**: avatar → `POST /transcript` → `meeting.transcript` → **planner** → `agent.tasks.*` → **runner** → harness → agent → `agent.results/activity/trace` → gateway WS → FE. One image, three long-lived processes (runner/planner/gateway) + a static sites server.

But "works as plumbing" ≠ "works as an employee." Three things gate that:

1. 🔴 **data-agent is a stub the planner will route to.** "Show me the numbers" returns `{"status":"stub"}` on camera.
2. 🔴 **`update_website` doesn't update** — it regenerates a fresh site each time, ignoring the persistent workspace.
3. 🟡 **The suite does two things well (KG Q&A, web build) and nothing else.** To be more than transcription it needs deliverable-producing agents (meeting-ops) and a graph that grows live (kg-writer).

**Decided scope this pass** (your call): core = ① make data-agent real, ② make update_website real, ③ meeting-ops, ④ kg-writer. Outbound integrations (Slack/GitHub/email) + a Slackbot entrypoint = **extension**, after the core works. Deploy = AWS container (recommendation in §6: **single EC2 + `docker compose` + Caddy**, not ECS).

---

## 1. How the suite works (the parts that matter)

- **Harness** ([`shared/harness.py`](../agent-system/shared/src/shared/harness.py)) — every task runs one lifecycle: dedupe → `activity("received")` → `run()` → lift `result["artifacts"]` → emit `task.completed/failed` + `activity`. Specialists implement **only** `async def run(task, ctx) -> dict`. Adding an agent is genuinely cheap.
- **Registry** ([`registry.py`](../agent-system/agent-runner/src/agent_runner/registry.py)) — flat `intent -> fn`. New agent = module + one dict line + add intent to `TaskIntent` Literal ([`contracts.py`](../agent-system/shared/src/shared/contracts.py)) + `TASK_TOPIC_BY_INTENT` ([`config.py`](../agent-system/shared/src/shared/config.py)) + a line in the planner prompt.
- **Data plane is all Aiven MCP** ([`mcp.py`](../agent-system/shared/src/shared/mcp.py)) — one warm `npx mcp-aiven` stdio session, reused. Programmatic (`pg_read`) or LLM-exposed (`llm_tools()`).
- **Two read paths to the same Postgres** (intentional, but overlap): the **avatar** answers quick lookups *in-call, spoken* via `central-kg-api` HTTP ([avatar `tools.py`](../avatar-agent/src/avatar_agent/tools.py)); the **suite's git/KG agent** answers *async, on the dashboard* via MCP. Keep the split deliberate: avatar = fast/synchronous/spoken; suite = slow/async/artifact-producing.

**Current agents:** `echo` (smoke ✅), `git`/KG ([`git.py`](../agent-system/agent-runner/src/agent_runner/agents/git.py)) — real LLM tool-runner over `aiven_pg_read`, also serves the general `ask` intent ✅, `web` ([`web.py`](../agent-system/agent-runner/src/agent_runner/agents/web.py)) — real, streams the build, publishes a URL ✅, `data` ([`data.py`](../agent-system/agent-runner/src/agent_runner/agents/data.py)) — **stub ❌**.

---

## 2. Critical evaluation — ranked

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **data-agent is a stub** the planner routes `analyze/summarize_metrics/query_data` to | 🔴 | `data.py` returns `{"status":"stub"}` |
| 2 | **`update_website` regenerates, never patches** — `workspace_id` ignored, writes to `.sites/<task_id>/` (new URL each time) | 🔴 | `web.py:56` |
| 3 | **Runner is at-most-once** — `enable_auto_commit=True` + fire-and-forget `create_task` commits offsets before the task finishes; a crash drops in-flight work (undercuts "autonomous operator") | 🟠 | `kafka.py:66`, `runner.py:72` |
| 4 | **No `verify` gate** — harness promises validate→…→verify→emit but nothing gates output before it's emitted/spoken; "reviewer" unbuilt | 🟡 | harness lifecycle |
| 5 | **No agent→agent subtasks** — `parent_task_id`/`depth` exist, unused; the web→data chart example is vapor | 🟡 | — |
| 6 | In-memory unbounded dedupe (`ctx._seen`, planner `seen`); not durable across restart | 🟢 | harness/planner |
| 7 | `git` uses Haiku tool-runner (no true thinking stream); `web` uses Opus streaming — inconsistent trace fidelity | 🟢 | `git.py` comment |
| 8 | Dead `agent-system/agents/` dir (only stale `__pycache__`) — delete | 🟢 | — |
| 9 | Planner enum allows `ask` but the prompt never describes it — model could pick an undocumented intent | 🟢 | `planner.py` |

**Solid and worth protecting:** the harness abstraction, warm MCP/Kafka/Anthropic clients, planner-as-single-brain, the gateway's shared-consumer + replay-ring, and the web-agent.

---

## 3. Testing the agents in isolation — the ladder (now unblocked)

You've never run the agents. There are **four rungs**, cheapest first:

| Rung | Command | What it proves | Creds |
|---|---|---|---|
| **1. Agent only, no Kafka/Docker** | `make try I=echo A='{"text":"hi"}'` (any intent) · or `make ask Q="..."` (KG only) | the specialist `run()` itself, with streamed trace printed inline | per-agent |
| **2. One task through the runner** | `make up && make topics && make run`, then `make echo` / `publish_task.py --intent ...` | resolve → harness → agent → emit, per intent | per-agent |
| **3. From your entrypoint (transcript→planner)** | `make planner` + `make mock`, then type an utterance | the whole call-stack **minus the avatar** | ANTHROPIC (+AIVEN) |
| **4. HTTP edge** | `make gateway` + `curl POST /tasks`/`/transcript`, watch WS `/stream` | the avatar's real seam | as above |

**Fixed/added this pass:**
- `scripts/ask.py` — was **broken** (the new `ctx.trace()` in `git.py` `AttributeError`'d against `CliCtx`); added a no-op `trace()`. Works again.
- `scripts/try_agent.py` (+ `make try I=<intent> A='<json>'`) — the **generic** rung-1 runner `ask.py` lacked: resolves *any* registered intent, calls `run()` directly with a real warm context (MCP+Anthropic when present) but a Kafka-free ctx that prints `activity` + streamed `trace`. **Verified** on the echo path.

**Still missing (acceptable for the timeline, noted):** no pytest / assertions anywhere — every script is eyeball-only. Rung 1 is the right place to add a couple of smoke asserts later.

**Recommended first run for you:** rung 1 echo (done), then rung 1 `make ask Q="who last touched X"` to confirm the live MCP→KG path, then rung 3 `make mock` to see the planner delegate.

---

## 4. Agent scoping — the roadmap (decided core)

Principle reaffirmed: **avatar = fast/synchronous/spoken; suite = slow/async/artifact-producing.** Don't duplicate the KG read path.

### Tier 0 — fix what's advertised but fake
**① data-agent (make real)** — intents `analyze/summarize_metrics/query_data`.
- Path: same MCP loop as `git.py` (`aiven_pg_read` → rows) → `pandas` → `matplotlib` PNG → save under the sites/sessions dir → return `{"summary": ..., "artifacts": [{"kind":"image","value": url}]}`.
- Reuse: the KG tool-runner is already written; the *new* work is rendering + serving the PNG (the sites server already serves a dir — point it at artifacts too, or write the PNG into `.sites/<task_id>/`).
- Effort: ~½ day. **Highest ROI** — removes the on-camera stub.

**② update_website (make real)** — intents `build_website/update_website`.
- Key by `workspace_id` (fall back to task_id): write to `.sites/<workspace_id>/` for a **stable URL**; persist prior HTML + brief in the SessionStore workspace; on `update_website`, load prior → ask the model to patch → rewrite same path. `SessionStore.workspace()` already exists.
- Effort: ~½ day.

### Tier 1 — the "employee" beats (new agents)
**③ meeting-ops** — new module `agents/meeting.py`; intents `recap`, `action_items`, `decisions` (add to `TaskIntent` + topic map + registry + planner prompt; route to a new `agent.tasks.ops` or reuse `.git`).
- Input = the transcript. Cleanest + on-rubric: **read utterance/meeting nodes from the KG via MCP** (they exist in the schema) rather than threading text through args.
- Output = structured list (text/json artifact for the dashboard) **+ write `decision`/`action_item` nodes back via `kg.updates`** → the graph captures the meeting's outcomes. This is the strongest "AI employee" demo beat.
- Effort: ~1 day.

**④ kg-writer (background)** — a 4th long-lived process (like the planner) **or** folded into the planner loop: tail `meeting.transcript`, extract people/topics/decisions via one LLM call, emit `kg.updates`. Makes `/graph` **grow live** mid-meeting — a great visible beat.
- ⚠️ **Dependency:** needs `central-kg-api` to have a **`kg.updates` consumer** that writes nodes/edges. AGENT_SYSTEM.md §12 lists this as the teammate's unbuilt item — **confirm it exists before building kg-writer**, else the writes go nowhere.
- Effort: ~1 day (excluding the consumer dependency).

### Tier 2 — extensions (after core works)
- **Outbound integrations** as agents: Slack post of the recap, GitHub issue from a decision, calendar event from an action item. Each = 1 module + 1 token. (The Slackbot-as-*inbound*-entrypoint you mentioned is the twin — it'd `POST /tasks` like the avatar does; treat as backup entrypoint, not core.)
- **reviewer** (the missing `verify` gate) — a cheap Haiku pass that gates an agent's output before emit/speak.

---

## 5. Where each capability lives (main agent vs suite)

| Capability | Lives in | Why |
|---|---|---|
| Quick KG lookup ("who owns auth") | avatar (in-call, spoken) | sub-second, conversational |
| Record action item (single fact) | avatar (already built) | one write, immediate ack |
| Deep KG question / `ask` | suite git/KG agent | heavier traversal, dashboard-rendered |
| Build/update website | suite web-agent | minutes-long, artifact |
| Data analysis + chart | suite data-agent | code execution, artifact |
| Recap / action-items / decisions | suite meeting-ops | structured deliverable + KG write |
| **Web research (outside world)** | **suite research agent** | live web_search/web_fetch, cited, async |
| Live graph enrichment | suite kg-writer (background) | continuous, no user turn |
| Slack/GitHub/email actions | suite integration agents | side-effecting, async |

The two read-paths now have a clean split: **git/KG agent = our own graph** (codebase, meetings, decisions via MCP), **research agent = the open web** (current facts, prices, news via server tools).

---

## 6. Deploy recommendation (you were unsure — here's the call)

**Recommendation: a single EC2 box running the existing `docker compose` stack, with Caddy in front for automatic TLS.** Reasons:
- **Reuses what exists.** The compose already brings up runner+planner+gateway+sites health-gated; no new IaC. ECS/Fargate (the alternative AWS path) adds ALB + ACM + VPC + task defs + ECR wiring for zero demo benefit — DEPLOY.md §6 explicitly recommends against it for a short demo.
- **TLS is the real requirement, not orchestration.** The browser opens the gateway WS **directly**, and artifact URLs must be HTTPS, so you need public `wss://`/`https://`. Caddy gives that with a one-line `reverse_proxy` per service and auto-renewing certs — far less work than ALB+ACM.
- **Cross-cloud is fine.** Aiven (PG/Kafka/OpenSearch) already lives on DigitalOcean; the suite reaches it by env (`KAFKA_*`, `DATABASE_URL`). EC2→Aiven is just egress.
- **Keep the avatar local.** Its 4-tier Terraform is the heaviest, least-tested piece (never applied). Narrate the avatar from a laptop over the cloud loop.

Concrete shape: `t3.large` (web/matplotlib builds want headroom) → install Docker + compose → clone → `.env` with secrets → `docker compose up -d` → Caddy mapping `gateway.<host>`→8800, `sites.<host>`→8810 (and the FE on Vercel pointing `NEXT_PUBLIC_GATEWAY_WS_URL` at `wss://gateway.<host>`). A few hours, no new code. If you'd rather not run a box at all, Fly/Railway gives the same result with managed TLS — but since you're leaning AWS, EC2+compose+Caddy is the lowest-friction AWS answer.

---

## 6b. Entrypoints & the missing "concierge" (chat terminal question)

**Does the suite have an orchestrator? No.** The planner is a *stateless router* (one utterance → tasks); it doesn't hold a conversation or answer anything itself. The only conversational brain today is the **avatar**, and it's bonded to LiveKit (STT/TTS + `RunContext`). So a "chat terminal that's basically the same agent without STT/TTS" doesn't exist yet — and decoupling the avatar from LiveKit cleanly is non-trivial (tools take `RunContext[AgentRuntime]`, session lifecycle is LiveKit's).

**Recommendation: build a new `concierge` entrypoint in the suite rather than reuse the LiveKit agent.** It's a text-in/out conversational loop that shares the avatar's *philosophy* (and ideally a shared system-prompt string) but uses the suite's own primitives:
- answers quick things itself — KG via MCP, **web via the same server tools the research agent uses**;
- **delegates** heavy work via the gateway `POST /tasks` (exactly like the avatar's `delegate()`), fire-and-forget;
- **subscribes to the gateway WS** to surface async results inline as they land.

This one component is four things at once: (a) the **chat terminal** you want, (b) a **test harness** for the full delegation+async flow, (c) the suite's first real **orchestrator**, and (d) a **non-STT entrypoint** / backup to the avatar. A Slack bot is the same thing with a Slack transport in front — Tier-2 extension, not core. Keep the avatar as the realtime voice frontend; longer term, both the avatar and the concierge can be thin frontends over one shared brain, but don't do that refactor now.

Effort: ~half-day for a CLI v1 (conversation loop + delegate tool + WS result printer). The `mock_meeting.py` script is the *transcript-pipe* precursor; the concierge is the *conversational* version.

---

## 7. Prioritized actions

| # | Action | Why | Effort |
|---|---|---|---|
| 1 | ✅ **Fix `ask.py` + add `try_agent.py`** (done this pass) | unblock isolated per-agent testing | done |
| 2 | **You run rung-1/rung-3 tests** (`make ask`, `make mock`) | first-ever confirmation the agents work live | minutes |
| 3 | ✅ **Make data-agent real** (done this pass) | kill the on-camera stub | done |
| 4 | ✅ **Make update_website real** (done this pass) | the second fake capability | done |
| 5 | ✅ **meeting-ops agent** (done this pass) | the headline "employee" deliverable | done |
| 6 | ✅ **research agent (live web search)** (done this pass) | the "look it up online" capability you asked for | done |
| 7 | **concierge entrypoint** (§6b) — text chat that answers + delegates + streams results | chat terminal + flow test + orchestrator, in one | ~½ day |
| 8 | **kg-writer** — ⚠️ `kg.updates` has **no consumer** (confirmed); write live extraction via `aiven_pg_write` like meeting-ops | live graph growth | ~1 day |
| 9 | Deploy: EC2 + compose + Caddy (§6) | public clickable loop | hours |
| 10 | Flip runner to at-least-once (commit after `run()`); delete dead `agents/` dir; add `ask` to planner prompt | hardening | low |

**Critical path to "more than transcription":** data-agent → update_website → meeting-ops → research (all ✅), then the **concierge** (#7) so you can drive and *see* the whole swarm conversationally.

— Reviewed by Claude (Opus 4.8), focused agent-suite pass

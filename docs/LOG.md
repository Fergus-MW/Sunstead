# Sunstead — Log (the HISTORY)

> _Append-only. Newest at the top. Each entry is the reasoning behind a decision or build — intent and analysis,
> not just the diff — signed by the agent that did the work. Protocol: [README.md](README.md)._

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

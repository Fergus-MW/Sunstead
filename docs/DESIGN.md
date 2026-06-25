# Sunstead — Design (the FUTURE)

> _The target architecture and the plan to get there. The single forward source-of-truth: when this disagrees with
> [PLAN.md](PLAN.md) / [AGENT_SYSTEM.md](AGENT_SYSTEM.md), **this wins** and those get folded in. For current
> reality see [OVERVIEW.md](OVERVIEW.md); for the reasoning trail see [LOG.md](LOG.md)._

---

## 1. Vision

A low-latency, knowledge-graph-grounded **AI employee that sits in your video calls** — a photorealistic avatar
that converses in real time and delegates real work (deploy a site, answer from the code graph, analyze data) to a
swarm of specialist agents that talk to data **natively via Aiven MCP**. One sentence exercises the whole system:
*"build us a landing page and tell me who last touched the auth module."*

## 2. The reconciled architecture — two layers

The system fragmented into three independently-built pillars that didn't connect (see [LOG.md](LOG.md) 2026-06-25
entries). The resolution is **not to force everything onto one mechanism**, but to split by tempo — each layer
speaks its native, latency-appropriate idiom, joined by one explicit seam.

```
  REALTIME LAYER (human-facing, <1s)                 ASYNC LAYER (heavy work, seconds–minutes)
 ┌──────────────────────────────┐                   ┌───────────────────────────────────────────┐
 │ avatar-agent (Ferg)          │   delegate(...)   │ agent-runner container (ours)              │
 │ Recall + LiveKit + Anam      │ ───── seam ─────▶ │ Kafka consumer → harness → specialists     │
 │ STT→LLM(Sonnet)→TTS          │   agent.tasks.*   │ web · git · data  (Aiven MCP for all data) │
 │ fast KG reads via HTTP ──────┼───┐               │ emits agent.results / agent.activity ──────┼──┐
 └──────────────────────────────┘   │ HTTP          └───────────────────────────────────────────┘  │
            ▲ speaks / shows         ▼                              │ aiven_pg_read/write              │ Kafka
            │                  ┌──────────────┐                     ▼                                  ▼
   FRONTEND (Vercel) ◀── WS ── │  gateway     │ ◀── Kafka tail ── AIVEN: Postgres+pgvector (KG) · Kafka · OpenSearch
   transcript + activity +     │ POST /tasks  │
   the deliverable             │ WS /stream   │     central-kg-api (FastAPI): seed + FE/avatar read bridge
                               └──────────────┘
```

**Realtime layer = the avatar.** Recall.ai joins the Meet and streams the Anam avatar as the bot's camera; LiveKit
runs the STT→LLM→TTS loop (Soniox `stt-rt-v5` default / Deepgram fallback · Anthropic Sonnet · Cartesia). Its tools read the KG over **HTTP to
`central-kg-api`** — and that's *correct here*: a <0.3 s in-conversation lookup can't afford an MCP detour. **The
avatar is a human-facing realtime client, like the FE — not one of the MCP worker agents.**

**Async layer = the worker suite.** One long-lived container: a Kafka consumer feeds a shared harness
(validate→dedupe→act→emit) that routes to specialists. **Every data operation here goes through Aiven MCP**
(`aiven_pg_read`/`aiven_pg_write`, provisioning) — this is the 34% showcase. Workers emit results/activity on Kafka.

### The MCP two-layer framing (resolves the contradiction)

PLAN §3.5 said "agents talk to data via MCP; the FastAPI is FE-only; agents must not call the HTTP API." Taken
absolutely, the avatar violates it. The framing that makes code and doctrine agree:

> **MCP-native is the property of the async worker suite, not of every process that touches data.** The realtime
> avatar is a human-facing client and uses HTTP for its latency-bound reads; the *34% depth* lives in the worker
> suite (KG read/write + provisioning over MCP) and in the Kafka swarm. Both are true at once.

This is honest for the rubric: we *show* deep MCP usage where it counts, and we don't pretend the avatar is
something it isn't.

## 3. The seams (contracts that must stay stable)

**Kafka topics + the `sunstead.v1` envelope** — the bus between async components. Topics: `agent.tasks.{web,data,git}`
(dispatch), `agent.results` (outputs), `agent.activity` (visible status feed, *not* chain-of-thought), `kg.updates`
(async facts), `meeting.transcript` (from the avatar, optional). Payloads are pydantic models in
`agent-system/shared/contracts.py` — **change = PR + a LOG entry.** (Detail: [AGENT_SYSTEM.md](AGENT_SYSTEM.md) §6.)

**The avatar → worker delegation seam** (the one wire that turns three pillars into one system) — **shipped, option (a)**.
The avatar's `delegate(intent, brief)` tool `POST`s to the gateway's `/tasks`, which produces the `agent.tasks.*`
message (`avatar-agent/src/avatar_agent/{gateway.py,tools.py}`; see [AVATAR_DELEGATION.md](AVATAR_DELEGATION.md)).
Chosen over (b) direct-Kafka because it reuses the avatar's native HTTP-tool idiom (no Kafka creds/deps in the
realtime container) and the gateway is the single authenticated task front door (the FE "ask box" uses it too).
Worker↔worker and result-broadcast stay on Kafka — option (a) only shapes the single external ingress edge.

**Result return.** Heavy work is async, so the avatar's tool is **fire-and-forget** ("on it — I'll put it on
screen"). The deliverable surfaces on the **FE** (gateway → WS). Spoken results are a *later, separate* notify edge
(gateway → LiveKit session) — don't make the avatar a Kafka consumer for it.

**KG access.** Workers: Aiven MCP (`aiven_pg_read`/`_write`) against the live Postgres. Avatar + FE:
`central-kg-api` HTTP (`/query`, `/entity`, `/timeline`, `/update`). `central-kg-api` is **seed + read-bridge**, not
on the worker data path. (Detail: [CENTRAL-KG-API.md](CENTRAL-KG-API.md).)

## 4. Deployment topology

Multi-host by design; each pillar deploys in its native idiom. Acceptable for the hackathon as long as the demo
runs (locally is fine).

| Component | Host | Notes |
|---|---|---|
| agent-runner | one container — ECS Fargate / EC2 | long-lived consumer + warm `mcp-aiven` + durable session volume |
| avatar-agent | LiveKit on EC2 + dispatch Lambda + viewer on S3/CloudFront, **or** all on one EC2 ([SINGLE_EC2.md](../avatar-agent/deploy/SINGLE_EC2.md)) | on `main`; Terraform in `avatar-agent/deploy/` |
| central-kg-api | Lambda (Mangum) | seed + FE/avatar read bridge |
| frontend / viewer | Vercel / S3 | |
| Aiven (PG+pgvector, **Kafka**, OpenSearch) | Aiven cloud | Kafka **provisioned** via MCP (`kafka-254bd14f`, 9 topics); remaining: point runner/gateway at it (bootstrap + SASL) |

## 5. Roadmap — what's done, what's left

The remaining risk is **integration, not capability.** Most of the original "three wires and a merge" is done.

**Done (this build):** avatar merged to `main`; Aiven Kafka provisioned via MCP (`kafka-254bd14f`, 9 topics);
web-agent real (site → served URL); delegation seam wired *twice* — the avatar's `delegate()` (§3, option a) **and**
the `planner` (tails `meeting.transcript` → `agent.tasks.*`); gateway built (`POST /tasks` + `WS /stream`, with a
replay ring buffer); Soniox STT + Anam avatar wired; FE `/api/join` dispatches + the dashboard scopes by `meeting_id`;
a `mock_meeting` harness drives the whole pipe locally with no Recall/LiveKit.

**Left:**
1. **Point runner/gateway at Aiven Kafka** (bootstrap + SASL creds) — flips local→cloud by env only.
2. **Land `demo-data`** (the remaining unmerged, additive branch).
3. **Decide the single delegation brain** (see §6) — planner vs avatar `delegate()` — so they don't both fire.
4. **Record the happy-path run** for the written submission: `aiven_pg_read` over `mcp-aiven` (the 34% proof) +
   the ask-box → Kafka → agent → live-result loop.

Both trajectories — **win the live demo** and **win the written Anthropic submission** — are served by #1–#5.

## 6. Decisions

- **One delegation brain — DECIDED & shipped: the planner.** The avatar emits each final user utterance to the
  gateway's `/transcript` → `meeting.transcript`, and the **planner** does all routing — so the *same* path serves
  the `mock_meeting` harness and the real avatar. `delegate()` is no longer an always-on avatar tool; it's opt-in via
  `AVATAR_DELEGATES=true` (which then expects the planner to be run OFF, or both delegate the same utterance).
  Implemented in `avatar-agent` (`agent.py` transcript hook + tool gating, `gateway.py:publish_transcript`,
  `config.py:avatar_delegates`) and the gateway (`POST /transcript`). §3.
- **Spoken vs FE-only results** — FE-only for the demo; spoken is a later notify edge. §3.
- **Doc consolidation** — fold PLAN/AGENT_SYSTEM/HACKINFO detail into DESIGN/OVERVIEW as we go; keep them as deep
  references meanwhile.

---

_Deep references: [PLAN.md](PLAN.md) (whole-system, incl. the MCP-native §3.5), [AGENT_SYSTEM.md](AGENT_SYSTEM.md)
(the agent suite, Kafka contracts, latency rules), [HACKINFO.md](HACKINFO.md) (rubric + submission),
[CENTRAL-KG-API.md](CENTRAL-KG-API.md) (the KG service)._

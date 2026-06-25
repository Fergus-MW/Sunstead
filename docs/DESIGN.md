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
runs the STT→LLM→TTS loop (Deepgram / Anthropic Sonnet / Cartesia). Its tools read the KG over **HTTP to
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

**The avatar → worker delegation seam** (the one wire that turns three pillars into one system). The avatar adds a
`delegate(intent, args)` tool. Two bridge options (see [LOG.md](LOG.md) 2026-06-25):
- **(a) HTTP edge → gateway → Kafka** *(recommended default)* — the tool `POST`s to the gateway's `/tasks`, which
  produces the `agent.tasks.*` message. Reuses the avatar's native HTTP-tool idiom (zero new deps for Ferg); the
  gateway is the single authenticated task front door (FE "ask box" uses it too). Kafka stays internal to our side.
- **(b) Direct Kafka** — the avatar gains an `aiokafka` producer and writes `agent.tasks.*` itself. Keeps "pure
  Kafka," but pushes Kafka creds + deps into a finished realtime container.
- **Decision:** (a) unless Ferg prefers to own a Kafka producer. *Pending his sign-off.* Either way, worker↔worker
  and result-broadcast stay on Kafka — the choice only affects the single external ingress edge.

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
| avatar-agent | LiveKit on EC2 + dispatch Lambda + viewer on S3/CloudFront | Ferg's Terraform |
| central-kg-api | Lambda (Mangum) | seed + FE/avatar read bridge |
| frontend / viewer | Vercel / S3 | |
| Aiven (PG+pgvector, **Kafka**, OpenSearch) | Aiven cloud | **Kafka still to be provisioned — do it via MCP, on camera** |

## 5. Roadmap — "three wires and a merge" (rubric-ordered)

The remaining risk is **integration, not capability.** Each step below is hours, not days, and maps to the score.

1. **Merge the unmerged branches** → `main` (avatar, demo-data). Get the whole system on the integration line.
   *Owner's PR for teammate branches; we don't merge those unilaterally.*
2. **Provision Aiven Kafka via MCP, on camera** → `kafka_admin` creates topics. *Kills the infra gap; banks 33%
   autonomy.*
3. **Make web-agent real** — a tiny static site → Vercel from a mid-call brief. *33% creativity/impact; the visual
   wow; and the one genuine delegation target (the avatar can already answer reads itself).*
4. **Wire the delegation seam** (§3, option a) — avatar `delegate` → `agent.tasks.web` → web-agent → Vercel URL →
   shown on the FE. *This is the single highest-leverage wire: it turns three pillars into one system.*
5. **Build the gateway** (`POST /tasks` + `WS /stream`) — needed for both #4 and the FE result display.
6. **git-agent on camera** — record it issuing `aiven_pg_read` through `mcp-aiven` on the live graph. *No new code;
   it's already the 34% proof.*
7. **Reconcile docs to reality** — this doc system (OVERVIEW/DESIGN/LOG) is step zero; fold PLAN/AGENT_SYSTEM detail
   in over time.

Two trajectories share these steps: **win the live demo** (1→5) and **win the written Anthropic submission** (a
diagram that matches the code + logs of real `aiven_pg_read` calls + the MCP-depth narrative). Both are served by
closing the seams.

## 6. Open decisions

- **Delegation seam (a) vs (b)** — pending Ferg; default (a). §3.
- **Spoken vs FE-only results** — FE-only for the demo; spoken is a later notify edge. §3.
- **Kafka provisioning timing** — do it early and on camera (autonomy evidence). §5.2.
- **Doc consolidation** — fold PLAN/AGENT_SYSTEM/HACKINFO detail into DESIGN/OVERVIEW as we go; keep them as deep
  references meanwhile. §5.7.

---

_Deep references: [PLAN.md](PLAN.md) (whole-system, incl. the MCP-native §3.5), [AGENT_SYSTEM.md](AGENT_SYSTEM.md)
(the agent suite, Kafka contracts, latency rules), [HACKINFO.md](HACKINFO.md) (rubric + submission),
[CENTRAL-KG-API.md](CENTRAL-KG-API.md) (the KG service)._

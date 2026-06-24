# Agent System — focused plan (our part)

> Scope-down of [PLAN.md](PLAN.md) to **the part our team owns: the agents and the live-call pipeline.**
> Self-contained in one umbrella folder (`agent-system/`) on branch `feat/agent-system`, integrated with the
> rest of the system (knowledge graph, frontend) **only through stable seams** (Kafka topics + the KG HTTP API).
> We pipe/merge it together with the other parts later.

`PLAN.md` is the whole-system source of truth and is still evolving. **This doc is authoritative for our subsystem.**
When `PLAN.md` changes in ways that touch our seams, we update this doc first, then let it trickle into code.

---

## 1. What we own vs. what we integrate with

```
        ┌─────────── OURS (agent-system/) ───────────────────────────┐
        │                                                            │
 call ─▶│ call-gateway → [Kafka] → listener-agent → [Kafka] → agents │
        │                   ▲           │  ▲                  │       │
        │                   │           │  │                  │       │
        └───────────────────┼───────────┼──┼──────────────────┼───────┘
                            │           │  │                  │
            ┌───────────────┘           │  └──────────┐       │
            │ FE reads our events        │ KG queries  │       │ KG queries
            ▼ (via gateway WS)           ▼             ▼       ▼
       ┌─────────┐                  ┌──────────────────────────────┐
       │ FRONTEND│                  │  central-kg-api  (teammate)   │
       │ (FE team)│                 │  graph + vector + search      │
       └─────────┘                  └──────────────────────────────┘
```

| | Owned here | Integration | Contract |
|---|---|---|---|
| **Call pipeline** | ✅ call-gateway | Recall.ai, Soniox (external) | — |
| **Listener (Agent A)** | ✅ listener-agent | — | — |
| **Worker agents** | ✅ web / data / git | Vercel, Anthropic (external) | — |
| **FE event bridge** | ✅ gateway (thin) | Frontend team builds UI on top | REST + WS schema (§4) |
| **Knowledge graph** | ❌ | `central-kg-api` (teammate) | **KG HTTP client (§5)** |
| **Message bus** | shared | Aiven Kafka | **Topics + envelope (§4)** |

**Rule:** we never reach into the KG's database or the FE's code. We call the KG's HTTP API and we publish/consume Kafka.
Those two seams are the only things that must stay stable across teams.

---

## 2. Folder layout (`agent-system/`)

uv workspace; each component is its own package, `shared` is a path dependency.

```
agent-system/
├─ README.md                 # points here
├─ pyproject.toml            # uv workspace root
├─ .env.example              # all secrets/config keys (no values)
├─ shared/                   # the glue — imported by every component
│  └─ src/shared/
│     ├─ contracts.py        # pydantic envelope + message payloads (§4)
│     ├─ kafka.py            # producer/consumer helpers (Aiven mTLS/SASL)
│     ├─ kg_client.py        # HTTP client for central-kg-api (§5)
│     └─ config.py           # env loading, topic names
├─ call-gateway/             # Recall→Soniox→Kafka  (+ mock audio source, +TTS out)
├─ listener-agent/           # Agent A: two-tier parse → decide → delegate → speak
├─ agents/
│  ├─ web-agent/             # Agent B → Vercel
│  ├─ data-agent/            # Agent C → analysis
│  └─ git-agent/             # Agent D → repo/git (often answers from KG)
├─ gateway/                  # thin FE-facing REST + Kafka→WS bridge
└─ infra/
   ├─ kafka_admin.py         # create our topics idempotently (auto-create is OFF on Aiven)
   └─ topics.py              # single source of topic names + partition/RF config
```

---

## 3. Components — responsibilities & build notes

### 3.1 `shared/`
The contract layer. Everything else depends on it. Build this **first** so the seams are frozen early.
- `contracts.py` — the envelope + every payload as pydantic models (validation + JSON Schema export).
- `kafka.py` — thin wrappers over `aiokafka`/`confluent-kafka`; handles Aiven TLS (`ca.pem`) + SASL creds from env.
- `kg_client.py` — typed async client for `central-kg-api` (`query`, `semantic_search`, `quicksearch`, `upsert`).
- `config.py` — loads env, exposes topic names from `infra/topics.py`.

### 3.2 `call-gateway/`
Turns a meeting into a transcript stream on Kafka.
- **Dev mode (default):** read a local WAV / mic → chunk to S16LE 16k mono → Soniox WS → tokens → `meeting.transcript`.
  Lets us build the whole pipeline without burning Recall minutes.
- **Live mode:** Recall.ai bot joins Meet → `audio_separate_raw` per participant (base64 S16LE 16k mono) → decode →
  one Soniox stream per active participant → tokens (with Recall's speaker label) → `meeting.transcript`.
- **TTS out (later):** consume a `speak` instruction → TTS (ElevenLabs/Cartesia) → PCM → Recall output-audio into the call.
- Also emits `meeting.events` (join/leave/mute/lifecycle).

### 3.3 `listener-agent/` — Agent A ★ (the centerpiece)
Lightweight brain near the call; **the graph is the memory, not the Listener.** Two-tier loop:
- **Tier 1 (continuous, cheap):** Haiku/Sonnet structured classifier on every `transcript.final` →
  `{actionable: question|task|claim|reference|none}`. Most utterances are `none` → just update a small rolling window;
  periodically summarize the window into `kg.updates`.
- **Tier 2 (on trigger, Opus):** ground in KG (`kg_client`) → choose `answer | delegate | fact_check | clarify` →
  emit `task.create` to the right `agent.tasks.*`, or `speak`, or `write_fact`.
- **Tools:** `kg_query`, `kg_semantic_search`, `quicksearch`, `emit_task`, `speak`, `write_fact`.
- **MVP decision:** Listener Tier-2 *is* the orchestrator (no separate orchestrator). Revisit only for barge-in/reviewer.

### 3.4 `agents/` — worker suite
Common shape: Kafka consumer on `agent.tasks.<x>` → one Claude Agent SDK session per task → `agent.results`.
- **web-agent** (`build_website`, `update_website`) — codegen + `vercel_deploy`; may route LLM calls via Vercel AI Gateway. Returns a live URL.
- **data-agent** (`analyze`, `summarize_metrics`, `query_data`) — python/pandas execution; returns answer + chart artifact.
- **git-agent** (`read_git`, `blame`, `who_changed`, `recent_changes`) — often answers straight from the KG (commits/people/files are seeded), falls back to `git` via bash.

### 3.5 `gateway/` — FE bridge (thin)
So the frontend has one clean, auth'd connection and never touches Kafka.
- `POST` commands (start bot, ask, approve task) → produce to Kafka.
- `WS /stream?meeting_id=` → bridge `meeting.transcript` + `agent.results` + agent activity → browser.
- FE team builds the Meet-overlay UI on top of this. Schema in §4.

---

## 4. Seam A — Kafka topics & envelope (must stay stable)

Topics (created by `infra/kafka_admin.py`; start 3 partitions / RF 3 / min.insync 2):

| Topic | Key | Producer | Consumer |
|---|---|---|---|
| `meeting.transcript` | `meeting_id` | call-gateway | listener, gateway |
| `meeting.events` | `meeting_id` | call-gateway | listener, gateway |
| `agent.tasks.web` / `.data` / `.git` | `task_id` | listener | the matching agent |
| `agent.results` | `task_id` | agents | listener, gateway |
| `kg.updates` | `node_key` | listener, agents | central-kg-api (its consumer) |
| `agent.reasoning` *(stretch)* | `task_id` | agents | reviewer, gateway |

Envelope (every message):
```jsonc
{ "schema": "sunstead.v1", "id": "uuid", "type": "...", "meeting_id": "mtg_abc",
  "ts": "2026-06-25T10:00:00.123Z", "payload": { /* type-specific, see PLAN.md §6 */ } }
```
Payload shapes for `transcript.*`, `task.create`, `task.completed/failed`, etc. are defined in `shared/contracts.py`
(mirrors [PLAN.md §6](PLAN.md)). **Changing a payload = PR to `shared/contracts.py` + a note here.**

---

## 5. Seam B — Knowledge Graph HTTP client (must stay stable)

We consume the KG **only** through `central-kg-api`'s HTTP surface (so the AGE-not-on-Aiven graph internals stay the
KG team's concern). Expected endpoints our `kg_client.py` wraps:

| Method | Endpoint | Use |
|---|---|---|
| `POST /query` | graph/SQL traversal | call-graph, ownership, import closure |
| `POST /semantic_search` | `{text, k}` (pgvector) | "what relates to X in the codebase" |
| `POST /quicksearch` | `{text}` (OpenSearch) | fast full-text |
| `POST /upsert` | nodes/edges | (rarely; prefer async via `kg.updates`) |

If the KG team's endpoint shapes differ, we reconcile here and in `kg_client.py` — this table is the agreed interface.

---

## 6. Local dev story

- **No meeting needed:** call-gateway dev mode plays a recorded WAV → full pipeline runs end-to-end.
- **Kafka:** point at Aiven (shared dev cluster) using `ca.pem` + SASL creds in `.env`. (Optional: local redpanda/kafka in
  docker if we want to develop offline — same `shared/kafka.py`.)
- **KG:** point `KG_BASE_URL` at the teammate's running `central-kg-api` (local or deployed). Until it's up, `kg_client`
  has a `--stub` mode returning canned results so listener/agents can be built in parallel.
- **Run a component:** `uv run -m call_gateway`, `uv run -m listener_agent`, etc.
- Secrets via `.env` (never committed; never read secret *values*, only key presence).

---

## 7. Build order (our team)

1. **`shared/` contracts + kafka + config** — freeze the seams. *(blocks everyone, do first)*
2. **`infra/kafka_admin.py`** — topics exist on Aiven.
3. **`call-gateway` dev mode** — WAV → Soniox → `meeting.transcript`. *(first visible signal)*
4. **`gateway` WS bridge** — FE can see the live transcript. *(unblocks FE team)*
5. **`listener-agent` Tier-1 + Tier-2** — answers one grounded question. *(needs KG client; use stub until KG is up)*
6. **`agents/git-agent`** first (simplest, answers from KG), then **web-agent** (deploys a URL = great demo), then **data-agent**.
7. **call-gateway live mode (Recall)** — swap mock for the real bot. **TTS out** + **fact-check** as polish.

Each item is a short-lived branch off `feat/agent-system`, merged via PR; `shared/contracts` changes get extra eyes.

---

## 8. Conventions

- **Docs trickle down:** change this doc (or PLAN.md seams) → reflect in `shared/contracts.py` → consumers follow.
- **Contracts are code:** payloads are pydantic models; don't hand-roll dicts in components.
- **One folder, clean merge:** everything we build stays under `agent-system/`; we don't edit `central-kg-api/` or the
  FE folder — we integrate via Kafka + the KG client.
- **Models:** Opus for Listener Tier-2 / hard agent reasoning; Sonnet for codegen/mid; Haiku for Tier-1 + simple retrieval.

---

## 9. Open questions (track here, resolve as we go)

- Exact `central-kg-api` endpoint shapes — confirm with KG owner; update §5 + `kg_client.py`.
- One Soniox stream per participant vs. one mixed stream — start mixed for simplicity, move to per-participant for clean speaker labels.
- Soniox model id (`stt-rt-v5` vs `stt-rt-preview`) — verify at first integration.
- Does `gateway` belong to us or the FE team long-term? For now it's ours (thin) so we're demoable standalone.
- TTS provider choice + whether voice-out is in the demo or text-only.

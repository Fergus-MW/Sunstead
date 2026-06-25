# avatar-agent

> A face on the call that listens, speaks, and does the work.

A **live video AI assistant for Google Meet**: an AI participant that joins a
call as a photorealistic streaming avatar, converses in real time, and takes
actions on your behalf by calling a backend API through custom tools.

This is the implementation of the *Meeting Avatar Agent* design doc. It composes
three managed layers rather than driving a headless browser into the call:

| Layer | Tech | Responsibility |
|---|---|---|
| Meeting transport | **Recall.ai** | Joins the meeting; streams the avatar out as the bot's camera (`output_media`); pipes meeting audio to the page. |
| Orchestration | **LiveKit Agents** (Python) | Realtime STT → LLM → TTS pipeline, turn-taking, tool calls. |
| Avatar | **Anam** | Renders a real-time, lip-synced talking face into the LiveKit room. |
| Backend (tools) | **central-kg-api** | The knowledge-graph service the custom tools call over HTTPS. |

The central engineering constraint is **end-to-end latency** (< ~1 s perceived
response) — Anam's ~180 ms render leg is what protects the budget for the legs
that can't be compressed (network, LLM, tool calls).

## How it fits together

```
   ┌──────────────┐  audio + transcript   ┌─────────────────────────┐
   │ Google Meet  │ ────────────────────▶ │  LiveKit Agent (this)   │
   │ participants │ ◀──── avatar video ─── │  STT → LLM → TTS  +tools│
   └──────┬───────┘                        └────────┬────────────────┘
          │ joins + output_media(webpage)           │ HTTPS tool calls
   ┌──────┴───────┐      renders/captures   ┌────────┴───────┐  ┌──────────────┐
   │  Recall bot  │ ◀────────────────────▶  │ viewer page    │  │ central-kg-api│
   │  (transport) │      (LiveKit room)     │ (the "camera") │  │  (backend)    │
   └──────────────┘                         └───────┬────────┘  └──────────────┘
                                                     │ renders Anam avatar (out)
                                              ┌──────┴───────┐  + publishes meeting
                                              │  Anam avatar │    audio (in)
                                              └──────────────┘
```

The **viewer page** ([viewer/index.html](viewer/index.html)) is the linchpin:
Recall loads it as the bot's camera. It joins the LiveKit room, **renders** the
Anam avatar's audio+video (streamed back out to the meeting by Recall) and
**publishes** the meeting's audio into the room so the agent can hear it. When
that page joins the room, LiveKit dispatches a session to the worker — one
session per meeting.

## Layout

```
avatar-agent/
├─ src/avatar_agent/
│  ├─ agent.py          # LiveKit worker: STT→LLM→TTS + Anam avatar (the entrypoint)
│  ├─ tools.py          # @function_tool defs → backend calls (add a tool here, CT-3)
│  ├─ backend.py        # httpx client for the backend API (central-kg-api)
│  ├─ runtime.py        # per-session userdata (backend + tool log)
│  ├─ observability.py  # per-leg latency, tool-call log, post-call artifact
│  ├─ recall.py         # Recall.ai bot create/leave
│  ├─ dispatch.py       # `dispatch-bot <meet-url>` — send the avatar into a call
│  └─ config.py         # env/settings
├─ viewer/index.html    # the page Recall streams as the bot's camera
├─ tests/test_tools.py  # tool error-handling + summarizer + idempotency
└─ Dockerfile           # the worker container
```

## Run it

### 1. Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
# fetch VAD / turn-detector models for the cascade pipeline
python -m avatar_agent.agent download-files
```

### 2. Configure

```bash
cp .env.example .env
# fill: RECALL_API_KEY, LIVEKIT_* , ANAM_* , a cognition pipeline, BACKEND_*
```

Pick a **cognition pipeline** with `PIPELINE_MODE`:
- `cascade` (default) — streaming STT → **Anthropic** LLM → streaming TTS
  (Cartesia). Gives per-leg latency metrics; Anthropic-native (matches Sunstead).
  STT engine is `STT_PROVIDER`: **`soniox`** (default, streaming — needs
  `SONIOX_API_KEY`) or `deepgram` (needs `DEEPGRAM_API_KEY`). Also needs
  `ANTHROPIC_API_KEY` + `CARTESIA_API_KEY`.
- `realtime` — OpenAI Realtime, one speech-to-speech model. Lowest latency, one
  key (`OPENAI_API_KEY`), but no per-leg breakdown.

### 3. Start the worker

```bash
avatar-agent start          # connects to LiveKit Cloud, waits for sessions
```

### 4. Host the viewer & send the bot in

Host `viewer/` somewhere Recall's browser can reach (Vercel, S3, ngrok for dev)
and set `VIEWER_URL`. Then:

```bash
dispatch-bot https://meet.google.com/abc-defg-hij
# → bot joins the Meet; the avatar appears as its camera
dispatch-bot --leave <bot_id>   # pull it back out
```

## Deploy on AWS

A complete Terraform stack lives in [deploy/](deploy/) — self-hosted LiveKit on
EC2, the agent worker on ECS Fargate, the viewer on S3+CloudFront, and dispatch
as a Lambda Function URL (secrets in SSM). See [deploy/README.md](deploy/README.md)
for the runbook.

## Custom tools

Tools are LLM function schemas whose handlers call the backend. They live in
[src/avatar_agent/tools.py](src/avatar_agent/tools.py); the docstring *is* the
schema the model sees. Shipped tools (calling `central-kg-api`):

| Tool | Backend | Purpose |
|---|---|---|
| `lookup_context(query)` | `GET /query` | Search the knowledge graph for relevant people/tasks/code/docs. |
| `get_entity(entity_id, hops)` | `GET /entity/{id}` | Expand one entity's neighborhood. |
| `recent_activity(since)` | `GET /timeline` | What changed recently. |
| `record_action_item(description, owner)` | `POST /update` | **Write** — capture a follow-up (idempotent). |
| `delegate(intent, brief)` | gateway `POST /tasks` | **Hand off** (opt-in) — dispatch heavy work to the async worker suite. See delegation brain below. |

**Delegation brain.** By default the avatar **emits each final user utterance** to the
gateway's `POST /transcript` (→ `meeting.transcript`) and the **planner** does all routing
— the same path `make mock` exercises, and it lights up the FE feed. So `delegate()` is
**not** an always-on tool; set `AVATAR_DELEGATES=true` to give the avatar its own dispatch
tool instead (then run the planner OFF). See [docs/DESIGN.md](../docs/DESIGN.md) §6.

**Add a tool** (story CT-3): write a `@function_tool` async function and append it to
`BASE_TOOLS`. The conversation loop in `agent.py` is untouched.

Every tool **degrades gracefully** (CT-4): on a backend error/timeout it returns
a sentence the avatar speaks ("I couldn't reach the knowledge base just now…")
instead of raising into the pipeline. Writes send the tool-call's correlation id
as an `Idempotency-Key` so an at-least-once retry can't double-write.

## Observability

- **Per-leg latency** (STT / LLM / TTS / EOU) is logged per turn via LiveKit's
  `metrics_collected` events (OB-2).
- **Every tool call** is logged with inputs, output, duration, and a correlation
  id (OB-1).
- A **post-call artifact** (transcript + tool-call timeline) is written to
  `ARTIFACT_DIR` on session shutdown.

## Test

```bash
pytest          # tool error-handling, summarizer, idempotency wiring
```

The tool tests run without a live LiveKit session or backend — they invoke the
tool functions against a fake backend, so they're fast and CI-friendly.

## Notes & swap-points

- **Avatar is swappable.** Anam is the v1 choice for latency headroom; the
  design doc benchmarks it against Tavus / HeyGen / Simli. Swapping is a one-line
  change of the `AvatarSession` plugin in `agent.py`.
- **Backend is generic.** Point `BACKEND_URL` at any token-authed REST service;
  the shipped tools assume the `central-kg-api` surface, but `backend.py` is the
  only file that knows the endpoints.
- **Latency budget.** The PRD targets `BACKEND_TIMEOUT ≤ 0.3s` for tool
  endpoints; it's relaxed in `.env.example` for local dev — tighten for prod.
```

# Handoff — wire the avatar to the agent suite (the last demo wire)

> For the `avatar-agent` owner. The worker half ("delegate → Kafka → agent → result on the FE") is **built and
> proven**; this is the one remaining wire: an avatar tool that hands work to the suite. ~15 min, in your idiom
> (another `@function_tool` → REST). Design context: [DESIGN.md](DESIGN.md) §3 (seam option a).

## The flow

```
avatar LLM ── @function_tool ──▶ POST {gateway}/tasks ──▶ Kafka agent.tasks.* ──▶ worker (web/git/data)
   │  "On it — I'll put it on screen."                                                   │
   └──────────── speaks the ack immediately (fire-and-forget) ── result ──▶ agent.results ──▶ FE dashboard
```

Heavy work (a site build) takes seconds–minutes, so the tool is **fire-and-forget**: it kicks the task off, the
avatar **acks fast**, and the deliverable shows up on the **FE dashboard** (the gateway's `WS /stream`). The avatar
does *not* wait for or speak the result (that's a later, optional notify edge).

## The seam — the gateway's `POST /tasks` (stable contract)

```
POST {GATEWAY_URL}/tasks
  body: { "intent": "<intent>", "args": { ... }, "meeting_id": "<id>", "requested_by": "avatar" }
  200 : { "task_id": "tsk_xxx", "status": "accepted", "topic": "agent.tasks.web" }
```
Valid `intent`s: `build_website`, `update_website` (web-agent) · `analyze`, `summarize_metrics`, `query_data`
(data-agent) · `read_git`, `blame`, `who_changed`, `recent_changes` (git-agent). The gateway is the agent-runner
container, default port **8800**.

> Codebase *reads* ("who touched auth?") you can keep answering yourself via your existing `lookup_context` KG tools
> — that's the lower-latency path. **Delegation earns its keep for work the avatar can't do in-call: building a
> site, running an analysis.** So the one tool that matters for the headline demo is `build_website`.

## 1. Env var

```bash
GATEWAY_URL=http://localhost:8800          # the agent-runner gateway (set to its host:port in deploy)
```
Add it to `config.py` (`settings()`), mirroring `backend_url`.

## 2. The tool (paste into `src/avatar_agent/tools.py`)

```python
import os
import httpx
from livekit.agents import RunContext
from livekit.agents.llm import function_tool

from .runtime import AgentRuntime

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8800")


@function_tool
async def build_website(context: RunContext[AgentRuntime], brief: str, style: str = "") -> str:
    """Build and deploy a one-page website from a short brief; the live link appears on the shared screen.

    Use when someone on the call asks to create, build, or put up a landing page / site / page. This is
    asynchronous — it starts the build and the URL shows on screen when ready; don't wait for it.

    Args:
        brief: what the site is for, one sentence (e.g. "a landing page for Sunstead").
        style: optional visual direction (e.g. "dark, aurora").
    """
    payload = {
        "intent": "build_website",
        "args": {"brief": brief, "style": style},
        "meeting_id": getattr(context.userdata, "meeting_id", "mtg_dev"),
        "requested_by": "avatar",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(f"{GATEWAY_URL}/tasks", json=payload)
            r.raise_for_status()
        return "On it — I'm building that page now and I'll put the link up on screen when it's ready."
    except Exception:
        return "I couldn't reach the build service just now, so I haven't started that page."
```

Optional generic version for data/git delegation later (same shape, `intent` chosen by the model):

```python
@function_tool
async def delegate_task(context: RunContext[AgentRuntime], intent: str, request: str) -> str:
    """Delegate a heavier task to a specialist agent. intent ∈ {build_website, update_website, analyze,
    summarize_metrics, query_data}. `request` is the natural-language brief. Async — result shows on screen."""
    payload = {"intent": intent, "args": {"brief": request, "question": request},
               "meeting_id": getattr(context.userdata, "meeting_id", "mtg_dev"), "requested_by": "avatar"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            (await c.post(f"{GATEWAY_URL}/tasks", json=payload)).raise_for_status()
        return "On it — I've handed that off and I'll surface the result on screen."
    except Exception:
        return "I couldn't reach the agent service just now."
```

## 3. Register it

Append to `BACKEND_TOOLS` in `tools.py` — `agent.py`'s conversation loop is untouched (your CT-3 pattern):

```python
BACKEND_TOOLS = [lookup_context, get_entity, recent_activity, record_action_item, build_website]
```

## 4. meeting_id (so the result lands on the right screen)

The gateway's `WS /stream?meeting_id=` filters by `meeting_id`. For the result to appear on the FE dashboard:
- set the payload's `meeting_id` to the same value the FE dashboard subscribes to, **or**
- for the demo, use one shared id (e.g. `"mtg_dev"`) on both sides, **or** have the FE subscribe unfiltered.

Simplest: stash the session's meeting id on `AgentRuntime` (derive from the LiveKit room, which `dispatch.py`
already builds as `avatar-<meet-code>`) and pass it through.

## 5. Verify before wiring (no avatar needed)

With the agent-runner + gateway running (`make run` + `make gateway`):
```bash
curl -X POST http://localhost:8800/tasks -H 'content-type: application/json' \
  -d '{"intent":"build_website","args":{"brief":"landing page for Sunstead","style":"dark, aurora"},"meeting_id":"mtg_dev"}'
# → {"task_id":"tsk_…","status":"accepted","topic":"agent.tasks.web"}  ; the URL then appears on the dashboard
```

That `curl` *is* exactly what your tool does — once it returns a URL on the dashboard, the tool will too.

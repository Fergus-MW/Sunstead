# Avatar → agent-suite delegation — ✅ SHIPPED

> This was a handoff to *build* the avatar's delegation wire. **It's built.** Kept as a
> pointer; the live contract is the code below + [DESIGN.md](DESIGN.md) §3 (seam option a).

The avatar hands heavy work to the agent suite through the gateway, fire-and-forget; the
result surfaces on the FE dashboard (gateway `WS /stream`). The avatar never speaks Kafka.

```
avatar LLM ── delegate(intent, brief) ──▶ POST {GATEWAY_URL}/tasks ──▶ agent.tasks.* ──▶ worker (web/git/data)
   │  "On it — I'll put it on screen."                                                        │
   └──────────── acks immediately (fire-and-forget) ──── result ──▶ agent.results ──▶ FE dashboard
```

What shipped (in `avatar-agent/src/avatar_agent/`):
- **`gateway.py`** — `GatewayClient.delegate(intent, args, meeting_id)` → `POST /tasks`, graceful degrade.
- **`tools.py`** — the `delegate(intent, brief)` `@function_tool` (registered in `BACKEND_TOOLS`);
  `DELEGATE_ARG_KEY` maps each intent to the arg key the receiving agent reads (web→`brief`, git/data→`question`).
- **`config.py`** — `GATEWAY_URL` / `GATEWAY_TOKEN` / `GATEWAY_TIMEOUT`.
- **`agent.py` / `runtime.py`** — wires the client in and threads the canonical `meeting_id` (derived from the
  LiveKit room, `dispatch.py:meeting_id_for_room`) so results correlate to the call on the FE.

The seam contract (stable):
```
POST {GATEWAY_URL}/tasks
  body: { "intent": "<intent>", "args": { ... }, "meeting_id": "<id>", "requested_by": "avatar" }
  200 : { "task_id": "tsk_xxx", "status": "accepted", "topic": "agent.tasks.web" }
```
Valid intents: `build_website`, `update_website` · `analyze`, `summarize_metrics`, `query_data` ·
`read_git`, `blame`, `who_changed`, `recent_changes`. Gateway default port **8800**.

Verify without the avatar (`make run` + `make gateway`), or drive the whole loop with `make mock`:
```bash
curl -X POST http://localhost:8800/tasks -H 'content-type: application/json' \
  -d '{"intent":"build_website","args":{"brief":"landing page for Sunstead","style":"dark, aurora"},"meeting_id":"mtg_dev"}'
# → {"task_id":"tsk_…","status":"accepted","topic":"agent.tasks.web"}
```

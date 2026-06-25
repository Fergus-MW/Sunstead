# Verify the delegation loop (before recording the demo)

Two independent things must hold for "Aino hears a request → the team does it" to work:

- **Backend** — the planner turns a `meeting.transcript` into `agent.tasks.*` and the agents run. Provable
  locally today (no Recall/LiveKit/Anam).
- **Avatar STT → transcript** — the live avatar actually publishes a **final** `meeting.transcript` for each
  spoken utterance. This is the risky one in **realtime (OpenAI) mode** and needs a real call to prove.

`mock_meeting.py` only covers the backend — it publishes the transcript itself, standing in for the avatar's
STT. It can NOT tell you whether the realtime avatar emits finals. Do both parts.

---

## Part A — backend loop + the split-request fix (local, ~2 min)

```bash
cd agent-system
make up && make topics      # redpanda + topics      (terminal 1)
make run                    # agent-runner            (terminal 2)
make planner                # the delegation brain    (terminal 3)
make mock                   # speak + watch           (terminal 4)
```

In `make mock`, check three cases:

1. **Single request** — `build us a dark landing page for Sunstead`
   → planner logs `delegated build_website … (task …)`; `agent.activity` then `agent.results` print.
2. **Compound request** — `build a site and tell me who last touched auth`
   → **two** tasks delegated (`build_website` + `who_changed`).
3. **Split request (the #3 fix)** — send as **two separate lines**:
   ```
   you> can you build us a site
   you> about the EU AI Act, and make the facts accurate
   ```
   → the *second* line delegates **one** `build_website` whose brief names "EU AI Act".
   Before the fix the planner saw only "about the EU AI Act…" with no verb and delegated nothing.
   Confirm it does **not** re-delegate the first line (no duplicate task) — that's the "do not re-action
   earlier lines" guard working.

Also sanity-check backchannel (`yeah totally`) → **no** task (the keyword pre-filter / Haiku gate drop it).

---

## Part B — realtime avatar emits final transcripts (real call, the actual risk)

The avatar publishes transcript from the `user_input_transcribed` event with `is_final=True`
([agent.py](../avatar-agent/src/avatar_agent/agent.py)). **Confirm this fires in `PIPELINE_MODE=realtime`
(OpenAI)** — if the realtime model doesn't surface final input-transcription events, the planner is silently
starved and nothing delegates, even though the avatar still talks.

1. Start the agent-runner + planner + gateway pointed at the **same Kafka** the avatar uses (Aiven for a real
   call — see below).
2. Bring up the avatar in realtime mode and join a real meeting; **speak a clear request** ("can you build a
   landing page for our launch?").
3. **Watch `meeting.transcript`** for a final from your utterance, then **`agent.activity`** for `delegated`.
   Quick tail (run from `agent-system/`):
   ```python
   # tail_transcript.py — uv run python tail_transcript.py
   import asyncio
   from shared import config
   from shared.kafka import consume
   async def main():
       s = config.load()
       async for m in consume(config.TRANSCRIPT, config.ACTIVITY, group_id="verify",
                              settings=s, auto_offset_reset="latest"):
           print(m.topic, m.value[:200])
   asyncio.run(main())
   ```
   - **Transcript finals appear + planner delegates** → realtime path is good. Ship it.
   - **No transcript finals** → realtime mode isn't emitting finals. Fixes, in order:
     switch that meeting to **cascade** mode (`PIPELINE_MODE=cascade`, which uses Soniox/Deepgram STT and is
     known to emit finals), or add a fallback that publishes from the realtime model's output transcription.
   - **Transcript finals but no delegation** → planner/Kafka issue, not the avatar (check topics exist on the
     live cluster — see [the Kafka topic note](DEPLOY.md), and that the planner group is reading `earliest`).

---

## One mutual-exclusion rule (don't trip the double-delegation guard)

- **Planner mode (default):** run the planner; the avatar has no `delegate()` tool and feeds transcript. ✅
- **`AVATAR_DELEGATES=true`:** the avatar delegates itself and **suppresses transcript-for-routing** — do
  **not** also run the planner, or every request is delegated twice. The avatar logs a warning at startup in
  this mode.

Pick one. The default (planner as the single brain) is the recommended path.

"""The LiveKit agent worker: STT → LLM (tool calling) → TTS, fronted by an Anam
streaming avatar. One worker process serves meetings; LiveKit dispatches a job
per room (the Recall viewer joining the room is what triggers a session).

Run the worker:
    avatar-agent start            # or: python -m avatar_agent.agent start
Predownload model files (VAD / turn detector), e.g. in the Docker build:
    avatar-agent download-files
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RunContext,
    cli,
    metrics,
)
from livekit.plugins import anam, anthropic, cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from .backend import BackendClient
from .config import Settings, settings
from .observability import CallArtifact, ToolCallLog
from .runtime import AgentRuntime
from .tools import BACKEND_TOOLS

load_dotenv()
logger = logging.getLogger("avatar-agent")

INSTRUCTIONS = (
    "You are Sunstead, a helpful AI teammate present as a live video avatar in a "
    "meeting. You can see the conversation transcript and speak back into the call. "
    "Keep replies short and conversational — one or two sentences — since people are "
    "listening, not reading. Do not use markdown, emojis, or special characters. "
    "When someone asks about company data — people, projects, tasks, code, documents, "
    "or recent activity — use your tools to look it up before answering, and speak the "
    "result naturally. Only record an action item when you are explicitly asked to "
    "capture a task or follow-up. If a tool fails, say so briefly and carry on; never "
    "invent data you couldn't retrieve."
)

# Silero VAD is expensive to construct; cache it across jobs in the worker
# process (effectively a prewarm after the first session). Cascade mode only.
_VAD = None


def _get_vad():
    global _VAD
    if _VAD is None:
        _VAD = silero.VAD.load()
    return _VAD


def build_session(cfg: Settings, runtime: AgentRuntime) -> AgentSession[AgentRuntime]:
    """Construct the AgentSession for the configured pipeline mode.

    The avatar plugin is swappable and the cognition legs are swappable — changing
    a provider here is a one-line edit, never a rewrite (PRD goal §2.2).
    """
    if cfg.pipeline_mode == "realtime":
        # Single speech-to-speech model: lowest latency, one API key, but no
        # per-leg latency breakdown.
        return AgentSession(
            userdata=runtime,
            llm=openai.realtime.RealtimeModel(voice=cfg.realtime_voice),
        )

    # Cascade: streaming STT → Anthropic LLM (tool calling) → streaming TTS, with
    # VAD + a turn detector for natural turn-taking and barge-in (CV-3).
    tts_kwargs = {"voice": cfg.tts_voice} if cfg.tts_voice else {}
    return AgentSession(
        userdata=runtime,
        stt=deepgram.STT(model=cfg.stt_model),
        llm=anthropic.LLM(model=cfg.llm_model),
        tts=cartesia.TTS(**tts_kwargs),
        vad=_get_vad(),
        turn_detection=MultilingualModel(),
    )


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    cfg = settings()
    ctx.log_context_fields = {"room": ctx.room.name}

    # Per-session runtime: a backend client + a tool-call log, reachable from
    # every tool via RunContext.userdata.
    backend = BackendClient(settings=cfg)
    tools_log = ToolCallLog()
    runtime = AgentRuntime(backend=backend, tools_log=tools_log)
    artifact = CallArtifact(room=ctx.room.name, tools_log=tools_log)

    session = build_session(cfg, runtime)

    # ── Observability: per-leg latency (STT/LLM/TTS/EOU) per turn (OB-2) ──
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)

    # ── Post-call artifact: transcript + tool-call timeline (OB-1) ──
    async def _on_shutdown() -> None:
        try:
            artifact.set_transcript(session.history.to_dict().get("items", []))
        except Exception:  # never let artifact writing break shutdown
            logger.exception("failed to capture transcript")
        artifact.write(cfg.artifact_dir)
        await backend.aclose()

    ctx.add_shutdown_callback(_on_shutdown)

    # ── Avatar: render a real-time lip-synced face into the room. When an avatar
    # session is attached, the agent's audio is routed to the avatar worker (which
    # publishes synced audio+video) rather than straight to the room. ──
    # In console mode (`avatar-agent console`) there's no rendered avatar and the
    # agent talks to your local mic/speakers, so skip the avatar — otherwise it
    # would swallow the agent's audio and you'd hear nothing.
    if ctx.room.name == "console":
        logger.info("console mode: skipping Anam avatar, routing audio locally")
    else:
        avatar = anam.AvatarSession(
            persona_config=anam.PersonaConfig(
                name=cfg.anam_avatar_name,
                avatarId=cfg.anam_avatar_id,
            ),
            api_key=cfg.anam_api_key,
        )
        await avatar.start(session, room=ctx.room)

    await session.start(
        agent=Agent(instructions=INSTRUCTIONS, tools=BACKEND_TOOLS),
        room=ctx.room,
    )


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()

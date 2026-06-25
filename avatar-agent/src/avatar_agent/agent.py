"""The LiveKit agent worker: STT → LLM (tool calling) → TTS, fronted by an Anam
streaming avatar. One worker process serves meetings; LiveKit dispatches a job
per room (the Recall viewer joining the room is what triggers a session).

Run the worker:
    avatar-agent start            # or: python -m avatar_agent.agent start
Predownload model files (VAD / turn detector), e.g. in the Docker build:
    avatar-agent download-files
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RunContext,
    UserInputTranscribedEvent,
    cli,
    metrics,
)
from livekit.plugins import anam

from .backend import BackendClient
from .config import Settings, settings
from .observability import CallArtifact, ToolCallLog
from .dispatch import meeting_id_for_room
from .gateway import GatewayClient
from .runtime import AgentRuntime
from .tools import BASE_TOOLS, delegate

load_dotenv()
logger = logging.getLogger("avatar-agent")

# Base persona — read the KG, capture action items, stay conversational.
_BASE_INSTRUCTIONS = (
    "You are Sunstead, a helpful AI teammate present as a live video avatar in a "
    "meeting. You can see the conversation transcript and speak back into the call. "
    "Keep replies short and conversational — one or two sentences — since people are "
    "listening, not reading. Do not use markdown, emojis, or special characters. "
    "When someone asks about company data — people, projects, tasks, code, documents, "
    "or recent activity — use your tools to look it up before answering, and speak the "
    "result naturally. Only record an action item when you are explicitly asked to "
    "capture a task or follow-up. "
)
# Default (planner is the single brain): the team picks up build/do requests from the
# transcript automatically, so the avatar just acknowledges them out loud.
_PLANNER_CLAUSE = (
    "When someone asks the team to BUILD or DO real work — a web page, a code-history "
    "investigation, a data analysis — acknowledge it naturally and say the team is on it; "
    "they pick the request up from the meeting automatically, so you don't act further on it. "
)
# AVATAR_DELEGATES mode: the avatar dispatches the work itself via the delegate tool.
_DELEGATE_CLAUSE = (
    "When someone asks you to BUILD or DO real work — a web page, a code-history "
    "investigation, a data analysis — use the delegate tool to hand it to the specialist "
    "team, and say you're on it; the result shows up on their dashboard. "
)
_TAIL = "If a tool fails, say so briefly and carry on; never invent data you couldn't retrieve."


def _instructions(cfg: Settings) -> str:
    return _BASE_INSTRUCTIONS + (_DELEGATE_CLAUSE if cfg.avatar_delegates else _PLANNER_CLAUSE) + _TAIL

# Silero VAD is expensive to construct; cache it across jobs in the worker
# process (effectively a prewarm after the first session). Cascade mode only.
_VAD = None


def _get_vad():
    global _VAD
    if _VAD is None:
        from livekit.plugins import silero

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
        from livekit.plugins import openai

        return AgentSession(
            userdata=runtime,
            llm=openai.realtime.RealtimeModel(voice=cfg.realtime_voice),
        )

    # Cascade: streaming STT → Anthropic LLM (tool calling) → streaming TTS, with
    # VAD + a turn detector for natural turn-taking and barge-in (CV-3).
    from livekit.plugins import anthropic, cartesia
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    tts_kwargs = {"voice": cfg.tts_voice} if cfg.tts_voice else {}
    return AgentSession(
        userdata=runtime,
        stt=_build_stt(cfg),
        llm=anthropic.LLM(model=cfg.llm_model),
        tts=cartesia.TTS(**tts_kwargs),
        vad=_get_vad(),
        turn_detection=MultilingualModel(),
    )


def _build_stt(cfg: Settings):
    """The streaming STT leg. Soniox (default) or Deepgram — each plugin reads its
    own key from the env. Fail clearly if the chosen provider's key is missing."""
    if cfg.stt_provider == "soniox":
        if not cfg.soniox_api_key:
            raise RuntimeError("STT_PROVIDER=soniox but SONIOX_API_KEY is not set.")
        from livekit.plugins import soniox

        hints = [h.strip() for h in cfg.soniox_language_hints.split(",") if h.strip()]
        return soniox.STT(
            params=soniox.STTOptions(model=cfg.soniox_model, language_hints=hints)
        )

    if not cfg.deepgram_api_key:
        raise RuntimeError("STT_PROVIDER=deepgram but DEEPGRAM_API_KEY is not set.")
    from livekit.plugins import deepgram

    return deepgram.STT(model=cfg.stt_model)


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    cfg = settings()
    ctx.log_context_fields = {"room": ctx.room.name}

    # Per-session runtime: a backend client (KG reads), a delegation gateway
    # client (hand work to the worker suite), the canonical meeting_id (so results
    # correlate to this call on the FE), and a tool-call log — all reachable from
    # every tool via RunContext.userdata.
    backend = BackendClient(settings=cfg)
    gateway = GatewayClient(settings=cfg)
    tools_log = ToolCallLog()
    runtime = AgentRuntime(
        backend=backend,
        tools_log=tools_log,
        gateway=gateway,
        meeting_id=meeting_id_for_room(ctx.room.name),
    )
    artifact = CallArtifact(room=ctx.room.name, tools_log=tools_log)

    session = build_session(cfg, runtime)

    # ── Observability: per-leg latency (STT/LLM/TTS/EOU) per turn (OB-2) ──
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)

    # ── Single delegation brain (DESIGN §6): publish each FINAL user utterance to the
    # gateway → `meeting.transcript`, so the planner routes it and the FE feed shows it.
    # Best-effort and off the hot path; transcript emission must never disrupt the call. ──
    _bg: set[asyncio.Task] = set()

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final or not (ev.transcript or "").strip() or not gateway.enabled:
            return

        async def _emit() -> None:
            try:
                await gateway.publish_transcript(
                    meeting_id=runtime.meeting_id,
                    text=ev.transcript,
                    speaker=getattr(ev, "speaker_id", None),
                )
            except Exception:  # never let transcript emission break the call
                logger.warning("failed to publish transcript", exc_info=True)

        t = asyncio.create_task(_emit())
        _bg.add(t)
        t.add_done_callback(_bg.discard)

    # ── Post-call artifact: transcript + tool-call timeline (OB-1) ──
    async def _on_shutdown() -> None:
        try:
            artifact.set_transcript(session.history.to_dict().get("items", []))
        except Exception:  # never let artifact writing break shutdown
            logger.exception("failed to capture transcript")
        artifact.write(cfg.artifact_dir)
        await backend.aclose()
        await gateway.aclose()

    ctx.add_shutdown_callback(_on_shutdown)

    # ── Avatar: render a real-time lip-synced face into the room. When an avatar
    # session is attached, the agent's audio is routed to the avatar worker (which
    # publishes synced audio+video) rather than straight to the room. ──
    # Anam needs both an API key and an avatar (persona) id; fail with a clear
    # message rather than a cryptic Anam API error if either is missing.
    if not cfg.anam_api_key:
        raise RuntimeError(
            "ANAM_API_KEY (or ANAM_API_TOKEN) is not set — the avatar can't authenticate to Anam."
        )
    if not cfg.anam_avatar_id:
        raise RuntimeError(
            "ANAM_AVATAR_ID is not set — pick an avatar/persona id from the Anam "
            "dashboard (lab.anam.ai) and set it in your .env."
        )
    avatar = anam.AvatarSession(
        persona_config=anam.PersonaConfig(
            name=cfg.anam_avatar_name,
            avatarId=cfg.anam_avatar_id,
        ),
        api_key=cfg.anam_api_key,
    )
    await avatar.start(session, room=ctx.room)

    # Default: planner is the single brain (avatar emits transcript above), so the
    # avatar carries read tools only. AVATAR_DELEGATES adds the delegate() tool.
    tools = [*BASE_TOOLS, delegate] if cfg.avatar_delegates else list(BASE_TOOLS)
    await session.start(
        agent=Agent(instructions=_instructions(cfg), tools=tools),
        room=ctx.room,
    )


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()

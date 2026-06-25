"""Runtime configuration, loaded from the environment (see .env.example).

All secrets come from the environment / a secret manager — never hard-coded
(PRD §5.4). `settings()` is cached so every module sees one consistent view.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("avatar-agent.config")

PipelineMode = Literal["cascade", "realtime"]
SttProvider = Literal["soniox", "deepgram"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Recall.ai (meeting transport) ──
    recall_api_key: str = Field(default="", alias="RECALL_API_KEY")
    recall_region: str = Field(default="us-east-1", alias="RECALL_REGION")
    bot_name: str = Field(default="Sunstead Avatar", alias="BOT_NAME")

    # ── LiveKit (orchestration transport) ──
    livekit_url: str = Field(default="", alias="LIVEKIT_URL")
    livekit_api_key: str = Field(default="", alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="", alias="LIVEKIT_API_SECRET")
    viewer_url: str = Field(default="", alias="VIEWER_URL")

    # ── Anam (avatar) ──
    # Accept either ANAM_API_KEY (the name Anam/LiveKit document) or ANAM_API_TOKEN
    # (the name used in the shared repo-root vault) so the same key works for both.
    anam_api_key: str = Field(
        default="", validation_alias=AliasChoices("ANAM_API_KEY", "ANAM_API_TOKEN")
    )
    # Non-secret persona id (from the Anam dashboard). Defaulted so the avatar
    # runs out of the box; override per-deploy via env, or later per-session when
    # the FE lets a user pick which avatar joins (dispatch → job metadata).
    anam_avatar_id: str = Field(
        default="edf6fdcb-acab-44b8-b974-ded72665ee26", alias="ANAM_AVATAR_ID"
    )
    anam_avatar_name: str = Field(default="Sunstead", alias="ANAM_AVATAR_NAME")

    # ── Cognition ──
    pipeline_mode: PipelineMode = Field(default="cascade", alias="PIPELINE_MODE")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-sonnet-4-6", alias="LLM_MODEL")
    # STT leg: Soniox (streaming, our default — PLAN §STT) or Deepgram. Each plugin
    # reads its own key from the env (SONIOX_API_KEY / DEEPGRAM_API_KEY).
    stt_provider: SttProvider = Field(default="soniox", alias="STT_PROVIDER")
    soniox_api_key: str = Field(default="", alias="SONIOX_API_KEY")
    soniox_model: str = Field(default="stt-rt-v5", alias="SONIOX_MODEL")
    soniox_language_hints: str = Field(default="en", alias="SONIOX_LANGUAGE_HINTS")  # comma-sep
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")
    stt_model: str = Field(default="nova-3", alias="STT_MODEL")  # deepgram
    cartesia_api_key: str = Field(default="", alias="CARTESIA_API_KEY")
    tts_voice: str = Field(default="", alias="TTS_VOICE")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    realtime_voice: str = Field(default="alloy", alias="REALTIME_VOICE")

    # ── Backend API (custom tools target) ──
    backend_url: str = Field(default="http://localhost:8000", alias="BACKEND_URL")
    backend_token: str = Field(default="", alias="BACKEND_TOKEN")
    backend_timeout: float = Field(default=5.0, alias="BACKEND_TIMEOUT")

    # ── Delegation gateway (the avatar → worker-suite seam, DESIGN §3 option a) ──
    # The agent-system gateway's base URL. The `delegate` tool POSTs /tasks here to
    # hand heavy work to the async worker suite over Kafka. Empty = delegation off
    # (the tool degrades gracefully and says it can't dispatch right now).
    gateway_url: str = Field(default="", alias="GATEWAY_URL")
    gateway_token: str = Field(default="", alias="GATEWAY_TOKEN")
    gateway_timeout: float = Field(default=5.0, alias="GATEWAY_TIMEOUT")
    # Single delegation brain (DESIGN §6). Default: the avatar EMITS meeting.transcript
    # and the planner does all routing — so the same path serves the mock and the real
    # avatar. Set true to instead give the avatar its own `delegate()` tool (then run
    # the planner OFF, or both will delegate the same utterance).
    avatar_delegates: bool = Field(default=False, alias="AVATAR_DELEGATES")

    # ── Observability ──
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    artifact_dir: str = Field(default="./artifacts", alias="ARTIFACT_DIR")

    @property
    def recall_base_url(self) -> str:
        """Recall's API host is region-scoped, e.g. https://us-east-1.recall.ai."""
        return f"https://{self.recall_region}.recall.ai"


def _load_env_files() -> None:
    """Merge every `.env` from cwd up to the filesystem root into the environment,
    nearest-wins, skipping empty values — so the shared **repo-root `.env` vault**
    is picked up even when the worker is launched from `avatar-agent/`. Real env
    vars always win (we only `setdefault`). dotenv is an optional dev convenience;
    in prod, config comes from the environment / a secret manager (PRD §5.4).
    """
    try:
        from dotenv import dotenv_values

        merged: dict[str, str] = {}
        for d in reversed([Path.cwd(), *Path.cwd().parents]):  # farthest first → nearest overrides
            p = d / ".env"
            if p.exists():
                for k, v in dotenv_values(p).items():
                    if v and not v.lstrip().startswith("#"):
                        merged[k] = v
        for k, v in merged.items():
            os.environ.setdefault(k, v)
    except ImportError:
        pass  # python-dotenv is an optional dev dependency; prod uses the real env
    except Exception:  # a malformed/unreadable .env shouldn't be invisible
        logger.debug("failed to merge .env files", exc_info=True)


@lru_cache
def settings() -> Settings:
    _load_env_files()
    cfg = Settings()
    # Belt-and-suspenders: some LiveKit plugin code paths read ANAM_API_KEY straight
    # from the environment. If the key was provided only as ANAM_API_TOKEN, mirror it
    # to the canonical name so those paths authenticate too.
    if cfg.anam_api_key and not os.environ.get("ANAM_API_KEY"):
        os.environ["ANAM_API_KEY"] = cfg.anam_api_key
    return cfg

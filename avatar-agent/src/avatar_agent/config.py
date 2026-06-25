"""Runtime configuration, loaded from the environment (see .env.example).

All secrets come from the environment / a secret manager — never hard-coded
(PRD §5.4). `settings()` is cached so every module sees one consistent view.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PipelineMode = Literal["cascade", "realtime"]


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
    anam_api_key: str = Field(default="", alias="ANAM_API_KEY")
    anam_avatar_id: str = Field(default="", alias="ANAM_AVATAR_ID")
    anam_avatar_name: str = Field(default="Sunstead", alias="ANAM_AVATAR_NAME")

    # ── Cognition ──
    pipeline_mode: PipelineMode = Field(default="cascade", alias="PIPELINE_MODE")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-sonnet-4-6", alias="LLM_MODEL")
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")
    stt_model: str = Field(default="nova-3", alias="STT_MODEL")
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

    # ── Observability ──
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    artifact_dir: str = Field(default="./artifacts", alias="ARTIFACT_DIR")

    @property
    def recall_base_url(self) -> str:
        """Recall's API host is region-scoped, e.g. https://us-east-1.recall.ai."""
        return f"https://{self.recall_region}.recall.ai"


@lru_cache
def settings() -> Settings:
    return Settings()

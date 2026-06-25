"""Recall.ai client — joins/leaves a meeting and streams the avatar viewer page
out as the bot's camera via `output_media` (PRD glossary §7.2, Recall docs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


@dataclass
class RecallClient:
    settings: Settings

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.recall_base_url,
            headers={
                "Authorization": f"Token {self.settings.recall_api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def create_bot(self, *, meeting_url: str, viewer_url: str) -> dict[str, Any]:
        """Send a bot into `meeting_url` that streams `viewer_url` as its camera.

        The viewer page joins our LiveKit room: it renders the Anam avatar's
        video/audio (out to the meeting) and publishes the meeting's audio back
        into the room for the agent to hear.
        """
        payload: dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": self.settings.bot_name,
            "output_media": {
                "camera": {
                    "kind": "webpage",
                    "config": {"url": viewer_url},
                }
            },
        }
        # Bot variant = the only frame-rate lever Recall exposes (it's discrete):
        # "web" renders the camera webpage at ~15 fps, "web_4_core" at ~30 fps.
        # Keyed per meeting platform; we only join Google Meet.
        if self.settings.recall_bot_variant:
            payload["variant"] = {"google_meet": self.settings.recall_bot_variant}
        async with self._http() as client:
            resp = await client.post("/api/v1/bot", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def leave_bot(self, bot_id: str) -> None:
        """Make the bot leave the call (JP-3)."""
        async with self._http() as client:
            resp = await client.post(f"/api/v1/bot/{bot_id}/leave_call")
            resp.raise_for_status()

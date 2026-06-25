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

    async def create_bot(
        self, *, meeting_url: str, viewer_url: str | None = None
    ) -> dict[str, Any]:
        """Send a bot into `meeting_url`.

        With `viewer_url`, the bot streams that page as its camera via
        `output_media` — the page renders the Anam avatar out to the meeting and
        publishes the meeting audio back into the LiveKit room. Without it, the
        bot just joins (a bare smoke test of join + transcription).
        """
        payload: dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": self.settings.bot_name,
        }
        if viewer_url:
            payload["output_media"] = {
                "camera": {"kind": "webpage", "config": {"url": viewer_url}}
            }
        async with self._http() as client:
            resp = await client.post("/api/v1/bot", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_bot(self, bot_id: str) -> dict[str, Any]:
        """Fetch a bot's current state, including its `status_changes` history."""
        async with self._http() as client:
            resp = await client.get(f"/api/v1/bot/{bot_id}")
            resp.raise_for_status()
            return resp.json()

    async def leave_bot(self, bot_id: str) -> None:
        """Make the bot leave the call (JP-3)."""
        async with self._http() as client:
            resp = await client.post(f"/api/v1/bot/{bot_id}/leave_call")
            resp.raise_for_status()

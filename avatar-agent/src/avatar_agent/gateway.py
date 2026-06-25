"""HTTP client for the agent-system gateway — the avatar → worker-suite seam.

This is the *delegation* edge (DESIGN §3, option a): the realtime avatar hands
heavy, async work to the worker suite by POSTing a task to the gateway, which
produces the `agent.tasks.*` Kafka message. The avatar never speaks Kafka itself
— it stays a human-facing HTTP client, exactly like `backend.py` for KG reads.

The call is fire-and-forget from the avatar's point of view: the deliverable
surfaces later on the front-end (gateway → WS /stream), not back through the
avatar's tool return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .backend import BackendError  # reuse the speakable-failure error type
from .config import Settings


@dataclass
class GatewayClient:
    """Thin wrapper over the gateway's task front door (`POST /tasks`).

    One client per session; keep-alive pool reused across delegations. Mirrors
    `BackendClient` so the tool layer's error handling stays uniform.
    """

    settings: Settings
    _client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.gateway_url)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.settings.gateway_token:
                headers["Authorization"] = f"Bearer {self.settings.gateway_token}"
            self._client = httpx.AsyncClient(
                base_url=self.settings.gateway_url.rstrip("/"),
                headers=headers,
                timeout=self.settings.gateway_timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def delegate(
        self, *, intent: str, args: dict[str, Any], meeting_id: str, requested_by: str = "avatar"
    ) -> dict[str, Any]:
        """Enqueue a delegated task. Returns the gateway's ack ({task_id, status, topic}).

        Raises `BackendError` (speakable) on any transport/HTTP failure so the
        tool can degrade gracefully instead of raising into the pipeline.
        """
        if not self.enabled:
            raise BackendError("task delegation isn't configured")
        payload = {
            "intent": intent,
            "args": args,
            "meeting_id": meeting_id,
            "requested_by": requested_by,
        }
        try:
            resp = await self._http().post("/tasks", json=payload)
        except httpx.TimeoutException as exc:
            raise BackendError("the task gateway timed out") from exc
        except httpx.HTTPError as exc:
            raise BackendError("the task gateway was unreachable") from exc

        if resp.status_code >= 400:
            # A 422 here almost always means an intent outside the controlled vocab.
            raise BackendError(_safe_detail(resp), status=resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise BackendError("the task gateway returned a malformed response") from exc


def _safe_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            for field in ("detail", "error", "message"):
                if isinstance(data.get(field), str):
                    return data[field]
    except ValueError:
        pass
    return f"the task gateway returned status {resp.status_code}"

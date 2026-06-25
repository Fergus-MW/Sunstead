"""HTTP client for the separate backend API the custom tools call.

In Sunstead this is the `central-kg-api` (the knowledge-graph context service);
in another deployment it's whatever REST service you point `BACKEND_URL` at.
The client is intentionally thin — auth, timeout, and a uniform error type — so
the tool handlers in `tools.py` stay declarative (PRD §5.2/§5.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class BackendError(Exception):
    """A backend call that failed in a way the agent can verbalize gracefully."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class BackendClient:
    """Stateless wrapper over the backend REST API.

    One client is shared for a session's lifetime; it holds a keep-alive pool so
    repeated tool calls in a meeting reuse connections (latency budget, PRD §4.4).
    """

    settings: Settings
    _client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.settings.backend_token:
                headers["Authorization"] = f"Bearer {self.settings.backend_token}"
            self._client = httpx.AsyncClient(
                base_url=self.settings.backend_url.rstrip("/"),
                headers=headers,
                timeout=self.settings.backend_timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = await self._http().request(method, path, **kw)
        except httpx.TimeoutException as exc:  # the one we most expect under load
            raise BackendError("the backend timed out") from exc
        except httpx.HTTPError as exc:
            raise BackendError("the backend was unreachable") from exc

        if resp.status_code >= 400:
            # Surface a structured-but-safe reason; never leak raw 5xx bodies aloud.
            detail = _safe_detail(resp)
            raise BackendError(detail, status=resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise BackendError("the backend returned a malformed response") from exc

    # ── central-kg-api surface (see central-kg-api/README.md) ──

    async def query(self, q: str) -> dict[str, Any]:
        """Hybrid search → ranked nodes + a focused subgraph."""
        return await self._request("GET", "/query", params={"q": q})

    async def entity(self, node_id: str, hops: int = 1) -> dict[str, Any]:
        """A node plus its 1–N hop neighborhood."""
        return await self._request("GET", f"/entity/{node_id}", params={"hops": hops})

    async def timeline(self, since: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Time-ordered events (recent activity / follow-ups)."""
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        return await self._request("GET", "/timeline", params=params)

    async def append_event(
        self, *, node: str, kind: str, body: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Append an event (e.g. an action item) — a write tool.

        Sends an idempotency key so an at-least-once retry can't double-write
        (PRD §5.3). Callers may pass their own key to make a retry safe.
        """
        key = idempotency_key or str(uuid.uuid4())
        return await self._request(
            "POST",
            "/update",
            headers={"Idempotency-Key": key},
            json={"node": node, "kind": kind, "body": body},
        )


def _safe_detail(resp: httpx.Response) -> str:
    """A short, speakable failure reason from a non-2xx response."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            for field in ("detail", "error", "message"):
                if isinstance(data.get(field), str):
                    return data[field]
    except ValueError:
        pass
    return f"the backend returned status {resp.status_code}"

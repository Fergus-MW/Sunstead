"""Async HTTP client for the teammate's central-kg-api.

This is Seam B (docs/AGENT_SYSTEM.md §5): the ONLY way the agents touch the knowledge
graph. Endpoint shapes are the agreed interface — reconcile here if the KG team's differ.

Set KG_STUB=true to develop the listener/agents before central-kg-api is up.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import config


class KGClient:
    def __init__(self, settings: config.Settings | None = None):
        self._s = settings or config.load()
        self._http = httpx.AsyncClient(base_url=self._s.kg_base_url, timeout=30)

    async def query(self, q: str, params: dict | None = None) -> dict[str, Any]:
        if self._s.kg_stub:
            return {"rows": [], "stub": True}
        r = await self._http.post("/query", json={"query": q, "params": params or {}})
        r.raise_for_status()
        return r.json()

    async def semantic_search(self, text: str, k: int = 8) -> dict[str, Any]:
        if self._s.kg_stub:
            return {"hits": [], "stub": True}
        r = await self._http.post("/semantic_search", json={"text": text, "k": k})
        r.raise_for_status()
        return r.json()

    async def quicksearch(self, text: str) -> dict[str, Any]:
        if self._s.kg_stub:
            return {"hits": [], "stub": True}
        r = await self._http.post("/quicksearch", json={"text": text})
        r.raise_for_status()
        return r.json()

    async def upsert(self, nodes: list[dict] | None = None, edges: list[dict] | None = None) -> dict[str, Any]:
        if self._s.kg_stub:
            return {"ok": True, "stub": True}
        r = await self._http.post("/upsert", json={"nodes": nodes or [], "edges": edges or []})
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._http.aclose()

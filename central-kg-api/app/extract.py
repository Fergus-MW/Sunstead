from __future__ import annotations

import json
import logging
import re
from typing import Any, get_args

from .config import get_settings
from .models import EdgeType, NodeType

logger = logging.getLogger(__name__)

# Build the allowed-type lists from the single source of truth (models.py) so the
# extraction prompt can never drift from the schema the frontend/seed also mirror.
_NODE_TYPES = ", ".join(get_args(NodeType))
_EDGE_TYPES = ", ".join(get_args(EdgeType))

SYSTEM_PROMPT = (
    "You are an extraction engine for a real-time agent knowledge graph.\n"
    'Read the SOURCE text and return STRICT JSON with two arrays: "nodes" and "edges".\n\n'
    f"Allowed node types: {_NODE_TYPES}.\n\n"
    f"Allowed edge types: {_EDGE_TYPES}.\n\n"
    "Rules:\n"
    '- Each node has: {"type": <allowed>, "name": <short canonical name>, "properties": {...}}.\n'
    '- Each edge has: {"source": {"type":..,"name":..}, "target": {"type":..,"name":..}, '
    '"type": <allowed>, "properties": {...}}.\n'
    "- Use short, canonical names (no surrounding quotes, no markdown).\n"
    "- Prefer fewer high-signal nodes/edges over many low-signal ones.\n"
    "- Output JSON ONLY. No prose, no code fences.\n"
)


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    return s


async def extract_graph(text: str, hints: list[str] | None = None) -> dict[str, Any]:
    """Call Claude to extract {nodes, edges} from raw text.

    Returns {"nodes": [...], "edges": [...]} (possibly empty if no key / parse fail).
    """
    settings = get_settings()
    if not settings.anthropic_api_key or not text.strip():
        return {"nodes": [], "edges": []}

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    hint_str = ("\nHints: " + "; ".join(hints)) if hints else ""
    user = f"SOURCE:\n{text[:12000]}{hint_str}"

    try:
        msg = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.max_extract_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("anthropic extract failed: %s", e)
        return {"nodes": [], "edges": []}

    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    raw = _strip_json_fence(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            logger.warning("extract: could not parse JSON: %s", raw[:200])
            return {"nodes": [], "edges": []}
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"nodes": [], "edges": []}

    nodes = parsed.get("nodes") or []
    edges = parsed.get("edges") or []
    # Sanitise
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") and n.get("name")]
    edges = [
        e
        for e in edges
        if isinstance(e, dict)
        and e.get("type")
        and isinstance(e.get("source"), dict)
        and isinstance(e.get("target"), dict)
    ]
    return {"nodes": nodes, "edges": edges}

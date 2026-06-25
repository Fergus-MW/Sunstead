"""Shared KG-write helpers — idempotent node/edge upserts via Aiven MCP (`aiven_pg_write`).

The agent suite's flywheel: agents write their *conclusions* back into the same graph the
agents read, so the KB grows from the meeting itself (meeting-ops persists outcomes; research
persists findings). The SQL-literal escaping, slug keying, and best-effort exec live here so
every write path stays **injection-safe** and **idempotent** — never duplicate this in an agent.

Keying rule (docs/DESIGN.md §6): *episodes* (occurrence-identified — `action_item`, `decision`,
`research_finding`) are named `{meeting_id}::{slug(text)}` so a re-run within a meeting collapses
onto the same node via `ON CONFLICT (type, lower(name))`, while the same text in another meeting
stays a distinct node. Writes are **best-effort** — a hiccup is logged as activity, never raised,
so the agent's answer still stands (the suite's MCP data mandate, AGENT_SYSTEM §3).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import KG_DB, KG_PROJECT, KG_SERVICE
from .schema import is_episode


def lit(s: Any) -> str:
    """A safe single-quoted SQL string literal (LLM text → SQL; double the quotes)."""
    return "'" + str(s).replace("'", "''") + "'"


def jsonb(d: dict) -> str:
    return f"CAST({lit(json.dumps(d))} AS jsonb)"


def slug(text: str) -> str:
    """Normalize free text → a stable, URL-ish slug (lowercase, alnum, dash-joined, ~80 chars).

    Used to build a deterministic node name `{meeting_id}::{slug}` so re-running an extraction
    within a meeting collapses onto the same node, while items in other meetings stay distinct."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "item"


# --- entity resolution (the #1 representation-quality lever, docs/DESIGN.md §6) --------
#
# An entity node is name-identified, so two spellings of the same real-world thing must
# resolve to ONE name or the graph forks into disconnected duplicates and every cross-domain
# join ("did the person who owns auth speak in this meeting?") silently misses. We do the cheap,
# high-yield 90%: deterministic normalisation + a curated alias map. (Embedding-assisted
# dedup-on-write is the deferred next tier — it needs embeddings.py wired, currently a no-op.)

# Curated alias map: lower(raw spelling) → canonical display name. The hand-authored seam for the
# handful of people who appear under several spellings (short name, handle, email). Intentionally
# small and explicit — add entries as duplicates are spotted; never auto-merge two *different*
# people. Curated entries take precedence over the generated map below.
PERSON_ALIASES: dict[str, str] = {}

# Generated alias map: written by `scripts/resolve_people.py`, which clusters the live `person`
# nodes (shared email / local-part / name-subset) and folds each cluster's variants onto one
# canonical display name. Ships beside this module so the agent-runner container picks it up; loaded
# best-effort so a missing/corrupt file never breaks a write. Curated PERSON_ALIASES overrides it.
_GENERATED_ALIASES_FILE = "people_aliases.json"


def _load_generated_aliases() -> dict[str, str]:
    from pathlib import Path
    try:
        p = Path(__file__).with_name(_GENERATED_ALIASES_FILE)
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        # accept {"aliases": {...}} (script output) or a bare {variant: canonical} map
        aliases = data.get("aliases", data) if isinstance(data, dict) else {}
        return {str(k).lower(): str(v) for k, v in aliases.items() if k and v}
    except Exception:
        return {}


# Resolved at import: generated as the base, curated overrides on top.
_PERSON_ALIASES: dict[str, str] = {**_load_generated_aliases(), **PERSON_ALIASES}


def canonicalize_entity(node_type: str, name: str) -> str:
    """Resolve an entity name to its canonical spelling: trim, collapse internal whitespace,
    strip wrapping quotes/trailing punctuation, drop a leading `@` handle marker — then, for a
    `person`, fold known aliases (generated ∪ curated). Idempotent: canon(canon(x)) == canon(x),
    because every alias value is itself stored canonical (the resolver guarantees it)."""
    n = re.sub(r"\s+", " ", str(name or "")).strip().strip("\"'").strip(" .,;:")
    if node_type == "person":
        n = n.lstrip("@").strip()
        return _PERSON_ALIASES.get(n.lower(), n)
    return n


def canonical_key(node_type: str, name: str, scope: str | None = None) -> str:
    """The stored `nodes.name` for an extracted (type, name): episodes are scope-keyed
    `{scope}::{slug(text)}` so the same text in another meeting/doc stays distinct (never
    name-merge an occurrence); entities are canonicalised so aliases collapse to one node.
    This is the ONE place keying strategy is decided — agents must not re-implement it."""
    if is_episode(node_type):
        s = slug(name)
        return f"{scope}::{s}" if scope else s
    return canonicalize_entity(node_type, name)


def insert_nodes(node_type: str, items: list[tuple[str, dict]]) -> str | None:
    """Build one multi-row idempotent node upsert. items = [(name, properties), ...].

    ON CONFLICT (type, lower(name)) JSONB-merges properties (never overwrites) and bumps
    updated_at — so re-runs are flat in shape and strictly additive in properties."""
    rows = []
    for name, props in items:
        name = (name or "").strip()[:200]
        if not name:
            continue
        rows.append(f"({lit(node_type)}, {lit(name)}, {jsonb(props)})")
    if not rows:
        return None
    return (
        "INSERT INTO nodes (type, name, properties) VALUES\n"
        + ",\n".join(rows)
        + "\nON CONFLICT (type, lower(name)) DO UPDATE SET "
        "properties = nodes.properties || EXCLUDED.properties, updated_at = now();"
    )


def insert_edge(s_type: str, s_name: str, t_type: str, t_name: str, etype: str, props: dict) -> str:
    """Build one idempotent edge upsert that resolves endpoints by (type, lower(name)).

    SELECT-from-nodes form so we don't need the freshly-minted uuids; ON CONFLICT keeps re-runs flat."""
    return (
        "INSERT INTO edges (source_node_id, target_node_id, type, properties) "
        f"SELECT s.id, t.id, {lit(etype)}, {jsonb(props)} FROM nodes s, nodes t "
        f"WHERE s.type = {lit(s_type)} AND lower(s.name) = lower({lit(s_name)}) "
        f"AND t.type = {lit(t_type)} AND lower(t.name) = lower({lit(t_name)}) "
        "ON CONFLICT (source_node_id, target_node_id, type) "
        "DO UPDATE SET properties = edges.properties || EXCLUDED.properties;"
    )


async def kg_exec(ctx, sql: str | None, reasoning: str, label: str) -> bool:
    """Run one best-effort write via `aiven_pg_write`; True on success. A write hiccup never
    fails the task — the agent's answer still stands (the suite's MCP data mandate, §3). `ctx`
    is any object exposing `.mcp.pg_write(...)` and `.activity(...)` (a harness TaskCtx)."""
    if not sql:
        return False
    try:
        await ctx.mcp.pg_write(
            sql, project=KG_PROJECT, service_name=KG_SERVICE, database=KG_DB, reasoning=reasoning,
        )
        return True
    except Exception as e:
        await ctx.activity("kg write skipped", f"{label}: {type(e).__name__}")
        return False

"""Canonical KG schema — the **single source of truth** for the node/edge vocabulary and
the entity/episode identity split that decides how every node is keyed (docs/DESIGN.md §6).

This is the vocabulary the universal ingestor (`shared/ingest.py`) extracts into and the
persister (`shared/kg_write.py`) writes. Two other places **mirror** this list and must be
kept in sync (change = PR + a LOG entry, per DESIGN §6): `central-kg-api/app/models.py`
(`NodeType`/`EdgeType`, consumed by the HTTP extractor) and the FE graph explorer
(`meet-joiner/src/app/graph/types.ts`, colours + edge labels).

**Why the split matters — it's the core quality lever.** A knowledge graph is only as good
as its identity model; conflate two identities and every downstream join silently degrades.

- **ENTITY** nodes are *stable* and *name-identified* — the same name IS the same thing, so
  `(type, lower(name))` merge-on-conflict is correct, and re-mentions should *accrete*
  properties onto one node. These are canonicalised on write (entity resolution — normalise +
  alias) so `Fergus` / `Fergus MW` / `fergus` collapse instead of forking the graph into
  three disconnected people.
- **EPISODE** nodes are *occurrence-identified* — "we'll ship Friday" said in two meetings is
  two distinct events, not one. They must **never** dedupe by bare name, so the persister keys
  them `{scope}::{slug(text)}` (scope = the meeting/doc they occurred in). This is the bug
  DESIGN §6 records: meeting-ops once wrote `action_item` under `(type, lower(name))` and the
  same text across meetings silently merged (data loss).
"""

from __future__ import annotations

# --- node vocabulary ------------------------------------------------------------------

# Stable, name-identified things. Merge-on-conflict by (type, lower(name)) is correct;
# canonicalised on write so aliases of the same real-world entity collapse to one node.
ENTITY_NODE_TYPES: tuple[str, ...] = (
    "person",          # a human — canonicalised (normalise + alias) so handles/short names merge
    "company",
    "product",
    "feature",
    "requirement",
    "user_story",
    "workflow",
    "task",
    "topic",           # a concept/subject the team reasons about
    "code_module",     # a code symbol/file — keyed "source_file:label" by the seed adapter
    "commit",          # keyed by sha (globally unique) — stable, so name-merge is safe
    "meeting",         # keyed by meeting_id (its name IS the id)
    "website",         # a deployed/served site (web-agent output)
    "policy",          # company knowledge (HR/finance/eng policy, …)
    "source_document", # a citable source — URL / Notion page / Drive doc (keyed by uri)
)

# Occurrence-identified records. NEVER name-merged — the persister scopes them
# `{scope}::{slug}` so the same text in another meeting/doc stays a distinct node.
EPISODE_NODE_TYPES: tuple[str, ...] = (
    "utterance",         # a line said in a meeting
    "action_item",       # something the meeting decided must be done
    "decision",          # something the meeting decided
    "research_finding",  # an answer the research agent synthesised from the live web
)

NODE_TYPES: tuple[str, ...] = ENTITY_NODE_TYPES + EPISODE_NODE_TYPES

# --- edge vocabulary ------------------------------------------------------------------

EDGE_TYPES: tuple[str, ...] = (
    # structural / code
    "depends_on",     # A imports/calls/uses B  (graphify collapses imports|calls|uses → this)
    "part_of",        # A is a method/member/component of B
    "derived_from",   # A was produced from B  (finding ← source, subclass ← base)
    "relates_to",     # weak catch-all association
    "implements",     # code implements a feature/requirement
    # work / ownership
    "owns",           # person owns an action_item / module
    "assigned_to",    # work assigned to a person
    "blocks",         # A blocks B
    "authored",       # person authored a commit
    "touches",        # commit touches a code_module
    # meeting / knowledge
    "in_meeting",     # an episode (utterance/decision/…) occurred in a meeting
    "said",           # person said an utterance
    "attended",       # person attended a meeting
    "mentions",       # source/utterance mentions an entity (the demo's load-bearing edge)
    "discussed_in",   # a topic/entity was discussed in a meeting
    "rationale_for",  # a rationale/decision is the reason for something
)

# --- identity helpers (used by the persister to pick a keying strategy) ---------------

_EPISODE = frozenset(EPISODE_NODE_TYPES)
_NODE = frozenset(NODE_TYPES)
_EDGE = frozenset(EDGE_TYPES)


def is_episode(node_type: str) -> bool:
    """True if a node of this type is occurrence-identified and must be scope-keyed
    (`{scope}::{slug}`) rather than name-merged. Unknown types default to entity
    (name-merge) — the conservative choice for an ad-hoc type from an integration."""
    return node_type in _EPISODE


def is_known_node_type(node_type: str) -> bool:
    return node_type in _NODE


def is_known_edge_type(edge_type: str) -> bool:
    return edge_type in _EDGE

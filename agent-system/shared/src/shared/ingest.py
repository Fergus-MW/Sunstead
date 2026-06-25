"""The universal ingestor — one good door from *any* source into the knowledge graph.

A great graph representation is the foundation every downstream answer rides on (DESIGN §7),
so ingestion is deliberately split into two layers, each with one job done well:

    text/structured + Source ──▶ extract() ──▶ {nodes, edges} ──▶ persist() ──▶ graph
                                 (smart)         canonical          (safe)

- **extract()** — the *smart* layer. Claude reads free text and emits typed nodes/edges via a
  **strict** structured-output tool whose `type` fields are `enum`-constrained to the canonical
  vocabulary (`shared/schema.py`) — so the model can't invent a node type the traversal can't
  follow, and there's no brittle JSON-fence parsing (cf. the old central-kg-api extractor).
- **persist()** — the *safe* layer (built on `shared/kg_write.py`). It applies the entity/episode
  keying ONCE and centrally (so no agent re-introduces the silent-merge bug), resolves entity
  aliases, writes injection-safe idempotent upserts, and auto-attaches provenance so every node
  traces back to the source it came from (feeds the grounding ethos — DESIGN §7).

`ingest()` = extract-then-persist, the convenience door for integrations (Notion, Drive). Agents
that already hold structured data (research findings, meeting outcomes) skip extraction and call
`persist()` directly with canonical node/edge dicts — same keying, same provenance, no duplication.

Everything is **best-effort** (the suite's MCP data mandate, AGENT_SYSTEM §3): a write hiccup is
logged as activity, never raised, so the agent's user-facing answer still stands.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kg_write import canonical_key, insert_edge, insert_nodes, kg_exec
from .schema import EDGE_TYPES, NODE_TYPES, is_episode, is_known_edge_type, is_known_node_type

# A node/edge dict is the lingua franca between the two layers:
#   node = {"type": str, "name": str, "properties": dict}
#   edge = {"source": (type, name), "target": (type, name), "type": str, "properties": dict}


@dataclass
class Source:
    """Where an ingest came from — drives both provenance and episode keying.

    `scope` is the occurrence boundary for episode keying: episodes (`action_item`, `decision`,
    `utterance`, `research_finding`) are stored `{scope}::{slug}` so the same text under a different
    scope stays a distinct node. For meeting work `scope` is the `meeting_id`; for an integration
    it's the document id. `uri`/`title` anchor the `source_document` provenance node.
    """

    kind: str                    # "research" | "meeting-ops" | "notion" | "gdrive" | "transcript" | "manual"
    scope: str                   # occurrence scope (meeting_id, doc id, …) for episode keying
    uri: str | None = None       # canonical locator → the source_document node's name
    title: str | None = None


# --- extract layer: strict, schema-constrained structured output ----------------------

EXTRACT_SYSTEM = """You are the knowledge-graph extraction engine for Sunstead. Read the SOURCE \
text and distil it into a small, high-signal property graph — the durable team memory that every \
downstream agent answer is grounded in, so precision matters more than coverage.

Rules:
- Emit only what the text actually supports. Never invent owners, links, or facts. Prefer a few \
high-confidence nodes/edges over many speculative ones.
- Use SHORT, CANONICAL names. The same real-world entity must get the SAME name everywhere (one \
person, one canonical spelling — not a short name here and a full name there), so nodes merge \
instead of forking. Put display/long forms in the summary, not the name.
- Pick the most specific allowed node/edge type. Connect entities with edges — an unlinked node \
is nearly worthless; the relationships are the point.
- For every edge, quote the verbatim span of SOURCE that supports it in `evidence`."""

# Strict structured output (GA on Opus 4.8, no beta header): `strict: True` is a sibling of
# input_schema; every object needs additionalProperties:False and lists every prop in `required`.
# The `enum`s pin types to the canonical vocabulary — the model literally cannot emit an
# out-of-schema type, which is the cheapest, hardest guarantee of representation quality we have.
EXTRACT_TOOL = {
    "name": "emit_graph",
    "description": "Emit the high-signal property graph extracted from the SOURCE text.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "nodes": {
                "type": "array",
                "description": "The entities and episodes worth remembering from the source.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "enum": list(NODE_TYPES)},
                        "name": {"type": "string", "description": "Short canonical name (no quotes/markdown)."},
                        "summary": {"type": "string", "description": "One-line description; '' if none."},
                    },
                    "required": ["type", "name", "summary"],
                },
            },
            "edges": {
                "type": "array",
                "description": "Relationships between the nodes (the load-bearing part).",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source_type": {"type": "string", "enum": list(NODE_TYPES)},
                        "source_name": {"type": "string"},
                        "target_type": {"type": "string", "enum": list(NODE_TYPES)},
                        "target_name": {"type": "string"},
                        "type": {"type": "string", "enum": list(EDGE_TYPES)},
                        "evidence": {"type": "string", "description": "Verbatim source span supporting this edge."},
                    },
                    "required": ["source_type", "source_name", "target_type", "target_name", "type", "evidence"],
                },
            },
        },
        "required": ["nodes", "edges"],
    },
}

_EXTRACT_MAX_CHARS = 14000  # cap source size sent to the model (cost/latency guard)


def _to_graph(tool_input: dict) -> dict:
    """Reshape the strict tool output into persist()'s node/edge dicts, dropping anything whose
    type fell outside the canonical vocab (belt-and-braces; the enum should already prevent it)
    and any edge missing an endpoint."""
    nodes = []
    for n in tool_input.get("nodes") or []:
        t, name = n.get("type"), (n.get("name") or "").strip()
        if not name or not is_known_node_type(t):
            continue
        summary = (n.get("summary") or "").strip()
        nodes.append({"type": t, "name": name, "properties": {"summary": summary} if summary else {}})
    edges = []
    for e in tool_input.get("edges") or []:
        et = e.get("type")
        s_n, t_n = (e.get("source_name") or "").strip(), (e.get("target_name") or "").strip()
        if not (s_n and t_n and is_known_edge_type(et)):
            continue
        if not (is_known_node_type(e.get("source_type")) and is_known_node_type(e.get("target_type"))):
            continue
        props = {"evidence": e["evidence"]} if (e.get("evidence") or "").strip() else {}
        edges.append({
            "source": (e["source_type"], s_n),
            "target": (e["target_type"], t_n),
            "type": et, "properties": props,
        })
    return {"nodes": nodes, "edges": edges}


async def extract(ctx, text: str, source: Source, hints: list[str] | None = None) -> dict:
    """Claude reads `text` and returns a canonical `{nodes, edges}` graph (possibly empty if no
    key / nothing extractable). Strict tool → no JSON-fence parsing, no out-of-vocab types."""
    if ctx.anthropic is None or not (text or "").strip():
        return {"nodes": [], "edges": []}
    hint_str = ("\n\nHINTS: " + "; ".join(hints)) if hints else ""
    user = f"SOURCE ({source.kind}{f' — {source.title}' if source.title else ''}):\n{text[:_EXTRACT_MAX_CHARS]}{hint_str}"
    resp = await ctx.anthropic.messages.create(
        model=ctx.settings.model_smart,
        max_tokens=2500,
        system=EXTRACT_SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "emit_graph"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_graph":
            return _to_graph(block.input if isinstance(block.input, dict) else {})
    return {"nodes": [], "edges": []}


# --- persist layer: one keying + provenance path for every write ----------------------


async def persist(ctx, source: Source, nodes: list[dict], edges: list[dict] | None = None) -> dict:
    """Write a canonical `{nodes, edges}` graph idempotently, with keying + provenance applied
    centrally. Returns {"nodes": int, "edges": int} actually written (best-effort). Steps:

    1. Resolve every node to its stored key (`canonical_key`: entities canonicalised, episodes
       scoped `{scope}::{slug}`) and upsert, one batched statement per type.
    2. Write the caller's explicit edges, rewriting endpoints to their stored keys.
    3. When the Source has a single `uri` (the integration case — one doc in), upsert a
       `source_document` anchor and auto-link every node to it (`mentions` for entities,
       `derived_from` for episodes) so every fact is traceable. Agents with many/no URL sources
       (research cites several; meeting-ops has none) leave `uri` unset and pass their own
       `source_document` nodes + edges explicitly — no synthetic anchor is invented for them.
    """
    edges = edges or []
    scope = source.scope
    n_written = e_written = 0

    # 1) nodes → stored keys, grouped by type for one upsert per type. Dedupe per (type, stored
    #    key), merging properties — two inputs that canonicalise to the same node (one owner owning
    #    two action items → the same `person`; two sources sharing a URL) must NOT appear twice in
    #    one `INSERT … ON CONFLICT` statement, or Postgres raises "cannot affect row a second time".
    keymap: dict[tuple[str, str], str] = {}
    by_type: dict[str, dict[str, dict]] = {}
    for nd in nodes:
        ntype, raw = nd["type"], nd.get("name", "")
        stored = canonical_key(ntype, raw, scope)
        keymap[(ntype, raw)] = stored
        props = {"source": source.kind, "scope": scope, **(nd.get("properties") or {})}
        slot = by_type.setdefault(ntype, {})
        slot[stored] = {**slot.get(stored, {}), **props}  # last write wins on a clash, never duplicate

    # The single-source provenance anchor (integration case only — see docstring).
    src_name = source.uri
    if src_name:
        src_props = {k: v for k, v in
                     {"source": source.kind, "scope": scope, "uri": source.uri, "title": source.title}.items()
                     if v is not None}
        slot = by_type.setdefault("source_document", {})
        slot[src_name] = {**slot.get(src_name, {}), **src_props}

    for ntype, items in by_type.items():
        rows = list(items.items())  # [(stored_name, props), …]
        if await kg_exec(ctx, insert_nodes(ntype, rows),
                         f"ingest upserting {len(rows)} {ntype} from {source.kind}", ntype):
            n_written += len(rows)

    # 2) explicit edges — rewrite endpoints to stored keys (canonical_key handles endpoints that
    #    weren't in the node list, e.g. an edge to an already-existing meeting node).
    for ed in edges:
        s_t, s_n = ed["source"]
        t_t, t_n = ed["target"]
        s_stored = keymap.get((s_t, s_n)) or canonical_key(s_t, s_n, scope)
        t_stored = keymap.get((t_t, t_n)) or canonical_key(t_t, t_n, scope)
        if await kg_exec(ctx, insert_edge(s_t, s_stored, t_t, t_stored, ed["type"], ed.get("properties") or {}),
                         f"ingest edge {ed['type']} ({source.kind})", ed["type"]):
            e_written += 1

    # 3) auto-provenance edges — only when there's a single source anchor to trace back to.
    if src_name:
        prov = {"source": source.kind}
        for nd in nodes:
            ntype = nd["type"]
            stored = keymap[(ntype, nd.get("name", ""))]
            if ntype == "source_document" and stored == src_name:
                continue  # don't link the anchor to itself
            sql = (insert_edge(ntype, stored, "source_document", src_name, "derived_from", prov)
                   if is_episode(ntype)
                   else insert_edge("source_document", src_name, ntype, stored, "mentions", prov))
            if await kg_exec(ctx, sql, f"ingest provenance for {ntype}", "provenance"):
                e_written += 1

    return {"nodes": n_written, "edges": e_written}


async def ingest(ctx, source: Source, *, text: str | None = None,
                 nodes: list[dict] | None = None, edges: list[dict] | None = None,
                 hints: list[str] | None = None) -> dict:
    """The one door. Pass `text` to extract-then-persist (integrations), or pre-built canonical
    `nodes`/`edges` to persist directly (agents that already hold structured data). Best-effort;
    returns {"nodes": int, "edges": int}."""
    if nodes is None and text is not None:
        graph = await extract(ctx, text, source, hints)
        nodes, edges = graph["nodes"], graph["edges"]
    return await persist(ctx, source, nodes or [], edges or [])

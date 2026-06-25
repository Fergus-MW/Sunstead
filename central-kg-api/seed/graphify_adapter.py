"""Map a graphify `graph.json` payload into our (type, name, properties) schema.

graphify writes ~7k nodes / ~29k edges for a medium repo. We don't try to be
clever — keep the original `id`, `community`, `source_file`, `source_location`,
`confidence`, etc. in `properties` so they're queryable later, and map the
file_type / relation strings into our vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# graphify file_type → our node type. Anything unmapped falls through to "topic".
NODE_TYPE_FOR_FILE_TYPE: dict[str, str] = {
    "code": "code_module",
    "document": "source_document",
    "concept": "topic",
    "rationale": "decision",
    # image / video / paper / table / spreadsheet → source_document
    "image": "source_document",
    "video": "source_document",
    "paper": "source_document",
    "table": "source_document",
    "spreadsheet": "source_document",
}

# graphify relation → our edges.type. Anything unmapped falls through to "relates_to".
EDGE_TYPE_FOR_RELATION: dict[str, str] = {
    "imports": "imports",
    "imports_from": "imports",
    "calls": "calls",
    "method": "part_of",
    "contains": "part_of",
    "inherits": "derived_from",
    "uses": "relates_to",
    "references": "relates_to",
    "re_exports": "re_exports",
    "rationale_for": "rationale_for",
    "conceptually_related_to": "relates_to",
}


@dataclass
class NodeRow:
    graphify_id: str
    type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class EdgeRow:
    src_graphify_id: str
    dst_graphify_id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0


def map_node(n: dict[str, Any]) -> NodeRow:
    file_type = (n.get("file_type") or "").lower()
    explicit_type = n.get("type")  # graphify rarely sets this; respect it when present
    our_type = explicit_type or NODE_TYPE_FOR_FILE_TYPE.get(file_type, "topic")

    # Canonical name: code nodes use "<source_file>:<label>" so functions
    # share neither file-only nor label-only collisions — the same `__init__`
    # method in two different classes stays distinct, and so does the same
    # function name appearing in two files. The bare human label is kept in
    # properties.label for display. Non-code nodes fall back to label / id.
    label = n.get("label") or n.get("norm_label") or n.get("id")
    source_file = n.get("source_file")
    if file_type == "code" and source_file and label:
        name = f"{source_file}:{label}"
    else:
        name = label or n.get("id") or "(unnamed)"

    props: dict[str, Any] = {
        "graphify_id": n["id"],
        "graphify_file_type": file_type or None,
        "label": label if name != label else None,
        "source_file": n.get("source_file"),
        "source_location": n.get("source_location"),
        "community": n.get("community"),
        "origin": n.get("_origin"),
    }
    # Optional fields that graphify only sets on some nodes:
    for k in ("source_url", "captured_at", "author", "contributor", "ecosystem", "version", "metadata"):
        if n.get(k) is not None:
            props[k] = n[k]

    # Strip None values to keep the JSONB tidy.
    props = {k: v for k, v in props.items() if v is not None}

    return NodeRow(graphify_id=n["id"], type=our_type, name=name, properties=props)


def map_edge(e: dict[str, Any]) -> EdgeRow:
    relation = (e.get("relation") or e.get("type") or "").lower()
    our_type = EDGE_TYPE_FOR_RELATION.get(relation, "relates_to")
    props: dict[str, Any] = {
        "graphify_relation": relation or None,
        "context": e.get("context"),
        "confidence": e.get("confidence"),
        "confidence_score": e.get("confidence_score"),
        "source_file": e.get("source_file"),
        "source_location": e.get("source_location"),
    }
    props = {k: v for k, v in props.items() if v is not None}
    return EdgeRow(
        src_graphify_id=e["source"],
        dst_graphify_id=e["target"],
        type=our_type,
        properties=props,
        weight=float(e.get("weight") or 1.0),
    )

"""Unit tests for the graphify→KG adapter.

Locks in: the composite-name fix that avoids __init__.py collisions across
directories, and the relation→edge-type mapping that the live-retrieval
benchmark relies on.
"""

from __future__ import annotations

import pytest

from seed.graphify_adapter import (
    EDGE_TYPE_FOR_RELATION,
    NODE_TYPE_FOR_FILE_TYPE,
    map_edge,
    map_node,
)


def test_code_node_uses_composite_name():
    """Two __init__.py files in different directories must NOT collide
    on (type, lower(name))."""
    a = map_node({"id": "a", "file_type": "code", "label": "__init__", "source_file": "src/foo/__init__.py"})
    b = map_node({"id": "b", "file_type": "code", "label": "__init__", "source_file": "src/bar/__init__.py"})
    assert a.name != b.name
    assert a.name == "src/foo/__init__.py:__init__"
    assert b.name == "src/bar/__init__.py:__init__"


def test_non_code_node_falls_back_to_label():
    n = map_node({"id": "x", "file_type": "concept", "label": "RetryPolicy"})
    assert n.type == "topic"
    assert n.name == "RetryPolicy"


def test_file_type_to_node_type_mapping_is_complete():
    expected = {"code", "document", "concept", "rationale", "image", "video", "paper", "table", "spreadsheet"}
    assert expected.issubset(set(NODE_TYPE_FOR_FILE_TYPE.keys()))


@pytest.mark.parametrize(
    "graphify_relation,expected",
    [
        ("imports",          "imports"),
        ("imports_from",     "imports"),
        ("calls",            "calls"),
        ("method",           "part_of"),
        ("contains",         "part_of"),
        ("inherits",         "derived_from"),
        ("uses",             "relates_to"),
        ("references",      "relates_to"),
        ("re_exports",       "re_exports"),
        ("rationale_for",    "rationale_for"),
    ],
)
def test_edge_relation_mapping(graphify_relation, expected):
    assert EDGE_TYPE_FOR_RELATION[graphify_relation] == expected


def test_unknown_relation_defaults_to_relates_to():
    e = map_edge({"source": "a", "target": "b", "relation": "totally_made_up"})
    assert e.type == "relates_to"


def test_edge_properties_preserved():
    e = map_edge(
        {
            "source": "a",
            "target": "b",
            "relation": "calls",
            "context": "function-body",
            "confidence": "EXTRACTED",
            "confidence_score": 0.95,
            "source_file": "x.py",
            "source_location": "L42",
            "weight": 2.0,
        }
    )
    assert e.type == "calls"
    assert e.weight == 2.0
    assert e.properties["graphify_relation"] == "calls"
    assert e.properties["confidence_score"] == 0.95

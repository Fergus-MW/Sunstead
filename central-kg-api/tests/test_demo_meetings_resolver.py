"""Unit tests for the meeting-mention resolver.

These pin down the small set of choices that decide whether the mentions
edge for a standup utterance lands on the right node — getting this wrong
is what makes the live demo retrieve the wrong context.
"""

from __future__ import annotations

import pytest

from seed.demo_meetings import _build_code_index, _resolve_mentions


def make_code_nodes() -> list[dict]:
    """A tiny synthetic graph that includes both distinctive and noisy
    symbols, mimicking what we get out of graphify on a real codebase."""
    return [
        {"id": "n_stream", "name": "src/anthropic/_streaming.py:Stream", "label": "Stream"},
        {"id": "n_async_stream", "name": "src/anthropic/_streaming.py:AsyncStream", "label": "AsyncStream"},
        {"id": "n_streaming_file", "name": "src/anthropic/_streaming.py", "label": "_streaming.py"},
        {"id": "n_memory", "name": "src/anthropic/tools/memory.py", "label": "memory.py"},
        {"id": "n_session", "name": "src/anthropic/.../beta_session_runner.py", "label": "beta_session_runner"},
        {"id": "n_init1", "name": "src/anthropic/__init__.py:.__init__()", "label": "__init__"},
        {"id": "n_init2", "name": "src/anthropic/lib/__init__.py:.__init__()", "label": "__init__"},
        {"id": "n_init3", "name": "src/anthropic/types/__init__.py:.__init__()", "label": "__init__"},
        {"id": "n_self_ref", "name": "src/anthropic/_models.py:.self", "label": "self"},
    ]


PERSONS = {
    "robert craigie": "p_robert",
    "felix becker": "p_felix",
    "packy gallagher": "p_packy",
}


def test_stopword_drops_self_and_init():
    """`__init__` and `self` show up in every Python file — they must not
    fire as mentions even when explicitly typed in dialogue."""
    idx = _build_code_index(make_code_nodes())
    # The synthetic graph has three __init__ nodes; the dedup-by-distinctiveness
    # should leave them at low score and dropped by the threshold.
    code, _ = _resolve_mentions("we called self.__init__ in the test", idx, PERSONS)
    assert "n_init1" not in code
    assert "n_init2" not in code
    assert "n_init3" not in code
    assert "n_self_ref" not in code


def test_distinctive_symbol_lands():
    idx = _build_code_index(make_code_nodes())
    code, _ = _resolve_mentions(
        "The Stream class in _streaming.py has been accumulating retry logic",
        idx,
        PERSONS,
    )
    assert "n_stream" in code           # exact "Stream" hit
    assert "n_streaming_file" in code   # "_streaming" hit
    # Importantly, AsyncStream should NOT match here just because Stream did —
    # we use word boundaries so substring drift is avoided.
    assert "n_async_stream" not in code


def test_async_stream_only_when_named():
    idx = _build_code_index(make_code_nodes())
    code, _ = _resolve_mentions("AsyncStream should join the policy", idx, PERSONS)
    assert "n_async_stream" in code
    assert "n_stream" not in code


def test_person_full_name_required():
    """`Felix Becker` should match; the bare first name `Felix` should not
    (we don't want every utterance containing "Felix" elsewhere to fire)."""
    idx = _build_code_index(make_code_nodes())
    _, p = _resolve_mentions("Felix says he'll review", idx, PERSONS)
    assert "p_felix" not in p
    _, p2 = _resolve_mentions("Felix Becker says he'll review", idx, PERSONS)
    assert "p_felix" in p2


def test_session_runner_lands():
    """Should fire on the readable, distinctive part — not noise."""
    idx = _build_code_index(make_code_nodes())
    code, _ = _resolve_mentions(
        "beta_session_runner depends on a Stainless codegen change", idx, PERSONS
    )
    assert "n_session" in code


@pytest.mark.parametrize(
    "utterance,expected_present",
    [
        ("memory.py needs a parent dir mkdir", "n_memory"),
        ("we want to extract retry logic out of Stream", "n_stream"),
    ],
)
def test_smoke(utterance, expected_present):
    idx = _build_code_index(make_code_nodes())
    code, _ = _resolve_mentions(utterance, idx, PERSONS)
    assert expected_present in code

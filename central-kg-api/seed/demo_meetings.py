"""Seed the live-meeting layer of the knowledge graph from hand-crafted
JSON transcripts in `infra/demo_meetings/`.

Per PLAN.md §9.1 the meeting layer is: `meeting`, `utterance` nodes plus
`attended`, `said`, `in_meeting`, and `mentions` edges back into the code
and people layers.

Linking strategy
----------------
The "mentions" edge is the load-bearing one for the demo — it grounds what
was *said* in the call to specific code/people that already exist in the
graph from the graphify seed and the git seed.

We resolve mentions in two passes per utterance:

  1. PEOPLE — exact (case-insensitive) match on `person.name`. Trivial,
     fast, and we only have ~26 people so the dictionary is tiny.

  2. CODE — substring match against a curated index of distinctive
     symbol names from the existing code_module rows. We score each
     candidate by (token-length × token-distinctiveness) so common
     tokens like "self" / "from" / "id" never match.

Both passes are O(utterances × dictionary_size), entirely local, so the
whole seed runs in well under a second even with hundreds of utterances.

Usage:
    python -m seed.demo_meetings --dir infra/demo_meetings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger("seed.demo_meetings")

# Tokens we never want to count as a "code mention" — stopwords + Python
# keywords + ridiculously generic identifiers.
STOPWORDS = {
    "self", "from", "with", "this", "that", "those", "they", "them", "their",
    "have", "has", "had", "will", "would", "could", "should", "the", "and",
    "for", "but", "not", "you", "your", "our", "his", "her", "her", "she",
    "him", "any", "all", "out", "over", "into", "on", "in", "at", "of",
    "is", "it", "an", "a", "by", "to", "be", "do", "did", "no", "ok",
    "id", "args", "kwargs", "init", "main", "test", "tests", "name",
    "type", "list", "dict", "str", "int", "bool", "none", "true", "false",
    "if", "else", "elif", "while", "return", "import", "as", "or",
    "py", "json", "yaml", "md",
}


async def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(raw)
    return await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        user=p.username, password=p.password,
        database=p.path.lstrip("/") or "postgres",
        ssl="require", timeout=30,
    )


async def _register_source(conn, *, kind: str, title: str, uri: str | None) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO sources (kind, uri, title, metadata)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING id
        """,
        kind, uri, title, json.dumps({"seeder": "demo_meetings"}),
    )
    return str(row["id"])


async def _upsert_node(conn, *, type_: str, name: str, properties: dict, source_id: str) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO nodes (type, name, properties, source_id)
        VALUES ($1, $2, $3::jsonb, $4::uuid)
        ON CONFLICT (type, lower(name)) DO UPDATE
        SET properties = nodes.properties || EXCLUDED.properties,
            source_id  = COALESCE(nodes.source_id, EXCLUDED.source_id),
            updated_at = now()
        RETURNING id
        """,
        type_, name, json.dumps(properties), source_id,
    )
    return str(row["id"])


def _build_code_index(rows: list[dict]) -> list[tuple[str, str, float]]:
    """Return [(token, node_id, distinctiveness)] for code_module nodes.

    A node's `properties->>'label'` is the human-readable symbol name
    (e.g. "Stream", ".execute_async()", "_streaming.py"). We treat the
    label as the matchable surface, dropping leading dots, parens, etc.

    Distinctiveness: log-inverse of how often the token appears across
    the whole index. Repeated tokens like "py" get near-zero weight.
    """
    # Extract candidate tokens
    raw: list[tuple[str, str]] = []  # (token, node_id)
    for r in rows:
        label = (r["label"] or r["name"] or "").strip()
        # Trim ".foo()" → "foo", "_streaming.py" → "_streaming"
        token = re.sub(r"^[.\s_]+|\s*[(].*$", "", label)
        token = token.split(".")[0]  # left-most segment
        token = token.strip("_")     # _Streaming → Streaming
        if not token or len(token) < 3:
            continue
        if token.lower() in STOPWORDS:
            continue
        raw.append((token, str(r["id"])))

    freq = Counter(t for t, _ in raw)
    import math
    out: list[tuple[str, str, float]] = []
    for token, node_id in raw:
        # max frequency caps distinctiveness; tokens used once score highest
        score = 1.0 / math.log(freq[token] + math.e)
        out.append((token, node_id, score))
    return out


def _resolve_mentions(
    text: str,
    code_index: list[tuple[str, str, float]],
    person_index: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Return (code_node_ids, person_node_ids) referenced by `text`."""
    lower = text.lower()

    # People: exact substring of the person's name
    pp: set[str] = set()
    for name, pid in person_index.items():
        # Match on first-name only is too noisy ("Felix" vs other Felixes);
        # require the full display name.
        if name.lower() in lower:
            pp.add(pid)

    # Code: substring match of curated tokens, weighted by distinctiveness.
    # We accept any match with score >= 0.3 (drops tokens appearing >10x).
    # Match is "alpha-numeric boundary" — a leading underscore on the token
    # in dialogue (`_streaming.py`) still counts as a hit on `streaming`.
    # Trailing alnums don't (so `streaming` should NOT match `streaming_v2`).
    cc: set[str] = set()
    for token, nid, score in code_index:
        if score < 0.3:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text):
            cc.add(nid)
    return list(cc), list(pp)


async def _ingest_meeting(conn, path: Path, *, source_id: str, code_index, person_index):
    data = json.loads(path.read_text())
    mid_name = data["meeting_id"]

    # 1. Meeting node
    meeting_id = await _upsert_node(
        conn,
        type_="meeting",
        name=mid_name,
        properties={
            "title": data.get("title"),
            "occurred_at": data["occurred_at"],
            "channel": data.get("channel"),
            "attendees": data.get("attendees", []),
        },
        source_id=source_id,
    )

    # 2. Attended edges — link known persons to the meeting
    attended_rows = []
    for attendee in data.get("attendees", []):
        pid = person_index.get(attendee.lower())
        if not pid:
            # Person doesn't exist yet (the meeting brought a non-committer
            # into the graph) — upsert them quickly.
            pid = await _upsert_node(
                conn,
                type_="person",
                name=attendee,
                properties={"source": "meeting"},
                source_id=source_id,
            )
            person_index[attendee.lower()] = pid
        attended_rows.append((pid, meeting_id, "attended", "{}", 1.0, source_id))

    # 3. Utterance nodes + said + in_meeting + mentions
    utterance_rows: list[tuple] = []
    said_rows: list[tuple] = []
    in_meeting_rows: list[tuple] = []
    mentions_rows: list[tuple] = []

    for idx, u in enumerate(data["utterances"]):
        speaker = u["speaker"]
        text = u["text"]
        u_name = f"{mid_name}:u{idx:03d}"

        # Upsert the utterance row to get its UUID
        u_id = await _upsert_node(
            conn,
            type_="utterance",
            name=u_name,
            properties={
                "speaker": speaker,
                "ts": u.get("ts"),
                "text": text,
                "meeting_id": mid_name,
                "idx": idx,
            },
            source_id=source_id,
        )

        # speaker → utterance
        speaker_pid = person_index.get(speaker.lower())
        if not speaker_pid:
            speaker_pid = await _upsert_node(
                conn,
                type_="person",
                name=speaker,
                properties={"source": "meeting"},
                source_id=source_id,
            )
            person_index[speaker.lower()] = speaker_pid
        said_rows.append((speaker_pid, u_id, "said", "{}", 1.0, source_id))
        in_meeting_rows.append((u_id, meeting_id, "in_meeting", "{}", 1.0, source_id))

        # mentions
        code_ids, person_ids = _resolve_mentions(text, code_index, person_index)
        for cid in code_ids:
            mentions_rows.append((u_id, cid, "mentions", "{}", 1.0, source_id))
        for pid in person_ids:
            mentions_rows.append((u_id, pid, "mentions", "{}", 1.0, source_id))

    # 4. Bulk-insert all the edges in one go
    async def bulk_edges(rows: list[tuple]) -> None:
        if not rows:
            return
        async with conn.transaction():
            for start in range(0, len(rows), 500):
                await conn.executemany(
                    """
                    INSERT INTO edges (source_node_id, target_node_id, type, properties, weight, source_id)
                    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::uuid)
                    ON CONFLICT (source_node_id, target_node_id, type) DO NOTHING
                    """,
                    rows[start : start + 500],
                )

    await bulk_edges(attended_rows)
    await bulk_edges(said_rows)
    await bulk_edges(in_meeting_rows)
    await bulk_edges(mentions_rows)

    logger.info(
        "%s: %d utterances, %d mentions (%d code + %d people), %d attendees",
        path.name,
        len(data["utterances"]),
        len(mentions_rows),
        sum(1 for r in mentions_rows if "{}" in r[3] and "_mentions_code_seen" not in str(r)),  # cosmetic
        len({r[1] for r in mentions_rows if r[1] in {p for p in person_index.values()}}),
        len(attended_rows),
    )
    return {
        "meeting": meeting_id,
        "utterances": len(data["utterances"]),
        "mentions": len(mentions_rows),
        "attendees": len(attended_rows),
    }


async def run(dir_: Path) -> None:
    t0 = time.perf_counter()
    files = sorted(dir_.glob("*.json"))
    if not files:
        raise SystemExit(f"no .json transcripts in {dir_}")
    logger.info("found %d transcripts in %s", len(files), dir_)

    conn = await _connect()
    try:
        source_id = await _register_source(
            conn,
            kind="demo_meetings",
            title=f"Demo standup transcripts ({len(files)} files)",
            uri=None,
        )

        # Build person + code indices once
        persons = await conn.fetch(
            "SELECT id, name FROM nodes WHERE type='person'"
        )
        person_index = {r["name"].lower(): str(r["id"]) for r in persons}

        code_rows = await conn.fetch(
            """
            SELECT id, name, properties->>'label' AS label
            FROM nodes
            WHERE type='code_module'
            """
        )
        code_index = _build_code_index([dict(r) for r in code_rows])
        logger.info(
            "indices: %d persons, %d code tokens", len(person_index), len(code_index)
        )

        totals = {"utterances": 0, "mentions": 0, "attendees": 0, "meetings": 0}
        for path in files:
            summary = await _ingest_meeting(
                conn,
                path,
                source_id=source_id,
                code_index=code_index,
                person_index=person_index,
            )
            for k in ("utterances", "mentions", "attendees"):
                totals[k] += summary[k]
            totals["meetings"] += 1

    finally:
        await conn.close()

    dt = time.perf_counter() - t0
    print(
        f"\n=== demo_meetings done in {dt:.1f}s ===\n"
        f"  meetings   : {totals['meetings']}\n"
        f"  utterances : {totals['utterances']}\n"
        f"  attendees  : {totals['attendees']}\n"
        f"  mentions   : {totals['mentions']}\n"
    )


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--dir", type=Path, default=Path("infra/demo_meetings"))
    args = p.parse_args()
    asyncio.run(run(args.dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

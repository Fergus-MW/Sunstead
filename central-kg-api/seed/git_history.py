"""Seed `commit` + `person` nodes from a repo's git log, linking each commit
to the `code_module` rows graphify already produced.

Run order matters:
    1. graphify .                                 (writes graph.json)
    2. python -m seed --graph-json ...            (loads code into nodes/edges)
    3. python -m seed.git_history --repo-path ... (this script — fills in
                                                   commit + person + touches)

The "touches" edge links a commit to **every code_module whose source_file
matches the file the commit changed**. That includes both file-level nodes
and the symbol-level nodes inside that file — broad, but the listener agent
filters by hop distance, so noise is bounded.

Usage:
    python -m seed.git_history \\
        --repo-path /tmp/seed-repo/anthropic-sdk-python \\
        --repo-name anthropics/anthropic-sdk-python \\
        --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger("seed.git_history")

LOG_FORMAT = "%x1e%H%x1f%ae%x1f%an%x1f%aI%x1f%s"  # leading RS = record start


def _git_log(repo_path: Path, limit: int) -> list[dict[str, Any]]:
    """Return commits with their changed file lists. One subprocess call.

    Format inside each `\\x1e`-terminated record:
        SHA \\x1f email \\x1f name \\x1f iso_ts \\x1f subject \\n
        file1 \\n
        file2 \\n
        ...
    """
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        f"-n{limit}",
        f"--pretty=format:{LOG_FORMAT}",
        "--name-only",
        "--no-merges",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    commits: list[dict[str, Any]] = []
    for chunk in out.split("\x1e"):
        # Leading newlines come from the file list of the *previous* commit;
        # strip both ends so we always start at the SHA line.
        chunk = chunk.strip("\n").strip()
        if not chunk or "\x1f" not in chunk:
            continue
        head, _, file_block = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 5:
            logger.warning("skipping malformed header: %r", head[:200])
            continue
        sha, email, name, iso_ts, subject = parts[0], parts[1], parts[2], parts[3], "\x1f".join(parts[4:])
        files = [f for f in (file_block or "").split("\n") if f.strip()]
        commits.append(
            {
                "sha": sha,
                "author_email": email.strip().lower(),
                "author_name": name.strip(),
                "occurred_at": iso_ts,
                "subject": subject,
                "files": files,
            }
        )
    return commits


async def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(raw)
    return await asyncpg.connect(
        host=p.hostname,
        port=p.port or 5432,
        user=p.username,
        password=p.password,
        database=p.path.lstrip("/") or "postgres",
        ssl="require",
        timeout=30,
    )


async def _register_source(conn, *, repo_name: str, repo_url: str | None) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO sources (kind, uri, title, metadata)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING id
        """,
        "git_history",
        repo_url,
        f"git log: {repo_name}",
        json.dumps({"seeder": "git_history", "repo_name": repo_name}),
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
        type_,
        name,
        json.dumps(properties),
        source_id,
    )
    return str(row["id"])


async def _upsert_edge(
    conn,
    *,
    src: str,
    dst: str,
    type_: str,
    properties: dict | None = None,
    weight: float = 1.0,
    source_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO edges (source_node_id, target_node_id, type, properties, weight, source_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::uuid)
        ON CONFLICT (source_node_id, target_node_id, type) DO UPDATE
        SET properties = edges.properties || EXCLUDED.properties,
            weight     = GREATEST(edges.weight, EXCLUDED.weight)
        """,
        src,
        dst,
        type_,
        json.dumps(properties or {}),
        weight,
        source_id,
    )


async def run(repo_path: Path, repo_name: str, repo_url: str | None, limit: int) -> None:
    t0 = time.perf_counter()
    commits = _git_log(repo_path, limit)
    logger.info("git log: %d commits across %d unique authors", len(commits), len({c["author_email"] for c in commits}))

    conn = await _connect()
    try:
        source_id = await _register_source(conn, repo_name=repo_name, repo_url=repo_url)

        # 1. Cache: file path -> [code_module node ids that share that source_file]
        rows = await conn.fetch(
            """
            SELECT id, properties->>'source_file' AS source_file
            FROM nodes
            WHERE type = 'code_module' AND properties->>'source_file' IS NOT NULL
            """
        )
        path_to_ids: dict[str, list[str]] = {}
        for r in rows:
            path_to_ids.setdefault(r["source_file"], []).append(str(r["id"]))
        logger.info("indexed %d source paths covering %d code_module nodes", len(path_to_ids), len(rows))

        # 1. Upsert all persons first — one row per unique email — and
        #    capture their UUIDs into a cache for the edge phase.
        unique_authors: dict[str, str] = {}  # email -> display name
        for c in commits:
            unique_authors.setdefault(c["author_email"], c["author_name"] or c["author_email"])
        logger.info("upserting %d persons", len(unique_authors))
        person_cache: dict[str, str] = {}
        async with conn.transaction():
            for email, display in unique_authors.items():
                pid = await _upsert_node(
                    conn,
                    type_="person",
                    name=display,
                    properties={"email": email},
                    source_id=source_id,
                )
                person_cache[email] = pid

        # 2. Upsert all commits. We need their UUIDs back to attach edges, so
        #    we run a single batched RETURNING query rather than executemany.
        logger.info("upserting %d commits", len(commits))
        commit_id_cache: dict[str, str] = {}  # sha -> uuid
        BATCH = 200
        async with conn.transaction():
            for start in range(0, len(commits), BATCH):
                chunk = commits[start : start + BATCH]
                # Use UNNEST + INSERT … SELECT for one round-trip per chunk.
                rows = await conn.fetch(
                    """
                    INSERT INTO nodes (type, name, properties, source_id)
                    SELECT 'commit', t.name, t.props::jsonb, $1::uuid
                    FROM UNNEST($2::text[], $3::text[]) AS t(name, props)
                    ON CONFLICT (type, lower(name)) DO UPDATE
                    SET properties = nodes.properties || EXCLUDED.properties,
                        source_id  = COALESCE(nodes.source_id, EXCLUDED.source_id),
                        updated_at = now()
                    RETURNING id, name
                    """,
                    source_id,
                    [c["sha"][:12] for c in chunk],
                    [
                        json.dumps(
                            {
                                "sha": c["sha"],
                                "subject": c["subject"],
                                "occurred_at": c["occurred_at"],
                                "author_email": c["author_email"],
                            }
                        )
                        for c in chunk
                    ],
                )
                for r in rows:
                    commit_id_cache[r["name"]] = str(r["id"])
                logger.info("commits upserted: %d/%d", min(start + BATCH, len(commits)), len(commits))

        # 3. Authored edges — one tuple per commit, single executemany.
        logger.info("upserting %d authored edges", len(commits))
        authored_rows = [
            (
                person_cache[c["author_email"]],
                commit_id_cache[c["sha"][:12]],
                "authored",
                "{}",
                1.0,
                source_id,
            )
            for c in commits
        ]
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO edges (source_node_id, target_node_id, type, properties, weight, source_id)
                VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::uuid)
                ON CONFLICT (source_node_id, target_node_id, type) DO NOTHING
                """,
                authored_rows,
            )

        # 4. Touches edges — every (commit, code_module) where the commit
        #    changed a file path that a code_module sits under. Built
        #    in-memory, then bulk-inserted in chunks.
        touches_rows: list[tuple] = []
        for c in commits:
            commit_id = commit_id_cache[c["sha"][:12]]
            for f in c["files"]:
                for node_id in path_to_ids.get(f, ()):
                    touches_rows.append(
                        (commit_id, node_id, "touches", "{}", 1.0, source_id)
                    )
        logger.info("upserting %d touches edges in batches of 1000", len(touches_rows))
        async with conn.transaction():
            for start in range(0, len(touches_rows), 1000):
                await conn.executemany(
                    """
                    INSERT INTO edges (source_node_id, target_node_id, type, properties, weight, source_id)
                    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::uuid)
                    ON CONFLICT (source_node_id, target_node_id, type) DO NOTHING
                    """,
                    touches_rows[start : start + 1000],
                )
                logger.info(
                    "touches upserted: %d/%d",
                    min(start + 1000, len(touches_rows)),
                    len(touches_rows),
                )

        # final counts
        n_commits = await conn.fetchval(
            "SELECT count(*) FROM nodes WHERE type='commit' AND source_id=$1::uuid", source_id
        )
        n_persons = await conn.fetchval(
            "SELECT count(*) FROM nodes WHERE type='person' AND source_id=$1::uuid", source_id
        )
        n_touches = await conn.fetchval(
            "SELECT count(*) FROM edges WHERE type='touches' AND source_id=$1::uuid", source_id
        )
        n_authored = await conn.fetchval(
            "SELECT count(*) FROM edges WHERE type='authored' AND source_id=$1::uuid", source_id
        )
    finally:
        await conn.close()

    dt = time.perf_counter() - t0
    print(
        f"\n=== git_history done in {dt:.1f}s ===\n"
        f"  source_id : {source_id}\n"
        f"  commits   : {n_commits}\n"
        f"  persons   : {n_persons}\n"
        f"  authored  : {n_authored}\n"
        f"  touches   : {n_touches}\n"
    )


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Seed commit + person nodes from a repo's git log")
    p.add_argument("--repo-path", type=Path, required=True)
    p.add_argument("--repo-name", required=True, help="e.g. anthropics/anthropic-sdk-python")
    p.add_argument("--repo-url", default=None)
    p.add_argument("--limit", type=int, default=500)
    args = p.parse_args()
    if not (args.repo_path / ".git").exists():
        raise SystemExit(f"{args.repo_path} is not a git repo")
    asyncio.run(run(args.repo_path, args.repo_name, args.repo_url, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

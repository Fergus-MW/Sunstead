"""Entity resolution for `person` nodes — the #1 representation-quality lever (docs/DESIGN.md §6).

The git seed creates **one `person` node per unique commit email** (`seed/git_history.py`), and
meeting-ops/research add more keyed on whatever name was spoken — so the *same human* fragments
across several disconnected nodes, and the headline cross-domain join ("who last touched auth, and
did they speak in this meeting?") silently misses. This script reads the live `person` nodes,
clusters the ones that are the same person using cheap, conservative deterministic signals, and:

  • FORWARD (always, safe): writes a generated alias map → `shared/people_aliases.json`, which
    `shared.kg_write.canonicalize_entity` folds in, so EVERY future write (meeting owners, research,
    integrations) lands on the canonical git identity going forward.
  • BACK-FILL (opt-in `--apply-merge`, destructive): re-points existing edges from the duplicate
    nodes onto the canonical node, enriches the canonical with all emails/aliases, and deletes the
    dups — so the *current* seeded graph unifies too, not just future writes. Dry-run by default.

Clustering is deliberately conservative — the one unforgivable error is merging two DIFFERENT
people, so ambiguous matches are logged and left alone, never merged.

    uv run python scripts/resolve_people.py                 # forward map + dry-run merge plan
    uv run python scripts/resolve_people.py --apply-merge   # also execute the back-fill merge
    uv run python scripts/resolve_people.py --no-aliases     # only show the plan, don't write json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from shared import config
from shared.kg_write import jsonb, lit
from shared.mcp import AivenMCP

ALIASES_PATH = Path(__file__).resolve().parents[1] / "shared" / "src" / "shared" / "people_aliases.json"

# Local-parts that are roles/automation, not a person — never cluster two nodes just because they
# share one of these (e.g. two humans both pushing via "noreply@github").
GENERIC_LOCALPARTS = {
    "noreply", "no-reply", "github", "git", "actions", "action", "ci", "cd", "bot", "robot",
    "admin", "root", "dev", "build", "builds", "users", "user", "info", "support", "hello",
    "mail", "email", "team", "me",
}


# --- pure clustering (no I/O — unit-testable) ------------------------------------------

def _norm_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).strip("\"'").strip(" .,;:")


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]


def _localpart(email: str | None) -> str | None:
    """The 'namey' local-part of an email (before @, sans +tag), or None if absent/generic."""
    if not email or "@" not in email:
        return None
    lp = re.sub(r"\+.*$", "", email.split("@", 1)[0].lower()).strip(".")
    return lp if (len(lp) >= 3 and lp not in GENERIC_LOCALPARTS) else None


def _looks_like_email(name: str) -> bool:
    return "@" in name


def _pick_canonical(names: list[str]) -> str:
    """The best display name for a cluster: prefer a real multi-token name over a single token over
    an email-ish string; tie-break by length (longer = more complete)."""
    def rank(n: str) -> tuple:
        return (0 if _looks_like_email(n) else 1, len(_tokens(n)), len(n))
    return max(names, key=rank)


def cluster_people(persons: list[dict]) -> tuple[list[dict], list[dict]]:
    """Cluster same-human `person` rows. persons: [{"id","name","email"}]. Returns
    (clusters, ambiguous). Each cluster: {canonical, canonical_id, member_ids, names, emails, dups}.
    `ambiguous` are name-only nodes that plausibly matched >1 cluster and were left alone.

    Signals (union-find), strongest first — all conservative:
      1. identical email                      (definitely same person)
      2. identical 'namey' email local-part   (same person across two email domains)
      3. identical normalized full name
    then a post-pass attaches a *singleton* whose name is a strict token-subset of exactly ONE
    cluster's canonical (e.g. spoken "Jiaqi" → seeded "Jiaqi Chen"); >1 candidate ⇒ ambiguous."""
    n = len(persons)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    by_email: dict[str, int] = {}
    by_localpart: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for i, p in enumerate(persons):
        email = (p.get("email") or "").strip().lower()
        name = _norm_name(p.get("name")).lower()
        if email:
            by_email.setdefault(email, i) if email not in by_email else union(i, by_email[email])
            lp = _localpart(email)
            if lp:
                by_localpart.setdefault(lp, i) if lp not in by_localpart else union(i, by_localpart[lp])
        if name and not _looks_like_email(name):
            by_name.setdefault(name, i) if name not in by_name else union(i, by_name[name])

    # provisional clusters → canonical names, for the subset post-pass
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    def canon_of(members: list[int]) -> str:
        return _pick_canonical([_norm_name(persons[m].get("name")) for m in members])

    canon_tokens = {root: set(_tokens(canon_of(members))) for root, members in groups.items()}

    ambiguous: list[dict] = []
    for i in range(n):
        if len(groups[find(i)]) > 1:
            continue  # already clustered
        toks = set(_tokens(_norm_name(persons[i].get("name"))))
        if not toks:
            continue
        cands = [root for root, ct in canon_tokens.items()
                 if root != find(i) and toks < ct]  # strict subset of a *different* cluster's canon
        if len(cands) == 1:
            union(i, cands[0])
        elif len(cands) > 1:
            ambiguous.append({"id": persons[i].get("id"), "name": persons[i].get("name"),
                              "candidates": [canon_of(groups[c]) for c in cands]})

    # final clusters
    final: dict[int, list[int]] = {}
    for i in range(n):
        final.setdefault(find(i), []).append(i)

    clusters: list[dict] = []
    for members in final.values():
        names = list(dict.fromkeys(_norm_name(persons[m].get("name")) for m in members if _norm_name(persons[m].get("name"))))
        emails = list(dict.fromkeys((persons[m].get("email") or "").strip() for m in members if (persons[m].get("email") or "").strip()))
        canonical = _pick_canonical(names) if names else (emails[0] if emails else "")
        canonical_id = next((persons[m].get("id") for m in members if _norm_name(persons[m].get("name")) == canonical), None)
        dups = [{"id": persons[m].get("id"), "name": _norm_name(persons[m].get("name"))}
                for m in members if persons[m].get("id") != canonical_id]
        clusters.append({"canonical": canonical, "canonical_id": canonical_id,
                         "member_ids": [persons[m].get("id") for m in members],
                         "names": names, "emails": emails, "dups": dups})
    return clusters, ambiguous


def build_alias_map(clusters: list[dict]) -> dict[str, str]:
    """Flatten clusters → {lower(variant): canonical} for every variant that differs from canonical.
    Idempotency: canonical never maps to itself, so canon(canon(x)) == canon(x)."""
    aliases: dict[str, str] = {}
    for c in clusters:
        canon = c["canonical"]
        for name in c["names"]:
            if name and name.lower() != canon.lower():
                aliases[name.lower()] = canon
    return aliases


# --- back-fill merge SQL (one statement list per cluster) -----------------------------

def merge_statements(cluster: dict) -> list[str]:
    """SQL to fold a cluster's duplicate nodes into its canonical node. Per dup: re-point edges that
    wouldn't collide, drop the leftover colliding/dangling edges, then delete the dup node. Finally
    enrich the canonical with all emails + alternate names. Endpoints are uuid literals, so `lit()`
    keeps them injection-safe. Returns [] when the cluster has nothing to merge."""
    cid = cluster.get("canonical_id")
    dups = [d for d in cluster.get("dups", []) if d.get("id")]
    if not cid or not dups:
        return []
    stmts: list[str] = []
    for d in dups:
        did = d["id"]
        # re-point edges onto the canonical only where no equivalent edge already exists…
        stmts.append(
            f"UPDATE edges e SET source_node_id = {lit(cid)} WHERE e.source_node_id = {lit(did)} "
            f"AND NOT EXISTS (SELECT 1 FROM edges x WHERE x.source_node_id = {lit(cid)} "
            f"AND x.target_node_id = e.target_node_id AND x.type = e.type);"
        )
        stmts.append(
            f"UPDATE edges e SET target_node_id = {lit(cid)} WHERE e.target_node_id = {lit(did)} "
            f"AND NOT EXISTS (SELECT 1 FROM edges x WHERE x.target_node_id = {lit(cid)} "
            f"AND x.source_node_id = e.source_node_id AND x.type = e.type);"
        )
        # …then drop anything still hanging off the dup (would-be duplicates or self-edges)…
        stmts.append(f"DELETE FROM edges WHERE source_node_id = {lit(did)} OR target_node_id = {lit(did)};")
        # …and remove the dup node.
        stmts.append(f"DELETE FROM nodes WHERE id = {lit(did)};")
    # enrich the canonical with provenance of the merge (idempotent jsonb merge).
    enrich = {"emails": cluster.get("emails", []),
              "aka": [n for n in cluster.get("names", []) if n.lower() != cluster["canonical"].lower()],
              "merged_from": len(dups)}
    stmts.append(f"UPDATE nodes SET properties = properties || {jsonb(enrich)}, updated_at = now() "
                 f"WHERE id = {lit(cid)};")
    return stmts


# --- I/O driver -----------------------------------------------------------------------

async def _read_persons(mcp: AivenMCP) -> list[dict]:
    raw = await mcp.call_tool("aiven_pg_read", {
        "project": config.KG_PROJECT, "service_name": config.KG_SERVICE, "database": config.KG_DB,
        "query": "SELECT id::text AS id, name, properties->>'email' AS email FROM nodes WHERE type = 'person'",
        "reasoning": "entity resolution: read person nodes to cluster duplicates",
    })
    text = "".join(getattr(c, "text", "") for c in (raw.content or []) if getattr(c, "type", None) == "text")
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise SystemExit(f"could not parse person rows from pg_read: {text[:160]!r}")
    return json.loads(text[i:j + 1]).get("rows") or []


async def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve duplicate person nodes in the KG.")
    ap.add_argument("--apply-merge", action="store_true",
                    help="execute the destructive back-fill merge (default: dry-run plan only)")
    ap.add_argument("--no-aliases", action="store_true",
                    help="don't write the generated people_aliases.json (just show the plan)")
    args = ap.parse_args()

    mcp = await AivenMCP(config.load().mcp).start()
    try:
        persons = await _read_persons(mcp)
        print(f"read {len(persons)} person node(s)")
        clusters, ambiguous = cluster_people(persons)
        merged = [c for c in clusters if c["dups"]]
        aliases = build_alias_map(clusters)

        print(f"→ {len(clusters)} cluster(s); {len(merged)} with duplicates; "
              f"{len(aliases)} alias(es); {len(ambiguous)} ambiguous (left alone)")
        for c in merged:
            print(f"  • {c['canonical']!r}  ⇐  {[d['name'] for d in c['dups']]}  emails={c['emails']}")
        for a in ambiguous:
            print(f"  ? {a['name']!r} could be any of {a['candidates']} — NOT merged (review manually)")

        # FORWARD half (safe): write the generated alias map.
        if not args.no_aliases:
            ALIASES_PATH.write_text(json.dumps(
                {"generated_by": "scripts/resolve_people.py", "person_count": len(persons),
                 "aliases": aliases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {len(aliases)} alias(es) → {ALIASES_PATH}")

        # BACK-FILL half (opt-in): re-point edges + delete dup nodes.
        all_stmts = [s for c in merged for s in merge_statements(c)]
        if not all_stmts:
            print("no back-fill needed (no duplicate nodes to merge).")
        elif not args.apply_merge:
            print(f"\n[dry-run] back-fill would run {len(all_stmts)} statement(s). "
                  f"Re-run with --apply-merge to execute. Plan:")
            for s in all_stmts:
                print("   " + s)
        else:
            print(f"\napplying back-fill merge: {len(all_stmts)} statement(s) …")
            for s in all_stmts:
                await mcp.call_tool("aiven_pg_write", {
                    "project": config.KG_PROJECT, "service_name": config.KG_SERVICE, "database": config.KG_DB,
                    "query": s, "reasoning": "entity resolution: fold duplicate person nodes into canonical",
                })
            print(f"merged {sum(len(c['dups']) for c in merged)} duplicate node(s) into "
                  f"{len(merged)} canonical person(s).")
    finally:
        await mcp.stop()


if __name__ == "__main__":
    asyncio.run(main())

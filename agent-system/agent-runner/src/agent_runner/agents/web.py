"""web-agent — generate or UPDATE a one-file site with Claude and publish it to a served dir.

Self-contained demo deployer: no Vercel token needed. A site lives at a **stable id** (the
`workspace_id`, falling back to the task id) under `SITES_DIR/<site_id>/index.html`, served by
`scripts/serve_sites.py` on `SITES_BASE_URL`. Because the id is stable, `update_website` reopens
the existing HTML, asks the model to apply the change, and rewrites the SAME path — the URL never
changes across revisions. When `update_website` arrives without a `workspace_id`, it targets the
most-recently-built site (so "make the header bigger" right after a build just works).

Publish target: when ``VERCEL_TOKEN`` is set, every publish deploys ALL sites to one Vercel
project (see ``vercel_deploy.py``) and the per-site URL becomes the stable Vercel link; the Vercel
ids land in ``meta.json`` so the edit path reuses them. With no token it falls back to the local
served dir. See docs/AGENT_SYSTEM.md §5, §9.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg, now_iso
from shared.ingest import Source, persist
from shared.streaming import stream_completion

from .vercel_deploy import deploy_sites

BUILD_TIMEOUT_S = 150  # whole-build ceiling incl. any web-search round-trips (a build streams long)
# In-build grounding stays CHEAP regardless of the task's effort tier — a site verifies a few key
# facts, it isn't a research deliverable, so a "deep" build mustn't balloon into 8 searches + a
# minute of latency. Depth (thinking effort) still scales with the tier; only the search count is capped.
BUILD_GROUND_SEARCHES = 2
BUILD_GROUND_FETCHES = 1


async def _persist_website_node(ctx: TaskCtx, site_id: str, brief: str, url: str, meta: dict) -> None:
    """Write the published site into the KG as a `website` node (an entity, idempotent on its stable
    id) so the avatar/agents can recall "the site we built" and its live link — via the universal
    ingestor's `persist()`, the one write door. The `in_meeting` edge is passed without a meeting
    node, so it no-ops when the meeting node is absent (insert_edge resolves both endpoints from
    `nodes`). Best-effort: a missing/unconfigured MCP just skips it; the publish already succeeded."""
    vercel = meta.get("vercel") or {}
    props = {k: v for k, v in {
        "title": brief, "url": url,
        "revision": len(meta.get("revisions") or []),
        "host": vercel.get("host"), "deployment_id": vercel.get("deployment_id"),
        "updated": now_iso(),
    }.items() if v is not None}
    nodes = [{"type": "website", "name": site_id, "properties": props}]
    edges = ([{"source": ("website", site_id), "target": ("meeting", ctx.meeting_id),
               "type": "in_meeting", "properties": {}}] if ctx.meeting_id else [])
    await persist(ctx, Source(kind="web-agent", scope=ctx.meeting_id or site_id), nodes, edges)

# Concrete design contract — a vague "make it polished" prompt yields generic AI slop, so we pin
# the typography, rhythm, palette, and breakpoints the model must hit (and the clichés it must avoid).
BUILD_SYSTEM = """You are the web-agent for Sunstead. Produce a COMPLETE, self-contained \
single-file HTML document: inline <style> only (no external CSS/JS files; one Google Fonts <link> \
is fine).

Design contract — follow it exactly:
- Typography: pair a display serif with a clean sans via Google Fonts — use "Fraunces" for \
headings and "Sora" for body/UI. Do NOT default to Inter or Roboto.
- Spacing: define a base spacing unit (--space: 8px) and size all margins/padding/gaps as \
multiples of it; keep the rhythm consistent.
- Palette (DARK, product-matched): canvas #0b1a17, ink #f3ead3, aurora accent #4ade80. Derive any \
hovers/borders/surfaces from these — no off-palette colors.
- Responsive: mobile-first; add breakpoints (e.g. 640px / 1024px) so layout adapts from phone to \
desktop. Fluid, readable line lengths.
- Avoid generic AI aesthetics: not Inter/Roboto defaults, no purple-on-white gradient, no \
cookie-cutter centered-hero-with-two-buttons. Make it feel intentional and on-brand.

Grounding — do NOT hallucinate specifics: if the brief refers to something real you're unsure of \
(a product, company, person, event, price, statistic, date, quote), keep the copy general rather \
than inventing precise details. The OUTPUT is the site: return ONLY the HTML, starting with \
<!DOCTYPE html> — no commentary, no markdown fences."""

# update_website prefers surgical SEARCH/REPLACE edits over re-emitting the whole doc: cheaper,
# faster to stream, and it can't accidentally drop unrelated content. RARE sentinels so they never
# collide with real HTML. If parsing/matching fails we fall back to FULL_REWRITE_SYSTEM below.
UPDATE_SYSTEM = """You are the web-agent for Sunstead, EDITING an existing single-file site. \
Make the SMALLEST change that satisfies the request, preserving everything else.

Return one or more edit blocks in EXACTLY this format and nothing else:

<<<<<<< SEARCH
(an EXACT, verbatim substring of the current HTML — copy it character-for-character, including \
whitespace and indentation; long enough to be unique)
=======
(the replacement HTML)
>>>>>>> REPLACE

Each SEARCH must match the current HTML exactly or the edit is discarded. Emit as many blocks as \
the change needs. No markdown fences, no commentary outside the blocks."""

# Fallback when patching can't be applied — re-emit the entire document (the old behavior).
FULL_REWRITE_SYSTEM = """You are the web-agent for Sunstead, EDITING an existing single-file site. \
Apply the requested change to the current HTML, preserving everything else (structure, content, \
style) unless the change implies otherwise. Keep it a complete, self-contained, responsive \
single-file document. Return ONLY the full updated HTML, starting with <!DOCTYPE html> — no \
markdown fences, no commentary."""

# Sentinel lines that delimit a SEARCH/REPLACE block in an update response.
_SR_BLOCK = re.compile(
    r"<{7} SEARCH\s*?\n(.*?)\n={7}\s*?\n(.*?)\n>{7} REPLACE",
    re.DOTALL,
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _ensure_html(html: str) -> str:
    head = html[:200].lower()
    if "<!doctype" not in head and "<html" not in head:
        return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html}</body></html>"
    return html


def _extract_html(text: str) -> str:
    """Slice from the first <!doctype>/<html> if the model prefixed any preamble (more likely once
    web_search is in the loop — a stray "Here's the site:" before the document). If neither marker
    is present, return as-is and let _ensure_html wrap it."""
    low = text.lower()
    starts = [i for i in (low.find("<!doctype"), low.find("<html")) if i >= 0]
    return text[min(starts):].strip() if starts else text


def _latest_site(sites_dir: pathlib.Path) -> pathlib.Path | None:
    """The most-recently-modified built site dir (for update_website with no workspace_id)."""
    if not sites_dir.exists():
        return None
    built = [d for d in sites_dir.iterdir() if d.is_dir() and (d / "index.html").exists()]
    return max(built, key=lambda d: d.stat().st_mtime) if built else None


def _apply_search_replace(existing: str, response: str) -> str | None:
    """Apply SEARCH/REPLACE blocks to `existing`; return the patched HTML, or None to fall back.

    Returns None (signalling a full-rewrite fallback) if no blocks parse, or if ANY block's SEARCH
    text isn't found verbatim — we never apply a partial patch, since that would silently corrupt
    the site. Each match is replaced once (`.replace(old, new, 1)`) in document order.
    """
    blocks = _SR_BLOCK.findall(response)
    if not blocks:
        return None
    patched = existing
    for old, new in blocks:
        if old not in patched:
            return None
        patched = patched.replace(old, new, 1)
    return patched


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("web-agent requires ANTHROPIC_API_KEY")

    brief = first_arg(task.args, "brief", "text", default="a one-page landing site")
    style = first_arg(task.args, "style", default="clean, modern, dark")
    sites_dir = pathlib.Path(os.environ.get("SITES_DIR", "./.sites"))

    # Resolve the stable site id: explicit workspace_id wins; an update with none targets the
    # latest site; a build with none uses the task id (and returns it so a later update can reuse it).
    site_id = task.workspace_id
    if not site_id and task.intent == "update_website":
        latest = _latest_site(sites_dir)
        site_id = latest.name if latest else task.task_id
    if not site_id:
        site_id = task.task_id

    out = sites_dir / site_id
    index = out / "index.html"
    is_update = task.intent == "update_website" and index.exists()
    sources: list[dict] = []  # provenance of any facts the grounded build looked up (build path only)

    # Stream the build/edit: reasoning + HTML deltas flow to agent.trace as they generate, so the
    # dashboard can show the agent thinking and writing live (docs/AGENT_SYSTEM.md §3.5).
    if is_update:
        existing = index.read_text(encoding="utf-8")
        await ctx.activity("updating site", brief)

        # First try a surgical SEARCH/REPLACE patch; only re-emit the whole doc if that fails.
        patch_text = await stream_completion(
            ctx,
            model=ctx.settings.model_smart,
            max_tokens=16000,
            system=UPDATE_SYSTEM,
            messages=[{"role": "user", "content": f"Requested change: {brief}\n\nCurrent site HTML:\n{existing}"}],
        )
        patched = _apply_search_replace(existing, patch_text)
        if patched is not None:
            await ctx.activity("patched site", "applied SEARCH/REPLACE edit(s)")
            html = patched
        else:
            # No block parsed or a SEARCH didn't match exactly — re-request the full document.
            await ctx.activity("patch did not apply", "re-requesting full rewrite")
            rewrite = await stream_completion(
                ctx,
                model=ctx.settings.model_smart,
                max_tokens=16000,
                system=FULL_REWRITE_SYSTEM,
                messages=[{"role": "user", "content": f"Requested change: {brief}\n\nCurrent site HTML:\n{existing}"}],
            )
            html = _ensure_html(_strip_fences(rewrite))
    else:
        await ctx.activity("drafting site", brief)
        # Fast single-pass build: generate the whole site in one streamed completion. No web search —
        # builds must be snappy for live use, so we avoid hallucinated specifics via the prompt (keep
        # unknown facts general) rather than slow search round-trips.
        text = await stream_completion(
            ctx,
            model=ctx.settings.model_smart,
            max_tokens=16000,
            system=BUILD_SYSTEM,
            messages=[{"role": "user", "content": f"Build: {brief}\nStyle: {style}"}],
        )
        html = _ensure_html(_extract_html(_strip_fences(text)))

    # Truncation guard: a build or full-rewrite that ran out of tokens loses its closing tag. We
    # still publish (a half-page beats nothing) but flag it so the caller/dashboard can warn. A
    # patched update is exempt — it edits a known-good doc rather than emitting a whole one.
    truncated = "</html>" not in html.lower()
    if truncated:
        await ctx.activity("warning", "site may be truncated")

    await ctx.activity("publishing")
    out.mkdir(parents=True, exist_ok=True)
    index.write_text(html, encoding="utf-8")

    # Load/repair the revision record (durable workspace, §5). Tolerate a missing or corrupt
    # meta.json (partial write, manual edit): a broken record must not fail a good publish.
    meta_path = out / "meta.json"
    meta: dict = {"revisions": []}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("revisions"), list):
                meta = loaded
        except (json.JSONDecodeError, OSError):
            pass
    meta["revisions"].append({"intent": task.intent, "brief": brief, "task_id": task.task_id, "ts": now_iso()})
    meta["brief"] = brief
    if sources:  # remember what this build grounded itself in (provenance for the workspace)
        meta["grounded_sources"] = [s["url"] for s in sources][:8]
    revision = len(meta["revisions"])

    # Publish. The local preview URL is ALWAYS available (the served dir / `make sites`), so we
    # surface it regardless. When VERCEL_TOKEN is set we ALSO deploy all sites to the one Vercel
    # project and offer that public link. Deploy fails open — a Vercel error never fails an
    # otherwise-good build; we just fall back to the local link and record why. The Vercel ids/url
    # land in meta.json so a later update_website (the edit path) inherits the same project + link.
    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    local_url = f"{base}/{site_id}/"
    vercel_url = None
    if ctx.settings.vercel_token:
        try:
            await ctx.activity("deploying to Vercel", ctx.settings.vercel_project)
            dep = await deploy_sites(sites_dir, site_id, ctx.settings)
            vercel_url = dep["url"]
            meta["vercel"] = {
                "project": ctx.settings.vercel_project,
                "deployment_id": dep["deployment_id"],
                "host": dep["host"],
                "url": vercel_url,
            }
        except Exception as exc:  # noqa: BLE001 — fail open to local serve on any deploy error
            await ctx.activity("Vercel deploy failed", f"falling back to local serve: {exc}")

    # The headline link is the shareable Vercel URL when we have one, else the local preview. We
    # emit BOTH as clickable url artifacts (deployed first) so the dashboard can offer each.
    url = vercel_url or local_url
    artifacts = [{"kind": "url", "value": url}]
    if vercel_url and local_url != vercel_url:
        artifacts.append({"kind": "url", "value": local_url})

    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    await ctx.activity("published", url)
    await _persist_website_node(ctx, site_id, brief, url, meta)

    verb = "Updated" if is_update else "Built and published"
    result = {
        "summary": f"{verb} a site for: {brief}",
        "url": url,                          # shareable headline link (Vercel if deployed)
        "preview_url": local_url,            # always-on local preview (`make sites`, :8810)
        "workspace_id": site_id,             # pass this back into update_website to revise the same site
        "revision": revision,
        "bytes": len(html),
        "artifacts": artifacts,
    }
    if vercel_url:
        result["deployed_url"] = vercel_url  # public Vercel link (only when actually deployed)
    if truncated:
        result["truncated"] = True
    if sources:  # surface that the copy was grounded, and in what (the dashboard can show it)
        result["grounded"] = True
        result["sources"] = sources
    return result

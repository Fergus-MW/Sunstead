"""web-agent — generate or UPDATE a one-file site with Claude and publish it to a served dir.

Self-contained demo deployer: no Vercel token needed. A site lives at a **stable id** (the
`workspace_id`, falling back to the task id) under `SITES_DIR/<site_id>/index.html`, served by
`scripts/serve_sites.py` on `SITES_BASE_URL`. Because the id is stable, `update_website` reopens
the existing HTML, asks the model to apply the change, and rewrites the SAME path — the URL never
changes across revisions. When `update_website` arrives without a `workspace_id`, it targets the
most-recently-built site (so "make the header bigger" right after a build just works).

Swap in a real Vercel deploy by replacing the write+url step (docs/AGENT_SYSTEM.md §5, §9).
"""

from __future__ import annotations

import json
import os
import pathlib
import re

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx, first_arg, now_iso
from shared.streaming import stream_completion

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

Return ONLY the HTML, starting with <!DOCTYPE html> — no markdown fences, no commentary."""

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
        text = await stream_completion(
            ctx,
            model=ctx.settings.model_smart,
            max_tokens=16000,
            system=BUILD_SYSTEM,
            messages=[{"role": "user", "content": f"Build: {brief}\nStyle: {style}"}],
        )
        html = _ensure_html(_strip_fences(text))

    # Truncation guard: a build or full-rewrite that ran out of tokens loses its closing tag. We
    # still publish (a half-page beats nothing) but flag it so the caller/dashboard can warn. A
    # patched update is exempt — it edits a known-good doc rather than emitting a whole one.
    truncated = "</html>" not in html.lower()
    if truncated:
        await ctx.activity("warning", "site may be truncated")

    await ctx.activity("publishing")
    out.mkdir(parents=True, exist_ok=True)
    index.write_text(html, encoding="utf-8")

    # Persist a small revision record alongside the site (durable workspace, §5).
    # Tolerate a missing or corrupt meta.json (partial write, manual edit): a broken
    # record must not fail an otherwise-successful publish.
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
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    revision = len(meta["revisions"])

    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    url = f"{base}/{site_id}/"
    await ctx.activity("published", url)

    verb = "Updated" if is_update else "Built and published"
    result = {
        "summary": f"{verb} a site for: {brief}",
        "url": url,
        "workspace_id": site_id,    # pass this back into update_website to revise the same site
        "revision": revision,
        "bytes": len(html),
        "artifacts": [{"kind": "url", "value": url}],
    }
    if truncated:
        result["truncated"] = True
    return result

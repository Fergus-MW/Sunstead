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
from shared.harness import TaskCtx
from shared.streaming import stream_completion

BUILD_SYSTEM = """You are the web-agent for Sunstead. Produce a COMPLETE, self-contained \
single-file HTML document for the requested site: inline <style> (no external CSS/JS files; \
Google Fonts <link> is fine). Make it polished and responsive. Return ONLY the HTML, starting \
with <!DOCTYPE html> — no markdown fences, no commentary before or after."""

UPDATE_SYSTEM = """You are the web-agent for Sunstead, EDITING an existing single-file site. \
Apply the requested change to the current HTML, preserving everything else (structure, content, \
style) unless the change implies otherwise. Keep it a complete, self-contained, responsive \
single-file document. Return ONLY the full updated HTML, starting with <!DOCTYPE html> — no \
markdown fences, no commentary."""


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


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("web-agent requires ANTHROPIC_API_KEY")

    brief = task.args.get("brief") or task.args.get("text") or "a one-page landing site"
    style = task.args.get("style") or "clean, modern, dark"
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

    if is_update:
        existing = index.read_text(encoding="utf-8")
        await ctx.activity("updating site", brief)
        system = UPDATE_SYSTEM
        user = f"Requested change: {brief}\n\nCurrent site HTML:\n{existing}"
    else:
        await ctx.activity("drafting site", brief)
        system = BUILD_SYSTEM
        user = f"Build: {brief}\nStyle: {style}"

    # Stream the build/edit: reasoning + HTML deltas flow to agent.trace as they generate, so the
    # dashboard can show the agent thinking and writing live (docs/AGENT_SYSTEM.md §3.5).
    text = await stream_completion(
        ctx,
        model=ctx.settings.model_smart,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    html = _ensure_html(_strip_fences(text))

    await ctx.activity("publishing")
    out.mkdir(parents=True, exist_ok=True)
    index.write_text(html, encoding="utf-8")

    # Persist a small revision record alongside the site (durable workspace, §5).
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"revisions": []}
    meta["revisions"].append({"intent": task.intent, "brief": brief, "task_id": task.task_id, "ts": ctx.task_id})
    meta["brief"] = brief
    meta_path.write_text(json.dumps(meta, indent=2))
    revision = len(meta["revisions"])

    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    url = f"{base}/{site_id}/"
    await ctx.activity("published", url)

    verb = "Updated" if is_update else "Built and published"
    return {
        "summary": f"{verb} a site for: {brief}",
        "url": url,
        "workspace_id": site_id,    # pass this back into update_website to revise the same site
        "revision": revision,
        "bytes": len(html),
        "artifacts": [{"kind": "url", "value": url}],
    }

"""web-agent — generate a one-file site with Claude and publish it to a served dir.

Self-contained demo deployer: no Vercel token needed. The agent asks Claude for a
complete single-file HTML document, writes it to `SITES_DIR/<task_id>/index.html`,
and returns a URL artifact served by the local static server (`scripts/serve_sites.py`
on `SITES_BASE_URL`, default http://localhost:8810). Swap in a real Vercel deploy by
replacing the write+url step (docs/AGENT_SYSTEM.md §5, §9).
"""

from __future__ import annotations

import os
import pathlib
import re

from shared.contracts import TaskCreatePayload
from shared.harness import TaskCtx
from shared.streaming import stream_completion

WEB_SYSTEM = """You are the web-agent for Sunstead. Produce a COMPLETE, self-contained \
single-file HTML document for the requested site: inline <style> (no external CSS/JS files; \
Google Fonts <link> is fine). Make it polished and responsive. Return ONLY the HTML, starting \
with <!DOCTYPE html> — no markdown fences, no commentary before or after."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def run(task: TaskCreatePayload, ctx: TaskCtx) -> dict:
    if ctx.anthropic is None:
        raise RuntimeError("web-agent requires ANTHROPIC_API_KEY")

    brief = task.args.get("brief") or task.args.get("text") or "a one-page landing site"
    style = task.args.get("style") or "clean, modern, dark"
    await ctx.activity("drafting site", brief)

    # Stream the build: reasoning + HTML deltas flow to agent.trace as they generate, so the
    # dashboard can show the agent thinking and writing live (docs/AGENT_SYSTEM.md §3.5).
    text = await stream_completion(
        ctx,
        model=ctx.settings.model_smart,
        max_tokens=16000,
        system=WEB_SYSTEM,
        messages=[{"role": "user", "content": f"Build: {brief}\nStyle: {style}"}],
    )
    html = _strip_fences(text)
    head = html[:200].lower()
    if "<!doctype" not in head and "<html" not in head:
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html}</body></html>"

    await ctx.activity("publishing")
    sites_dir = pathlib.Path(os.environ.get("SITES_DIR", "./.sites"))
    out = sites_dir / task.task_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")

    base = os.environ.get("SITES_BASE_URL", "http://localhost:8810").rstrip("/")
    url = f"{base}/{task.task_id}/"
    await ctx.activity("published", url)

    return {
        "summary": f"Built and published a site for: {brief}",
        "url": url,
        "bytes": len(html),
        "artifacts": [{"kind": "url", "value": url}],
    }

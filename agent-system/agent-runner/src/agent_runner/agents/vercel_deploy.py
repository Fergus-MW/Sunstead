"""Deploy the generated sites to Vercel as ONE project holding MANY files.

Model: a single Vercel project (``VERCEL_PROJECT``) mirrors the whole ``SITES_DIR`` tree.
Every site lives under its own ``<site_id>/index.html`` path, so the project serves them all
at ``https://<project-host>/<site_id>/``. Because a Vercel deployment *replaces* the project's
entire filesystem, each deploy re-uploads **all** sites (read from local disk, which stays the
source of truth) — editing one site can't drop the others.

We deploy with ``target=production`` so the project's stable production host always points at the
latest deploy: the per-site URL never changes across edits. The resolved production host is cached
in ``SITES_DIR/.vercel.json`` so we don't re-resolve it every deploy.

Auth: a single ``VERCEL_TOKEN`` (Bearer). Scope the token to the right team and no team id is
needed; otherwise set ``VERCEL_TEAM_ID`` and it's passed as the ``teamId`` query param.
Docs: https://vercel.com/docs/rest-api/reference/endpoints/deployments/create-a-new-deployment
"""

from __future__ import annotations

import json
import pathlib

import httpx

API = "https://api.vercel.com"
_CACHE = ".vercel.json"  # under SITES_DIR: {"host": "<project>.vercel.app"}


def _gather_files(sites_dir: pathlib.Path) -> list[dict]:
    """Every built site's index.html as an inline Vercel file: {file, data}.

    Paths are ``<site_id>/index.html`` so Vercel serves each site at ``/<site_id>/``.
    """
    files: list[dict] = []
    if not sites_dir.exists():
        return files
    for d in sorted(sites_dir.iterdir()):
        index = d / "index.html"
        if d.is_dir() and index.exists():
            files.append({"file": f"{d.name}/index.html", "data": index.read_text(encoding="utf-8")})
    return files


def _params(settings) -> dict:
    # skipAutoDetectionConfirmation: never block on framework detection (these are static files).
    p = {"skipAutoDetectionConfirmation": "1"}
    if getattr(settings, "vercel_team_id", ""):
        p["teamId"] = settings.vercel_team_id
    return p


async def _ensure_public(client: httpx.AsyncClient, settings) -> None:
    """Turn OFF deployment protection so the sites are publicly reachable.

    Vercel Pro creates projects with Vercel Authentication (SSO) on by default, which 302-redirects
    visitors to a login page. Setting ssoProtection/passwordProtection to null makes the project
    public. Best-effort and idempotent — called once per project (when the host cache is absent).
    """
    try:
        await client.patch(
            f"{API}/v10/projects/{settings.vercel_project}",
            params=_params(settings),
            json={"ssoProtection": None, "passwordProtection": None},
        )
    except httpx.HTTPError:
        pass


def _pick_host(aliases: list[str], fallback: str | None) -> str | None:
    """The stable production host: the shortest alias ending in .vercel.app (the clean
    ``<project>.vercel.app`` beats the per-deploy ``<project>-<hash>.vercel.app``)."""
    clean = sorted((a for a in aliases if a.endswith(".vercel.app")), key=len)
    return clean[0] if clean else (aliases[0] if aliases else fallback)


async def _resolve_host(client: httpx.AsyncClient, settings, resp: dict, cache: pathlib.Path) -> str:
    """Resolve the project's stable production host, preferring (cache → deploy aliases →
    project lookup → per-deploy url) and cache the winner for next time."""
    if cache.exists():
        try:
            host = json.loads(cache.read_text(encoding="utf-8")).get("host")
            if host:
                return host
        except (json.JSONDecodeError, OSError):
            pass

    # First time we resolve this project: make it public (disable SSO/password protection).
    await _ensure_public(client, settings)

    host = _pick_host(resp.get("alias") or [], resp.get("url"))
    if not host:
        # Aliases not assigned synchronously yet — ask the project for its production alias.
        r = await client.get(f"{API}/v9/projects/{settings.vercel_project}", params=_params(settings))
        if r.is_success:
            prod = (r.json().get("targets") or {}).get("production") or {}
            host = _pick_host(prod.get("alias") or [], None)

    if host:
        try:
            cache.write_text(json.dumps({"host": host}), encoding="utf-8")
        except OSError:
            pass
    return host or (resp.get("url") or f"{settings.vercel_project}.vercel.app")


async def deploy_sites(sites_dir: pathlib.Path, site_id: str, settings) -> dict:
    """Deploy ALL sites to the one Vercel project; return {deployment_id, host, url} for ``site_id``.

    ``url`` is the stable per-site link: ``https://<host>/<site_id>/``. Raises on HTTP error so the
    caller can fall open to local serve.
    """
    files = _gather_files(sites_dir)
    body = {
        "name": settings.vercel_project,
        "files": files,
        "projectSettings": {"framework": None},  # static files, no build step
        "target": "production",
    }
    headers = {"Authorization": f"Bearer {settings.vercel_token}"}
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        r = await client.post(f"{API}/v13/deployments", params=_params(settings), json=body)
        r.raise_for_status()
        resp = r.json()
        host = await _resolve_host(client, settings, resp, sites_dir / _CACHE)
    return {
        "deployment_id": resp.get("id", ""),
        "host": host,
        "url": f"https://{host}/{site_id}/",
    }

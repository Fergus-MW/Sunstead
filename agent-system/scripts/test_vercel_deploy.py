"""Live smoke test for the Vercel publish path — no Kafka, no Anthropic needed.

Writes a throwaway site into SITES_DIR and deploys ALL sites to the one Vercel project, then
prints the stable per-site URL. Requires VERCEL_TOKEN (and optionally VERCEL_PROJECT /
VERCEL_TEAM_ID) in the environment or agent-system/.env.

    cd agent-system && python scripts/test_vercel_deploy.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent-runner" / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shared" / "src"))

from agent_runner.agents.vercel_deploy import deploy_sites  # noqa: E402
from shared import config  # noqa: E402


async def main() -> None:
    settings = config.load()
    if not settings.vercel_token:
        print("VERCEL_TOKEN not set — put it in agent-system/.env and retry.")
        sys.exit(1)

    sites_dir = pathlib.Path("./.sites")
    site_id = "smoke-test"
    (sites_dir / site_id).mkdir(parents=True, exist_ok=True)
    (sites_dir / site_id / "index.html").write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Sunstead smoke test</title></head>"
        "<body style='font-family:sans-serif;background:#0b1a17;color:#f3ead3'>"
        "<h1>Vercel deploy works ✓</h1><p>One project, many files.</p></body></html>",
        encoding="utf-8",
    )

    print(f"project={settings.vercel_project} team_id={settings.vercel_team_id or '(token-scoped)'}")
    print(f"deploying all sites under {sites_dir.resolve()} ...")
    dep = await deploy_sites(sites_dir, site_id, settings)
    print(f"deployment_id : {dep['deployment_id']}")
    print(f"production host: {dep['host']}")
    print(f"site URL      : {dep['url']}")


if __name__ == "__main__":
    asyncio.run(main())

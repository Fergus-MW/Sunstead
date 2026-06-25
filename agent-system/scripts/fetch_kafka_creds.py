"""Fetch the Aiven Kafka mTLS connection for the agent-runner — the one thing MCP can't do
(creds are redacted there). Uses the `avn` CLI (official creds path); the token is read from
config and passed to avn **without ever being printed**, and the certs land in files the
Kafka client reads (never echoed).

    uv tool install aiven-client        # provides `avn`
    uv run python scripts/fetch_kafka_creds.py

Writes ca.pem / service.cert / service.key into agent-system/ (gitignored) and prints the
non-secret bootstrap (host:port) + the KAFKA_* lines to drop into .env.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shared import config

AGENT_DIR = Path(__file__).resolve().parents[1]   # agent-system/
PROJECT, SERVICE, USER = "jq01", "kafka-254bd14f", "avnadmin"


def _avn() -> str:
    for cand in (shutil.which("avn"), Path.home() / ".local/bin/avn.exe", Path.home() / ".local/bin/avn"):
        if cand and Path(cand).exists():
            return str(cand)
    return "avn"


def main() -> None:
    token = config.load().mcp.aiven_token
    if not token:
        sys.exit("AIVEN_TOKEN not set")

    # scriptable auth: a private credentials file in a temp config dir (token never printed)
    cfgdir = AGENT_DIR / ".aivenconf"
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / "aiven-credentials.json").write_text(
        json.dumps({"auth_token": token, "user_email": "jchennq@gmail.com"})
    )
    env = {**os.environ, "AIVEN_CONFIG_DIR": str(cfgdir)}
    avn = _avn()

    try:
        got = subprocess.run(
            [avn, "service", "get", SERVICE, "--project", PROJECT, "--json"],
            env=env, capture_output=True, text=True,
        )
        if got.returncode != 0:
            print("avn service get FAILED:\n", got.stderr[-700:])
            sys.exit(1)
        svc = json.loads(got.stdout)
        uri = svc.get("service_uri")                       # host:port (not secret)
        nodes = svc.get("node_count") or 1
        rf = min(3, int(nodes)) if nodes else 1
        print(f"service state : {svc.get('state')}")
        print(f"node_count    : {nodes}  -> KAFKA_RF={rf}")
        print(f"bootstrap     : {uri}")

        dl = subprocess.run(
            [avn, "service", "user-creds-download", SERVICE,
             "--project", PROJECT, "-d", str(AGENT_DIR), "--username", USER],
            env=env, capture_output=True, text=True,
        )
        if dl.returncode != 0:
            print("creds-download FAILED:\n", dl.stderr[-700:])
            sys.exit(1)
        for f in ("ca.pem", "service.cert", "service.key"):
            p = AGENT_DIR / f
            print(f"  {f:14}: {'OK (' + str(p.stat().st_size) + ' bytes)' if p.exists() else 'MISSING'}")

        print("\n--- add these to agent-system/.env to use Aiven Kafka ---")
        print(f"KAFKA_BOOTSTRAP={uri}")
        print("KAFKA_SECURITY=SSL")
        print("KAFKA_CA_PATH=./ca.pem")
        print("KAFKA_CERT_PATH=./service.cert")
        print("KAFKA_KEY_PATH=./service.key")
        print(f"KAFKA_RF={rf}")
    finally:
        (cfgdir / "aiven-credentials.json").unlink(missing_ok=True)
        try:
            cfgdir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()

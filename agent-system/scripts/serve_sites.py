"""Static server for web-agent output (the deployed-URL demo, no Vercel needed).

Serves SITES_DIR (default ./.sites) on SITES_PORT (default 8810), so a site written to
SITES_DIR/<task_id>/index.html is reachable at http://localhost:8810/<task_id>/.

    uv run python scripts/serve_sites.py        (make sites)
"""

from __future__ import annotations

import functools
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITES_DIR = Path(os.environ.get("SITES_DIR", "./.sites")).resolve()
PORT = int(os.environ.get("SITES_PORT", "8810"))


def main() -> None:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(SITES_DIR))
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"serving {SITES_DIR} on http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()

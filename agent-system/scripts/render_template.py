#!/usr/bin/env python
"""Render a sitegen template to standalone HTML — the local review loop for templates.

    uv run python scripts/render_template.py                      # list templates
    uv run python scripts/render_template.py landing              # render with all defaults
    uv run python scripts/render_template.py landing -c cfg.json  # render with a config override
    uv run python scripts/render_template.py landing -o out.html  # write to a file (else stdout)

No network, no model — pure deterministic render, so it's the fastest way to eyeball a template
and its defaults while authoring. `--config` is the same JSON shape the web-agent's model will emit.
"""

from __future__ import annotations

import argparse
import json
import sys

from shared import sitegen


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a sitegen template to HTML.")
    ap.add_argument("template", nargs="?", help="template id (omit to list all)")
    ap.add_argument("-c", "--config", help="path to a JSON config file")
    ap.add_argument("-o", "--out", help="output HTML path (default: stdout)")
    ap.add_argument("--schema", action="store_true", help="print the template's field schema and exit")
    args = ap.parse_args()

    if not args.template:
        for spec in sitegen.catalog():
            print(f"{spec.id:12}  {spec.name}")
            print(f"              {spec.description}")
        return 0

    if args.schema:
        print(json.dumps(sitegen.get(args.template).public(), indent=2))
        return 0

    config = {}
    if args.config:
        config = json.loads(open(args.config, encoding="utf-8").read())

    html = sitegen.render(args.template, config)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(html)
        print(f"wrote {len(html):,} bytes -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""File/session store — a simple, inspectable folder tree (docs/AGENT_SYSTEM.md §5).

    sessions/<session_id>/
    ├─ meta.json
    ├─ workspace/      # web-agent persistent workspace
    ├─ artifacts/      # charts, screenshots, build logs, urls
    └─ tasks/<task_id>/
"""

from __future__ import annotations

import json
from pathlib import Path


class SessionStore:
    def __init__(self, root: str = "./.sessions"):
        self.root = Path(root)

    def session_dir(self, session_id: str) -> Path:
        d = self.root / session_id
        for sub in ("workspace", "artifacts", "tasks"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        meta = d / "meta.json"
        if not meta.exists():
            meta.write_text(json.dumps({"session_id": session_id}, indent=2))
        return d

    def workspace(self, session_id: str, workspace_id: str) -> Path:
        d = self.session_dir(session_id) / "workspace" / workspace_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def task_dir(self, session_id: str, task_id: str) -> Path:
        d = self.session_dir(session_id) / "tasks" / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_artifact(self, session_id: str, name: str, data: str | bytes) -> Path:
        p = self.session_dir(session_id) / "artifacts" / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return p

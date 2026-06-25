"""Make `src/` importable in tests without an editable install (which would pull
the heavy plugin deps — torch via silero, etc.). The agent only imports those
plugins lazily, so the tool tests stay light."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

"""Per-session runtime, stored as the AgentSession's userdata so `@function_tool`
handlers can reach the backend client and the tool-call log via `RunContext`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backend import BackendClient
from .observability import ToolCallLog


@dataclass
class AgentRuntime:
    backend: BackendClient
    tools_log: ToolCallLog

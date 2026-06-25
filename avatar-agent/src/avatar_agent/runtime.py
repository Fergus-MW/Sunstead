"""Per-session runtime, stored as the AgentSession's userdata so `@function_tool`
handlers can reach the backend client and the tool-call log via `RunContext`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backend import BackendClient
from .gateway import GatewayClient
from .observability import ToolCallLog


@dataclass
class AgentRuntime:
    backend: BackendClient
    tools_log: ToolCallLog
    # The delegation seam: present so the `delegate` tool can hand work to the
    # worker suite, and the canonical meeting key so results correlate on the FE.
    gateway: GatewayClient | None = None
    meeting_id: str = ""

"""Warm Aiven MCP session — the agent suite's only data-plane path (docs/AGENT_SYSTEM.md §3).

We run the Aiven MCP server **locally** over stdio (`npx -y mcp-aiven`) with a static
`AIVEN_TOKEN` — no hosted-server OAuth dance. The session is spawned **once** and reused
across tasks (warm; see §3.5).

Two ways to use it:
- `await mcp.call_tool("aiven_pg_read", {...})`  — programmatic (no LLM), for deterministic queries.
- `mcp.llm_tools()`                              — converted tools for the anthropic tool-runner,
                                                    when the agent must *decide* what to query.

`anthropic` + `mcp` are imported lazily so the local no-creds Kafka path doesn't need them.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any, Callable

from . import config


class _RecordingSession:
    """Transparent proxy around an MCP ClientSession that records each tool result.

    The tool-runner executes MCP tools via `session.call_tool(...)`; the rows those calls
    return are the *evidence* the grounding verifier (docs/DESIGN.md §7) checks an answer
    against — but they're produced inside the runner and otherwise discarded. Wrapping the
    session per task captures them without coupling to the runner's internals; everything
    other than `call_tool` delegates straight through.
    """

    def __init__(self, session: Any, on_result: Callable[[str, dict, str], None]):
        self._session = session
        self._on_result = on_result

    async def call_tool(self, name: str, arguments: dict | None = None, **kw: Any) -> Any:
        result = await self._session.call_tool(name, arguments=arguments, **kw)
        try:
            text = "".join(
                getattr(c, "text", "") for c in (result.content or [])
                if getattr(c, "type", None) == "text"
            )
            self._on_result(name, arguments or {}, text)
        except Exception:
            pass  # capture is best-effort — never break a tool call to record it
        return result

    def __getattr__(self, item: str) -> Any:
        return getattr(self._session, item)


class AivenMCP:
    def __init__(self, settings: config.McpSettings | None = None):
        self._s = settings or config.McpSettings()
        self._stack: AsyncExitStack | None = None
        self.session: Any = None
        self._tools: list[Any] = []

    async def start(self) -> "AivenMCP":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not self._s.configured:
            raise RuntimeError("AIVEN_TOKEN is not set — cannot start mcp-aiven")

        env = {
            **os.environ,
            "AIVEN_TOKEN": self._s.aiven_token,
            "AIVEN_SERVICES_SCOPE": self._s.services_scope,
            "AIVEN_READ_ONLY": "true" if self._s.read_only else "false",
            "AIVEN_ALLOW_SECRETS": "true" if self._s.allow_secrets else "false",
        }
        params = StdioServerParameters(command=self._s.cmd, args=self._s.args, env=env)

        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self._tools = (await self.session.list_tools()).tools
        return self

    async def stop(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None

    async def __aenter__(self) -> "AivenMCP":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # --- programmatic (no LLM) ---------------------------------------------

    async def call_tool(self, name: str, args: dict | None = None) -> str:
        """Call an MCP tool and return its text content joined."""
        result = await self.session.call_tool(name, arguments=args or {})
        parts = [getattr(c, "text", "") for c in (result.content or []) if getattr(c, "type", None) == "text"]
        return "".join(parts)

    async def pg_read(self, query: str, **extra: Any) -> str:
        return await self.call_tool("aiven_pg_read", {"query": query, **extra})

    async def pg_write(self, query: str, **extra: Any) -> str:
        return await self.call_tool("aiven_pg_write", {"query": query, **extra})

    async def kafka_produce(self, **args: Any) -> str:
        return await self.call_tool("aiven_kafka_topic_message_produce", args)

    # --- LLM-exposed (tool-runner) -----------------------------------------

    def llm_tools(self, on_result: Callable[[str, dict, str], None] | None = None) -> list[Any]:
        """Tools for the anthropic tool-runner. Pass `on_result` to capture each tool's
        result (name, args, text) per task — used to feed the grounding verifier."""
        from anthropic.lib.tools.mcp import async_mcp_tool
        session = self.session if on_result is None else _RecordingSession(self.session, on_result)
        return [async_mcp_tool(t, session) for t in self._tools]

    def tool_schemas(self) -> dict[str, Any]:
        """name -> input schema, for discovery (used by the connectivity spike)."""
        return {t.name: getattr(t, "inputSchema", None) for t in self._tools}

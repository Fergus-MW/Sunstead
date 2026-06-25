"""Tests for the tool layer: graceful backend-failure handling (CT-4), the
knowledge-graph summarizer, and idempotency-key wiring on writes.

The `@function_tool`-decorated callables are invoked through their underlying
function so we don't need a live LiveKit session — we hand them a stub
RunContext whose `.userdata` is a real AgentRuntime over a fake backend.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from avatar_agent import tools
from avatar_agent.backend import BackendError
from avatar_agent.observability import ToolCallLog
from avatar_agent.runtime import AgentRuntime


@dataclass
class _Ctx:
    """Minimal stand-in for livekit RunContext — tools only touch `.userdata`."""

    userdata: AgentRuntime


class _FakeBackend:
    def __init__(self) -> None:
        self.appended: list[dict] = []

    async def query(self, q):
        return {"nodes": [{"name": "Auth Service", "type": "code_module",
                           "properties": {"status": "green"}}]}

    async def entity(self, node_id, hops=1):
        raise BackendError("the backend timed out")

    async def timeline(self, since=None, limit=20):
        return {"events": [{"kind": "deploy", "body": "shipped v2"}]}

    async def append_event(self, *, node, kind, body, idempotency_key=None):
        self.appended.append(
            {"node": node, "kind": kind, "body": body, "key": idempotency_key}
        )
        return {"ok": True}


class _FakeGateway:
    """Stand-in for GatewayClient — records delegations, always 'enabled'."""

    enabled = True

    def __init__(self, fail: BackendError | None = None) -> None:
        self.fail = fail
        self.delegated: list[dict] = []

    async def delegate(self, *, intent, args, meeting_id, requested_by="avatar"):
        if self.fail is not None:
            raise self.fail
        record = {"intent": intent, "args": args, "meeting_id": meeting_id}
        self.delegated.append(record)
        return {"task_id": "tsk_abc123", "status": "accepted"}


def _call(fn):
    """A @function_tool object is directly callable and proxies to the function."""
    return fn


def _runtime(backend, gateway=None, meeting_id="abc-defg-hij") -> AgentRuntime:
    return AgentRuntime(
        backend=backend, tools_log=ToolCallLog(), gateway=gateway, meeting_id=meeting_id
    )


async def test_lookup_context_summarizes_nodes():
    rt = _runtime(_FakeBackend())
    out = await _call(tools.lookup_context)(_Ctx(rt), query="auth")
    assert "Auth Service" in out
    assert rt.tools_log.calls[-1].status == "ok"


async def test_tool_degrades_gracefully_on_backend_error():
    rt = _runtime(_FakeBackend())
    out = await _call(tools.get_entity)(_Ctx(rt), entity_id="x", hops=1)
    # CT-4: a sentence the avatar can speak, not an exception.
    assert "couldn't" in out.lower()
    assert rt.tools_log.calls[-1].status == "error"


async def test_recent_activity_lists_events():
    rt = _runtime(_FakeBackend())
    out = await _call(tools.recent_activity)(_Ctx(rt), since=None)
    assert "shipped v2" in out


async def test_write_uses_correlation_id_as_idempotency_key():
    backend = _FakeBackend()
    rt = _runtime(backend)
    out = await _call(tools.record_action_item)(
        _Ctx(rt), description="email the client", owner="Fergus"
    )
    assert "recorded" in out.lower()
    appended = backend.appended[-1]
    assert appended["key"]  # an idempotency key was sent
    assert appended["key"] == rt.tools_log.calls[-1].correlation_id
    assert "owner: Fergus" in appended["body"]


async def test_delegate_routes_brief_to_correct_arg_key():
    gw = _FakeGateway()
    rt = _runtime(_FakeBackend(), gateway=gw)
    out = await _call(tools.delegate)(
        _Ctx(rt), intent="build_website", brief="a dark landing page for Acme"
    )
    assert "on it" in out.lower()
    sent = gw.delegated[-1]
    # web-agent reads args.brief; the canonical meeting_id is forwarded for correlation.
    assert sent["args"] == {"brief": "a dark landing page for Acme"}
    assert sent["intent"] == "build_website"
    assert sent["meeting_id"] == "abc-defg-hij"
    assert rt.tools_log.calls[-1].status == "ok"


async def test_delegate_rejects_unknown_intent_without_calling_gateway():
    gw = _FakeGateway()
    rt = _runtime(_FakeBackend(), gateway=gw)
    out = await _call(tools.delegate)(_Ctx(rt), intent="order_pizza", brief="pepperoni")
    assert not gw.delegated  # never reached the gateway
    assert rt.tools_log.calls[-1].status == "error"
    assert "can't delegate" in out.lower()


async def test_delegate_degrades_when_gateway_unconfigured():
    rt = _runtime(_FakeBackend(), gateway=None)
    out = await _call(tools.delegate)(_Ctx(rt), intent="echo", brief="hi")
    assert "can't hand work" in out.lower()
    assert rt.tools_log.calls[-1].status == "error"


async def test_delegate_degrades_on_gateway_error():
    gw = _FakeGateway(fail=BackendError("the task gateway timed out"))
    rt = _runtime(_FakeBackend(), gateway=gw)
    out = await _call(tools.delegate)(_Ctx(rt), intent="read_git", brief="who owns auth")
    assert "couldn't hand that off" in out.lower()
    assert rt.tools_log.calls[-1].status == "error"


def test_summarizer_handles_empty():
    assert "didn't find" in tools._summarize_nodes({"nodes": []}).lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

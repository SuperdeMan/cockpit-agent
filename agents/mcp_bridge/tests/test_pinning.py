"""CA2-17 S3 V1：工具指纹在调用期仍然成立（复核时机、漂移即停用、写调用发出前拒）。"""
from __future__ import annotations

import asyncio
import copy
import logging

import pytest

from agents.mcp_bridge.src.admission import (REJECT_EGRESS, REJECT_URL, ServerSpec, ToolSpec,
                                             admit, check_egress, check_url, schema_fingerprint,
                                             tool_fingerprint)
from agents.mcp_bridge.src.mcp_client import McpError
from agents.mcp_bridge.src.pinning import (READ_MAX_AGE_S, WRITE_MAX_AGE_S, PinnedClient,
                                           ToolDrift)

READ = {"name": "query", "description": "查", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "outputSchema": {"type": "object"}}
WRITE = {"name": "create", "description": "建单", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}}}


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Inner:
    """假 MCP 客户端：tools/list 返回当前定义；记录调用。"""

    def __init__(self, tools):
        self.tools = copy.deepcopy(tools)
        self.healthy, self.alive = True, True
        self.server_info = {"version": "1.0.0"}
        self.tools_changed = False
        self.on_session_renewed = None
        self.lists = 0
        self.calls = []
        self.extra = "passthrough"

    async def list_tools(self):
        self.lists += 1
        return copy.deepcopy(self.tools)

    async def call_tool(self, name, arguments, timeout_s=None, *, retry_on_session_loss=True):
        self.calls.append(name)
        return {"ok": True, "text": "", "data": {}}


def _pinned(tools=(READ, WRITE)):
    inner = _Inner(list(tools))
    clock = _Clock()
    client = PinnedClient(inner, "merchant", {t["name"]: tool_fingerprint(t) for t in tools},
                          {"create"}, clock=clock)
    return client, inner, clock


def _run(coro):
    return asyncio.run(coro)


def test_fresh_calls_do_not_relist_and_pass_through():
    client, inner, _ = _pinned()
    _run(client.call_tool("query", {}))
    assert inner.lists == 0 and inner.calls == ["query"]
    assert client.healthy and client.alive and client.extra == "passthrough"
    assert inner.on_session_renewed is not None


def test_stale_read_reverifies_and_drift_disables_before_sending():
    client, inner, clock = _pinned()
    clock.now += READ_MAX_AGE_S + 1
    inner.tools[0]["inputSchema"]["properties"]["q"]["type"] = "integer"   # 合同变了
    with pytest.raises(ToolDrift):
        _run(client.call_tool("query", {}))
    assert inner.lists == 1 and inner.calls == []
    assert client.disabled == {"query": "fingerprint"}
    # 停用到重启：之后不再复核、直接拒
    with pytest.raises(ToolDrift):
        _run(client.call_tool("query", {}))
    assert inner.calls == []


def test_write_tools_are_reverified_after_a_minute():
    client, inner, clock = _pinned()
    clock.now += WRITE_MAX_AGE_S + 1
    _run(client.call_tool("query", {}))           # 读：一分钟远不到 30 分钟
    assert inner.lists == 0
    _run(client.call_tool("create", {}))          # 写：超过一分钟就复核
    assert inner.lists == 1 and inner.calls == ["query", "create"]


def test_list_changed_forces_reverification_and_missing_tool_is_disabled():
    client, inner, _ = _pinned()
    inner.tools_changed = True
    inner.tools = [READ]                          # create 没了
    with pytest.raises(ToolDrift):
        _run(client.call_tool("create", {}))
    assert inner.tools_changed is False
    assert client.disabled == {"create": "missing"}
    _run(client.call_tool("query", {}))           # 其余工具照常
    assert inner.calls == ["query"]


def test_description_changes_do_not_disable_but_output_schema_changes_do():
    client, inner, clock = _pinned()
    inner.tools[0]["description"] = "商户改了文档"
    clock.now += READ_MAX_AGE_S + 1
    _run(client.call_tool("query", {}))
    assert client.disabled == {}
    inner.tools[0]["outputSchema"] = {"type": "object", "properties": {"amount": {"type": "integer"}}}
    clock.now += READ_MAX_AGE_S + 1
    with pytest.raises(ToolDrift):
        _run(client.call_tool("query", {}))


def test_tools_outside_the_pins_are_refused():
    client, inner, _ = _pinned()
    with pytest.raises(ToolDrift):
        _run(client.call_tool("delete.everything", {}))
    assert inner.calls == []


def test_unpinned_demo_tools_are_not_compared():
    inner = _Inner([READ])
    client = PinnedClient(inner, "demo", {"query": ""}, set(), clock=_Clock())
    inner.tools_changed = True
    inner.tools[0]["inputSchema"] = {}
    _run(client.call_tool("query", {}))
    assert client.disabled == {} and inner.calls == ["query"]


def test_session_renewal_verifies_before_the_retry_and_refuses_a_drifted_tool():
    client, inner, _ = _pinned()

    async def renewing_call(name, arguments, timeout_s=None, *, retry_on_session_loss=True):
        # 模拟 HTTP 客户端：404 → 重新握手 → 回调 → 重试
        inner.tools[1]["inputSchema"]["properties"]["n"]["type"] = "string"
        await inner.on_session_renewed()
        inner.calls.append(name)
        return {"ok": True}

    inner.call_tool = renewing_call
    with pytest.raises(ToolDrift):
        _run(client.call_tool("create", {}))
    assert inner.calls == [] and client.disabled == {"create": "fingerprint"}


def test_renewal_during_verification_does_not_reenter():
    client, inner, clock = _pinned()
    original = inner.list_tools

    async def list_with_renewal():
        await inner.on_session_renewed()          # tools/list 自己撞上 404 重新握手
        return await original()

    inner.list_tools = list_with_renewal
    clock.now += READ_MAX_AGE_S + 1
    _run(asyncio.wait_for(client.call_tool("query", {}), timeout=2))
    assert inner.lists == 1 and inner.calls == ["query"]


def test_listing_failure_disables_nothing():
    client, inner, clock = _pinned()

    async def broken():
        raise McpError("merchant: HTTP 503")

    inner.list_tools = broken
    clock.now += READ_MAX_AGE_S + 1
    with pytest.raises(McpError):
        _run(client.call_tool("query", {}))
    assert client.disabled == {} and inner.calls == []


def test_server_version_change_is_logged_not_refused(caplog):
    client, inner, clock = _pinned()
    inner.server_info = {"version": "1.0.1"}
    clock.now += READ_MAX_AGE_S + 1
    with caplog.at_level(logging.WARNING, logger="agent.mcp_bridge.pinning"):
        _run(client.call_tool("query", {}))
    assert inner.calls == ["query"]
    assert any("1.0.1" in r.getMessage() for r in caplog.records)


# ─── 准入：指纹口径、必须钉全、URL 与出口代理 ───

def test_fingerprint_covers_input_and_output_schema_not_description():
    base = tool_fingerprint(READ)
    assert len(base) == 64
    assert tool_fingerprint({**READ, "description": "x"}) == base
    assert tool_fingerprint({**READ, "outputSchema": {}}) != base
    without_output = {k: v for k, v in READ.items() if k != "outputSchema"}
    assert tool_fingerprint(without_output) != base
    assert schema_fingerprint(READ["inputSchema"]) == tool_fingerprint(without_output)


def _remote(tool):
    return ServerSpec(id="merchant", command=[], version="", tools=[tool],
                      transport="streamable_http", url="https://mcp.example.cn/mcp")


def test_remote_tools_must_be_pinned_in_full():
    offered = [READ]
    for pin in ("", tool_fingerprint(READ)[:12]):
        admitted, rejected = admit(_remote(ToolSpec(name="query", intent="m.query", schema_sha=pin)), offered)
        assert admitted == [] and "schema_sha" in rejected[0] and tool_fingerprint(READ) in rejected[0]
    admitted, rejected = admit(
        _remote(ToolSpec(name="query", intent="m.query", schema_sha=tool_fingerprint(READ))), offered)
    assert [t.name for t, _ in admitted] == ["query"] and rejected == []


def test_local_demo_may_leave_a_tool_unpinned_but_not_half_pinned():
    demo = ServerSpec(id="demo", command=[], version="", tools=[], demo=True, transport="stdio")
    demo.tools = [ToolSpec(name="query", intent="m.query")]
    assert [t.name for t, _ in admit(demo, [READ])[0]] == ["query"]
    demo.tools = [ToolSpec(name="query", intent="m.query", schema_sha="abc")]
    assert admit(demo, [READ])[0] == []


@pytest.mark.parametrize("url", ["http://mcp.example.cn/mcp", "https://mcp.example.cn:8443/mcp",
                                 "https://u:p@mcp.example.cn/mcp", "https://10.0.0.8/mcp", ""])
def test_remote_server_url_must_be_clean_https(url):
    spec = _remote(ToolSpec(name="query", intent="m.query"))
    spec.url = url
    assert check_url(spec).startswith(REJECT_URL)


def test_clean_remote_url_and_stdio_pass_url_check():
    assert check_url(_remote(ToolSpec(name="query", intent="m.query"))) == ""
    stdio = ServerSpec(id="demo", command=["python"], version="", tools=[], transport="stdio")
    assert check_url(stdio) == ""


def test_remote_server_must_go_through_the_egress_proxy():
    spec = _remote(ToolSpec(name="query", intent="m.query"))
    assert check_egress(spec, {}).startswith(REJECT_EGRESS)
    proxied = {"HTTPS_PROXY": "http://http-proxy:8080", "NO_PROXY": "redis,nats,registry"}
    assert check_egress(spec, proxied) == ""
    assert check_egress(spec, {"https_proxy": "http://http-proxy:8080"}) == ""
    for bypass in ("mcp.example.cn", ".example.cn", "example.cn", "*"):
        assert check_egress(spec, {**proxied, "no_proxy": bypass}).startswith(REJECT_EGRESS), bypass
    assert check_egress(spec, {**proxied, "NO_PROXY": "ample.cn"}) == ""     # 只按标签边界匹配
    demo = ServerSpec(id="demo", command=["python"], version="", tools=[], demo=True, transport="stdio")
    assert check_egress(demo, {}) == ""

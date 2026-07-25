"""受控 MCP 桥（M3 P2）契约测试——准入是安全面，逐条钉死。"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.mcp_bridge.demo_servers import demo_coffee
from agents.mcp_bridge.src.admission import (REJECT_MISSING, REJECT_SCHEMA,
                                             REJECT_VERSION, ServerSpec, ToolSpec,
                                             admit, check_version, load_servers,
                                             schema_fingerprint)
from agents.mcp_bridge.src.agent import LEDGER_KIND, McpBridgeAgent
from agents.mcp_bridge.src.mcp_client import StdioMcpClient

SERVERS_YAML = "agents/mcp_bridge/servers.yaml"


# ── 准入清单 ────────────────────────────────────────────────────────────

def test_allowlist_loads_and_locks_version_and_schema():
    specs = load_servers(SERVERS_YAML)
    assert [s.id for s in specs] == ["demo-coffee"]
    s = specs[0]
    assert s.version and s.demo is True and s.trust == "third_party"
    assert {t.intent for t in s.tools} == {"shop.menu", "shop.order"}
    write = next(t for t in s.tools if t.write)
    assert write.require_confirm and write.compensate_tool and write.idempotency_key_arg
    assert write.arg_map.get("item") == "sku", "槽位词表与商户参数名解耦要有映射"


def test_version_lock_rejects_drift():
    spec = ServerSpec(id="x", command=[], version="0.1.0", tools=[])
    assert check_version(spec, {"version": "0.1.0"}) == ""
    reason = check_version(spec, {"version": "0.2.0"})
    assert reason.startswith(REJECT_VERSION), "版本变更必须拒载并重新人工准入"


def test_admit_ignores_tools_not_in_allowlist():
    """server 多提供的工具直接忽略——动态放行正是要防的事。"""
    spec = ServerSpec(id="x", command=[], version="1", tools=[
        ToolSpec(name="menu.list", intent="shop.menu")])
    offered = [{"name": "menu.list", "inputSchema": {}},
               {"name": "delete.everything", "inputSchema": {}}]
    admitted, rejected = admit(spec, offered)
    assert [t.intent for t, _ in admitted] == ["shop.menu"]
    assert rejected == []


def test_admit_rejects_schema_drift():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    spec = ServerSpec(id="x", command=[], version="1", tools=[
        ToolSpec(name="t", intent="i", schema_sha=schema_fingerprint(schema))])
    admitted, rejected = admit(spec, [{"name": "t", "inputSchema": {"type": "object"}}])
    assert admitted == [] and rejected and REJECT_SCHEMA in rejected[0]


def test_admit_reports_tool_missing_on_server():
    spec = ServerSpec(id="x", command=[], version="1",
                      tools=[ToolSpec(name="t", intent="i")])
    admitted, rejected = admit(spec, [])
    assert admitted == [] and REJECT_MISSING in rejected[0]


def test_write_tool_without_compensation_is_rejected():
    """§4.F 强制项：没有补偿路径（退款/取消）的写操作不许接。"""
    spec = ServerSpec(id="x", command=[], version="1", tools=[
        ToolSpec(name="pay", intent="shop.pay", write=True, compensate_tool="")])
    admitted, rejected = admit(spec, [{"name": "pay", "inputSchema": {}}])
    assert admitted == [] and "compensate_tool" in rejected[0]


# ── 演示商户 server 的协议与生命周期 ──────────────────────────────────────

def test_demo_server_is_idempotent_and_compensable():
    r1 = demo_coffee._order_create({"sku": "拿铁", "size": "大杯",
                                    "idempotency_key": "unit-k1"})
    oid = r1["structuredContent"]["order_id"]
    r2 = demo_coffee._order_create({"sku": "拿铁", "size": "大杯",
                                    "idempotency_key": "unit-k1"})
    assert r2["structuredContent"]["order_id"] == oid, "同幂等键必须复用原单，绝不双扣"
    assert r2["structuredContent"]["duplicate"] is True
    r3 = demo_coffee._order_cancel({"order_id": oid})
    assert r3["structuredContent"]["status"] == "refunded"
    assert r3["structuredContent"].get("refund_id")


def test_demo_server_rejects_missing_idempotency_key():
    r = demo_coffee._order_create({"sku": "拿铁"})
    assert r.get("isError") is True


# ── 真子进程：协议往返 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stdio_client_round_trip_against_real_subprocess():
    client = StdioMcpClient(
        "demo-coffee",
        [sys.executable, "-m", "agents.mcp_bridge.demo_servers.demo_coffee"],
        timeout_s=20)
    await client.start()
    try:
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "demo-coffee"
        tools = await client.list_tools()
        assert {t["name"] for t in tools} >= {"menu.list", "order.create"}
        res = await client.call_tool("menu.list", {})
        assert res["ok"] and "拿铁" in res["text"]      # 中文过 stdio 不能乱码
    finally:
        await client.close()


# ── Agent 级：合成 capability + 读写两条路径 ──────────────────────────────

class FakeClient:
    """假 MCP client：只记调用，不起子进程。"""

    def __init__(self, reply=None, boom=False):
        self.healthy, self.alive = True, True
        self.calls = []
        self._reply = reply or {"ok": True, "text": "已下单：拿铁大杯 25 元，订单号 DC1",
                                "data": {"order_id": "DC1", "amount_cents": 2500}}
        self._boom = boom

    async def call_tool(self, name, args, timeout_s=None):
        self.calls.append((name, dict(args)))
        if self._boom:
            raise asyncio.TimeoutError("timeout")
        return self._reply


class FakeLedger:
    def __init__(self):
        self.opened, self.closed = [], []

    async def open(self, user_id, session_id, agent_id, kind, goal, **kw):
        self.opened.append((user_id, kind, goal))
        from agents._sdk.ledger import LedgerTask
        return LedgerTask(task_id="t1", user_id=user_id, session_id=session_id,
                          agent_id=agent_id, kind=kind, goal=goal,
                          idempotency_key="k", status="accepted")

    async def close(self, task_id, status, **kw):
        self.closed.append((task_id, status, kw.get("result_ref")))
        return True


async def _agent(reply=None, boom=False):
    a = McpBridgeAgent()
    await a.bootstrap()
    a.ledger = FakeLedger()
    fake = FakeClient(reply=reply, boom=boom)
    for b in a._bindings.values():
        b.client = fake
    return a, fake


@pytest.mark.asyncio
async def test_bootstrap_synthesizes_capabilities_from_allowlist():
    """新增工具 = 改 servers.yaml + 人工审，**不改 Agent 代码**。"""
    a = McpBridgeAgent()
    assert list(a.manifest.capabilities) == []      # manifest 里故意是空的
    await a.bootstrap()
    try:
        intents = {c.intent for c in a.manifest.capabilities}
        assert intents == {"shop.menu", "shop.order"}
        order = next(c for c in a.manifest.capabilities if c.intent == "shop.order")
        assert order.require_confirm is True, "写操作必须声明二次确认"
        assert "演示商户" in order.description, "演示身份要出现在能力描述里"
        assert a.rejections == []
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_read_tool_marks_demo_provenance():
    a, _ = await _agent(reply={"ok": True, "text": "拿铁（22 元起）", "data": {}})
    try:
        res = await run_handle(a, "shop.menu", raw_text="咖啡有什么")
        assert res.status == "ok" and "拿铁" in res.speech
        assert res.speech.startswith("（演示商户）"), "话术层要说明白是演示"
        assert res.ui_card["demo"] is True and res.ui_card["demo_label"] == "演示商户"
        # _prov 绝不盖真章（conventions §9.3）
        assert res.ui_card["_prov"]["mode"] == "mock"
        assert "演示商户" in res.ui_card["_prov"]["note"]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_write_requires_confirmation_first():
    a, fake = await _agent()
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁")
        assert res.status == "need_confirm" and "确认吗" in res.speech
        assert fake.calls == [], "**没确认前一次商户调用都不能发生**"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_write_after_confirm_passes_request_fingerprint_as_idempotency_key():
    a, fake = await _agent()
    try:
        ctx = make_context()
        res = await run_handle(a, "shop.order", slots={"item": "拿铁", "size": "大杯"},
                               raw_text="点一杯拿铁", ctx=ctx,
                               meta={"confirmed": "true"})
        assert res.status == "ok" and "DC1" in res.speech
        name, args = fake.calls[0]
        assert name == "order.create"
        # arg_map：规划期的 item → 商户的 sku（桥不含任何领域词，映射在准入清单里）
        assert args["sku"] == "拿铁" and "item" not in args
        key = args["idempotency_key"]
        assert key and key != "t1", "幂等键必须是请求指纹，不能是每次都新的 task_id"
        assert a.ledger.closed[0][1] == "done"
        assert a.ledger.opened[0][1] == LEDGER_KIND
        # 同一请求再来一次 → 同一个幂等键（商户据此复用原单，不双扣）
        fake.calls.clear()
        await run_handle(a, "shop.order", slots={"item": "拿铁", "size": "大杯"},
                         raw_text="点一杯拿铁", ctx=ctx, meta={"confirmed": "true"})
        assert fake.calls[0][1]["idempotency_key"] == key
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_write_timeout_is_reported_as_uncertain_not_as_failure():
    """超时不等于没下单——**诚实说不确定**并给核对路径，绝不假装失败或成功。"""
    a, _ = await _agent(boom=True)
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech and "核对" in res.speech
        assert a.ledger.closed[0][1] == "failed"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_merchant_duplicate_is_surfaced_not_double_charged():
    a, _ = await _agent(reply={"ok": True, "text": "订单已存在：DC1",
                               "data": {"order_id": "DC1", "duplicate": True}})
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "已经下过了" in res.speech and "DC1" in res.speech
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_unknown_intent_and_unhealthy_server_are_honest():
    a, fake = await _agent()
    try:
        res = await run_handle(a, "shop.nope", raw_text="随便")
        assert "还没接入" in res.speech
        fake.healthy = False
        res = await run_handle(a, "shop.menu", raw_text="咖啡有什么")
        assert "暂时不可用" in res.speech
    finally:
        await a.shutdown()


def test_bridge_is_third_party_and_has_no_vehicle_control_permission():
    a = McpBridgeAgent()
    assert a.manifest.trust_level == "third_party"
    perms = set(a.manifest.requires_permissions)
    assert not any(p.startswith("vehicle.control") for p in perms), \
        "外部服务不得触碰车控——trust_level 硬上限之外再加一道声明层护栏"

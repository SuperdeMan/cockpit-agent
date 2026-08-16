"""受控 MCP 桥（M3 P2）契约测试——准入是安全面，逐条钉死。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.mcp_bridge.demo_servers import demo_coffee
from agents.mcp_bridge.src.admission import (REJECT_MISSING, REJECT_SCHEMA,
                                             REJECT_VERSION, ServerSpec, ToolSpec,
                                             admit, check_version, load_servers,
                                             schema_fingerprint)
from agents.mcp_bridge.src.agent import LEDGER_KIND, McpBridgeAgent, _Binding
from agents.mcp_bridge.src.mcp_client import McpError, StdioMcpClient

SERVERS_YAML = "agents/mcp_bridge/servers.yaml"


def test_e2e_cleanup_uses_atomic_owner_lifecycle_without_captured_ids():
    source = (
        Path(__file__).resolve().parents[3] / "test" / "e2e_mcp.py"
    ).read_text(encoding="utf-8")
    start = "def cleanup_external("
    end = "def bridge_registration("
    assert start in source
    assert end in source
    assert source.index(start) < source.index(end)
    cleanup = source.split(start, 1)[1].split(end, 1)[0]
    assert "order_ids" not in cleanup
    assert 'op="lifecycle_cleanup"' in cleanup
    assert 'op="count"' in cleanup


# ── 准入清单 ────────────────────────────────────────────────────────────

_MCD_TEST_STATUS_MAP = {
    "待支付": "待支付",
    "订单已取消": "订单已取消",
    "已取消": "订单已取消",
}

def test_allowlist_loads_and_locks_version_and_schema(monkeypatch):
    # 2026-08-11 起清单含三台 server：demo-coffee（stdio）+ 麦当劳/瑞幸（
    # streamable_http，真机 tools/list 核实激活）。token 刻意清空——断言缺 env
    # 时商户 server 带着**具名** env_error 被加载（bootstrap 据此整台拒载）。
    monkeypatch.delenv("MCD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LUCKIN_MCP_TOKEN", raising=False)
    specs = load_servers(SERVERS_YAML)
    assert [s.id for s in specs] == ["demo-coffee", "mcdonalds", "luckin"]

    mcd = next(s for s in specs if s.id == "mcdonalds")
    assert mcd.transport == "streamable_http" and mcd.url == "https://mcp.mcd.cn"
    assert not mcd.version, "远程托管平台版本不锁（工具级 schema_sha 锁）"
    assert "MCD_MCP_TOKEN" in mcd.env_error
    # 2026-08-13：`mcd.menu` 让给**当店菜单 workflow**，营养成分表改名 mcd.nutrition
    # ——它返回的一直是营养成分不是菜单，而用户说「菜单」时问的是价格。
    assert {t.intent for t in mcd.tools if t.expose} == {
        "mcd.nutrition", "mcd.order_status"}
    assert [w.intent for w in mcd.workflows] == ["mcd.order", "mcd.menu"]
    mcd_menu = next(w for w in mcd.workflows if w.intent == "mcd.menu")
    assert mcd_menu.required_scopes == ["merchant.read"]
    assert mcd_menu.require_confirm is False
    assert set(mcd_menu.required_tools) == {"query-nearby-stores", "query-meals"}
    assert {t.name for t in mcd.tools if not t.expose} == {
        "query-nearby-stores", "query-meals", "query-meal-detail",
        "calculate-price", "create-order"}
    assert all(not t.write for t in mcd.tools
               if t.name != "create-order")
    mcd_create = next(t for t in mcd.tools if t.name == "create-order")
    assert (mcd_create.write and mcd_create.require_confirm and
            mcd_create.compensate_policy == "abandon_unpaid" and
            mcd_create.unpaid_expiry and mcd_create.retry_policy == "never")
    assert mcd_create.pay_url_locator == "data.payH5Url"
    assert mcd_create.success_predicate == {
        "success": [True], "code": [200]}
    mcd_status = next(t for t in mcd.tools if t.intent == "mcd.order_status")
    assert mcd_status.arg_map.get("order_id") == "orderId"
    assert mcd_status.required_scopes == ["merchant.read"]
    assert mcd_status.forward_owner is False, "官方 remote 不得接收内部 owner 字段"
    assert mcd_status.success_predicate == {"success": [True], "code": [200]}
    assert mcd_status.result_map["order_id"] == "data.orderId"
    assert mcd_status.result_map["status"] == "data.orderStatus"
    assert mcd_status.result_map["amount_cents"] == "data.realTotalAmount"
    assert mcd_status.status_map["订单已取消"] == "订单已取消"
    assert mcd_status.amount_unit == "yuan"

    luckin = next(s for s in specs if s.id == "luckin")
    assert luckin.transport == "streamable_http"
    assert "LUCKIN_MCP_TOKEN" in luckin.env_error
    assert {t.intent for t in luckin.tools if t.expose} == {
        "luckin.order_status"}
    assert {w.intent for w in luckin.workflows} == {
        "luckin.order", "luckin.order_cancel", "luckin.menu"}
    # 只读看菜单不得要求下单权限，也不得进确认闸——它没有可确认的东西
    menu_flow = next(w for w in luckin.workflows if w.intent == "luckin.menu")
    assert menu_flow.required_scopes == ["merchant.read"]
    assert menu_flow.require_confirm is False
    assert set(menu_flow.required_tools) == {
        "queryShopList", "searchProductForMcp"}
    assert {t.name for t in luckin.tools if not t.expose} == {
        "queryShopList", "searchProductForMcp", "queryProductDetailInfo",
        "switchProduct", "previewOrder", "createOrder", "cancelOrder"}
    assert all(not t.write for t in luckin.tools
               if t.name not in {"createOrder", "cancelOrder"})
    status_tool = next(t for t in luckin.tools if t.expose)
    assert status_tool.required_scopes == ["merchant.read"]
    assert status_tool.forward_owner is False
    assert status_tool.success_predicate == {"success": [True], "code": [0]}
    assert status_tool.result_map["order_id"] == "data.orderId"
    assert status_tool.result_map["status"] == "data.orderStatusName"
    assert status_tool.result_map["amount_cents"] == "data.orderPayAmount"
    assert status_tool.status_map["已取消"] == "已取消"
    assert status_tool.amount_unit == "yuan"
    create_tool = next(t for t in luckin.tools if t.name == "createOrder")
    cancel_tool = next(t for t in luckin.tools if t.name == "cancelOrder")
    assert create_tool.write is True and cancel_tool.write is True
    assert create_tool.success_predicate == {
        "success": [True], "code": [0]}
    assert cancel_tool.success_predicate == {
        "success": [True], "code": [0]}
    assert create_tool.compensate_tool == "cancelOrder"
    assert cancel_tool.compensate_policy == "terminal"

    s = specs[0]
    assert s.version and s.demo is True and s.trust == "third_party"
    # M-D：查单与取消补进准入清单。`order.cancel` 从一开始就在商户侧存在、也被
    # order.create 声明为 compensate_tool，但从没进过清单——**于是补偿只在准入期被
    # 校验存在性，运行期零调用、用户零入口**（验收原话）。
    assert {t.intent for t in s.tools} == {
        "shop.menu", "shop.order", "shop.order_status", "shop.order_cancel"}
    create = next(t for t in s.tools if t.intent == "shop.order")
    assert create.require_confirm and create.compensate_tool and create.idempotency_key_arg
    assert create.idempotency_mode == "upstream" and create.forward_owner is True
    assert create.arg_map.get("item") == "sku", "槽位词表与商户参数名解耦要有映射"
    cancel = next(t for t in s.tools if t.intent == "shop.order_cancel")
    assert cancel.write and cancel.require_confirm, "取消是写操作，必须走确认闸"
    assert cancel.compensate_policy == "terminal" and not cancel.compensate_tool
    assert cancel.idempotency_mode == "local_at_most_once"
    assert cancel.retry_policy == "never"
    assert cancel.timeout_outcome == "uncertain"
    status = next(t for t in s.tools if t.intent == "shop.order_status")
    assert not status.write, "查单是只读的"


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
    spec = ServerSpec(id="x", command=[], version="1", demo=True, tools=[
        ToolSpec(name="pay", intent="shop.pay", write=True, require_confirm=True,
                 compensate_tool="", idempotency_mode="local_at_most_once",
                 retry_policy="never", timeout_outcome="uncertain")])
    admitted, rejected = admit(spec, [{"name": "pay", "inputSchema": {}}])
    assert admitted == [] and "compensate_tool" in rejected[0]


# ── 演示商户 server 的协议与生命周期 ──────────────────────────────────────

def test_demo_server_is_idempotent_and_compensable():
    r1 = demo_coffee._order_create({"sku": "拿铁", "size": "大杯",
                                    "idempotency_key": "unit-k1",
                                    "_owner_user_id": "unit-owner"})
    oid = r1["structuredContent"]["order_id"]
    r2 = demo_coffee._order_create({"sku": "拿铁", "size": "大杯",
                                    "idempotency_key": "unit-k1",
                                    "_owner_user_id": "unit-owner"})
    assert r2["structuredContent"]["order_id"] == oid, "同幂等键必须复用原单，绝不双扣"
    assert r2["structuredContent"]["duplicate"] is True
    r3 = demo_coffee._order_cancel({
        "order_id": oid,
        "_owner_user_id": "unit-owner",
    })
    assert r3["structuredContent"]["status"] == "refunded"
    assert r3["structuredContent"].get("refund_id")


def test_demo_server_rejects_missing_idempotency_key():
    r = demo_coffee._order_create({"sku": "拿铁"})
    assert r.get("isError") is True


def test_demo_orders_are_owner_bound_and_cross_owner_idempotency_isolated():
    r1 = demo_coffee._order_create({
        "sku": "拿铁",
        "idempotency_key": "same-fingerprint",
        "_owner_user_id": "owner-a",
    })
    r2 = demo_coffee._order_create({
        "sku": "拿铁",
        "idempotency_key": "same-fingerprint",
        "_owner_user_id": "owner-b",
    })
    assert r1["structuredContent"]["order_id"] != r2["structuredContent"]["order_id"]
    assert "owner_user_id" not in r1["structuredContent"]
    assert "_owner_user_id" not in r1["structuredContent"]


def test_demo_hidden_admin_exact_owner_compensate_then_purge():
    owner = "owner-admin"
    other = "owner-other"
    mine = demo_coffee._order_create({
        "sku": "拿铁", "idempotency_key": "admin-mine",
        "_owner_user_id": owner,
    })["structuredContent"]["order_id"]
    theirs = demo_coffee._order_create({
        "sku": "拿铁", "idempotency_key": "admin-theirs",
        "_owner_user_id": other,
    })["structuredContent"]["order_id"]

    assert "__e2e.namespace.admin" not in {tool["name"] for tool in demo_coffee.TOOLS}
    denied = demo_coffee._order_cancel({
        "order_id": mine, "_owner_user_id": other,
    })
    assert denied["isError"] is True

    counted = demo_coffee._e2e_namespace_admin({
        "op": "count", "_owner_user_id": owner,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    assert counted["structuredContent"]["count"] == 1
    status = demo_coffee._e2e_namespace_admin({
        "op": "status", "_owner_user_id": owner, "order_id": mine,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    assert status["structuredContent"]["status"] == "submitted"
    compensated = demo_coffee._e2e_namespace_admin({
        "op": "compensate", "_owner_user_id": owner, "order_id": mine,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    assert compensated["structuredContent"]["status"] == "refunded"
    repeated = demo_coffee._e2e_namespace_admin({
        "op": "compensate", "_owner_user_id": owner, "order_id": mine,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    assert repeated["structuredContent"]["duplicate"] is True
    purged = demo_coffee._e2e_namespace_admin({
        "op": "purge", "_owner_user_id": owner,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    assert purged["structuredContent"]["deleted"] == 1
    assert theirs in demo_coffee._ORDERS


def test_demo_atomic_lifecycle_cleanup_discovers_unseen_orders_and_is_idempotent():
    owner = "owner-lost-response"
    other = "owner-lifecycle-other"
    first = demo_coffee._order_create({
        "sku": "拿铁",
        "idempotency_key": "lost-response-1",
        "_owner_user_id": owner,
    })["structuredContent"]["order_id"]
    second = demo_coffee._order_create({
        "sku": "美式",
        "idempotency_key": "lost-response-2",
        "_owner_user_id": owner,
    })["structuredContent"]["order_id"]
    other_order = demo_coffee._order_create({
        "sku": "摩卡",
        "idempotency_key": "other-owner-order",
        "_owner_user_id": other,
    })["structuredContent"]["order_id"]
    demo_coffee._order_cancel({
        "order_id": second,
        "_owner_user_id": owner,
    })

    request = {
        "op": "lifecycle_cleanup",
        "_owner_user_id": owner,
        "write_tool": "order.create",
        "compensate_tool": "order.cancel",
    }
    cleaned = demo_coffee._e2e_namespace_admin(request)

    assert cleaned["structuredContent"] == {
        "op": "lifecycle_cleanup",
        "count": 0,
        "deleted": 2,
    }
    assert first not in demo_coffee._ORDERS
    assert second not in demo_coffee._ORDERS
    assert other_order in demo_coffee._ORDERS
    assert not any(key[0] == owner for key in demo_coffee._BY_IDEM)
    assert demo_coffee._e2e_namespace_admin(request)["structuredContent"] == {
        "op": "lifecycle_cleanup",
        "count": 0,
        "deleted": 0,
    }
    assert "__e2e.namespace.admin" not in {
        tool["name"] for tool in demo_coffee.TOOLS
    }


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


@pytest.mark.asyncio
async def test_stdio_hidden_cleanup_discovers_lost_response_in_same_subprocess():
    client = StdioMcpClient(
        "demo-coffee-cleanup",
        [sys.executable, "-m", "agents.mcp_bridge.demo_servers.demo_coffee"],
        timeout_s=20,
    )
    owner = "owner-stdio-lost-response"
    admin = {
        "_owner_user_id": owner,
        "write_tool": "order.create",
        "compensate_tool": "order.cancel",
    }
    await client.start()
    try:
        await client.initialize()
        tools = await client.list_tools()
        assert "__e2e.namespace.admin" not in {
            item["name"] for item in tools
        }
        # Simulate a committed order whose response never reached the caller.
        await client.call_tool("order.create", {
            "sku": "拿铁",
            "idempotency_key": "stdio-lost-response",
            "_owner_user_id": owner,
        })
        cleaned = await client.call_tool("__e2e.namespace.admin", {
            **admin,
            "op": "lifecycle_cleanup",
        })
        counted = await client.call_tool("__e2e.namespace.admin", {
            **admin,
            "op": "count",
        })
        assert cleaned["ok"] is True
        assert cleaned["data"]["deleted"] == 1
        assert cleaned["data"]["count"] == 0
        assert counted["data"]["count"] == 0
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
        self.retry_flags = []

    async def call_tool(self, name, args, timeout_s=None,
                        retry_on_session_loss=True):
        self.calls.append((name, dict(args)))
        self.retry_flags.append(retry_on_session_loss)
        if self._boom:
            raise asyncio.TimeoutError("timeout")
        return self._reply


class InProcessDemoAdminClient:
    """Exercise the real hidden demo lifecycle handler without subprocess I/O."""

    healthy = True
    alive = True

    def __init__(self, *, reject=False):
        self.calls = []
        self.reject = reject

    async def call_tool(self, name, args, timeout_s=None,
                        retry_on_session_loss=True):
        del timeout_s, retry_on_session_loss
        self.calls.append((name, dict(args)))
        if self.reject:
            return {"ok": False, "text": "rejected", "data": {}}
        raw = demo_coffee._e2e_namespace_admin(dict(args))
        return {
            "ok": not bool(raw.get("isError")),
            "text": "",
            "data": raw.get("structuredContent") or {},
        }


class PrivacyDraftStore:
    def __init__(self, result=True):
        self.result = result
        self.users = []

    async def delete_owner(self, user_id):
        self.users.append(user_id)
        return self.result


def _demo_admin_binding(client):
    server = ServerSpec(
        id="demo-coffee", command=[], version="1", tools=[], demo=True,
        transport="stdio")
    tool = ToolSpec(
        name="order.create", intent="shop.order", write=True,
        require_confirm=True, compensate_tool="order.cancel",
        compensate_policy="terminal", forward_owner=True)
    return _Binding(server, client, tool, {})


class FakeLedger:
    def __init__(self, history=None):
        self.opened, self.closed = [], []
        self.history = history or []

    async def open(self, user_id, session_id, agent_id, kind, goal, **kw):
        self.opened.append((user_id, kind, goal))
        from agents._sdk.ledger import LedgerTask
        return LedgerTask(task_id="t1", user_id=user_id, session_id=session_id,
                          agent_id=agent_id, kind=kind, goal=goal,
                          idempotency_key="k", status="accepted")

    async def close(self, task_id, status, **kw):
        self.closed.append((task_id, status, kw.get("result_ref")))
        return True

    async def recent(self, user_id, *, kind="", limit=5):
        return list(self.history)[:limit]


async def _agent(reply=None, boom=False):
    a = McpBridgeAgent()
    await a.bootstrap()
    a.ledger = FakeLedger()
    fake = FakeClient(reply=reply, boom=boom)
    for b in a._bindings.values():
        b.client = fake
    return a, fake


@pytest.mark.asyncio
async def test_bootstrap_synthesizes_capabilities_from_allowlist(monkeypatch):
    """新增工具 = 改 servers.yaml + 人工审，**不改 Agent 代码**。

    token 刻意清空：断言真实商户 server 在无凭证环境**具名诚实缺席**（rejections
    记 env_var_missing、能力面只剩 demo 四件）——不静默拿空 token 出站吃 401。
    """
    monkeypatch.delenv("MCD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LUCKIN_MCP_TOKEN", raising=False)
    a = McpBridgeAgent()
    assert list(a.manifest.capabilities) == []      # manifest 里故意是空的
    await a.bootstrap()
    try:
        intents = {c.intent for c in a.manifest.capabilities}
        assert intents == {"shop.menu", "shop.order",
                           "shop.order_status", "shop.order_cancel"}
        order = next(c for c in a.manifest.capabilities if c.intent == "shop.order")
        assert order.require_confirm is True, "写操作必须声明二次确认"
        assert "演示商户" in order.description, "演示身份要出现在能力描述里"
        assert a._bindings["shop.order"].input_schema["required"] == [
            "sku", "idempotency_key"], "_Binding 必须保留实时 inputSchema 本体"
        rejected_ids = {r.split(":")[0] for r in a.rejections}
        assert rejected_ids == {"mcdonalds", "luckin"}
        assert all("env_var_missing" in r for r in a.rejections)
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
        assert args["_owner_user_id"] == ctx.user_id
        assert fake.retry_flags == [False], "写路径不得在 404 重握手后自动重放"
        key = args["idempotency_key"]
        assert key and key != "t1", "幂等键必须是请求指纹，不能是每次都新的 task_id"
        assert a.ledger.closed[0][1] == "done"
        assert a.ledger.opened[0][1] == LEDGER_KIND
        # 同一请求再来一次 → 同一个幂等键（商户据此复用原单，不双扣）
        fake.calls.clear()
        await run_handle(a, "shop.order", slots={"item": "拿铁", "size": "大杯"},
                         raw_text="点一杯拿铁", ctx=ctx, meta={"confirmed": "true"})
        assert fake.calls[0][1]["idempotency_key"] == key
        assert fake.retry_flags[-1] is False
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_agent_namespace_admin_uses_write_binding_without_registering_hidden_tools():
    a, fake = await _agent()
    try:
        response = await a.namespace_admin({
            "op": "count",
            "user_id": "owner-a",
            "order_id": "",
            "intent": "shop.order",
        })
        assert fake.calls == [("__e2e.namespace.admin", {
            "op": "count",
            "_owner_user_id": "owner-a",
            "order_id": "",
            "write_tool": "order.create",
            "compensate_tool": "order.cancel",
        })]
        assert response["ok"] is True
        fake.calls.clear()
        response = await a.namespace_admin({
            "op": "lifecycle_cleanup",
            "user_id": "owner-a",
            "order_id": "",
            "intent": "shop.order",
        })
        assert fake.calls[0][1]["op"] == "lifecycle_cleanup"
        assert response["ok"] is True
        assert {c.intent for c in a.manifest.capabilities} == {
            "shop.menu", "shop.order", "shop.order_status", "shop.order_cancel"}
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_write_timeout_is_reported_as_uncertain_not_as_failure():
    """超时不等于没下单——诚实说不确定，并指向**真的存在**的核对入口。

    这条断言的历史值得留着：验收时它承诺「说『查一下我的订单』我帮你核对」而入口
    并不存在（准入清单只有 menu/order），于是先改成不承诺；M-D 把 order.get 接进
    清单后才把承诺加回来。**先有能力再有话术**——反过来就是把不确定包装成
    「有办法查清楚」。账目 result_ref 仍落 outcome=uncertain，不照 failed 状态说
    「上次下单失败了」。"""
    a, _ = await _agent(boom=True)
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert "查一下我的订单" in res.speech, "入口已存在，就该告诉用户怎么核对"
        assert "幂等键" in res.speech, "核对靠幂等键——超时这一单根本没拿到订单号"
        assert "别再下一单" in res.speech, "要提醒别重复下单（防用户自己造成双扣）"
        tid, status, ref = a.ledger.closed[0]
        assert status == "failed" and ref.get("outcome") == "uncertain"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_privacy_delete_keeps_demo_orders_as_external_references():
    owner = "privacy-target-inprocess"
    control = "privacy-control-inprocess"
    client = InProcessDemoAdminClient()
    drafts = PrivacyDraftStore()
    agent = McpBridgeAgent(draft_store=drafts)
    agent._bindings["shop.order"] = _demo_admin_binding(client)
    cleanup = lambda user: demo_coffee._e2e_namespace_admin({
        "op": "lifecycle_cleanup", "_owner_user_id": user,
        "write_tool": "order.create", "compensate_tool": "order.cancel",
    })
    cleanup(owner)
    cleanup(control)
    demo_coffee._order_create({
        "sku": "latte", "idempotency_key": "privacy-target",
        "_owner_user_id": owner,
    })
    demo_coffee._order_create({
        "sku": "latte", "idempotency_key": "privacy-control",
        "_owner_user_id": control,
    })
    try:
        assert await agent.delete_personal_data(
            owner, "privacy_user_all") is True
        assert drafts.users == [owner]
        target = demo_coffee._e2e_namespace_admin({
            "op": "count", "_owner_user_id": owner,
            "write_tool": "order.create", "compensate_tool": "order.cancel",
        })["structuredContent"]
        control_state = demo_coffee._e2e_namespace_admin({
            "op": "count", "_owner_user_id": control,
            "write_tool": "order.create", "compensate_tool": "order.cancel",
        })["structuredContent"]
        assert target["count"] == 1
        assert control_state["count"] == 1
        assert client.calls == []
    finally:
        cleanup(owner)
        cleanup(control)


@pytest.mark.asyncio
@pytest.mark.parametrize("draft_ok", [False, True])
async def test_privacy_delete_ack_depends_only_on_merchant_draft_store(draft_ok):
    drafts = PrivacyDraftStore(result=draft_ok)
    client = InProcessDemoAdminClient(reject=True)
    agent = McpBridgeAgent(draft_store=drafts)
    agent._bindings["shop.order"] = _demo_admin_binding(client)

    assert await agent.delete_personal_data(
        "privacy-failure-owner", "privacy_user_all") is draft_ok
    assert drafts.users == ["privacy-failure-owner"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_privacy_delete_without_demo_binding_does_not_touch_official_remote():
    drafts = PrivacyDraftStore(result=True)
    client = FakeClient()
    agent = McpBridgeAgent(draft_store=drafts)
    server = ServerSpec(
        id="official", command=[], version="", tools=[], demo=False,
        transport="streamable_http")
    tool = ToolSpec(
        name="create-order", intent="official.order", write=True,
        require_confirm=True, compensate_tool="cancel-order",
        compensate_policy="terminal", forward_owner=True)
    agent._bindings[tool.intent] = _Binding(server, client, tool, {})

    assert await agent.delete_personal_data(
        "privacy-official-owner", "privacy_user_all") is True
    assert drafts.users == ["privacy-official-owner"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_privacy_delete_keeps_stdio_demo_until_explicit_lifecycle_cleanup():
    owner = "privacy-target-stdio"
    control = "privacy-control-stdio"
    client = StdioMcpClient(
        "demo-coffee-privacy",
        [sys.executable, "-m", "agents.mcp_bridge.demo_servers.demo_coffee"],
        timeout_s=20,
    )
    drafts = PrivacyDraftStore(result=True)
    agent = McpBridgeAgent(draft_store=drafts)
    agent._bindings["shop.order"] = _demo_admin_binding(client)
    await client.start()
    try:
        await client.initialize()
        for user, idem in ((owner, "privacy-target"),
                           (control, "privacy-control")):
            created = await client.call_tool("order.create", {
                "sku": "latte", "idempotency_key": idem,
                "_owner_user_id": user,
            })
            assert created["ok"] is True

        assert await agent.delete_personal_data(
            owner, "privacy_user_all") is True
        target = await agent.namespace_admin({
            "op": "count", "user_id": owner, "order_id": "",
            "intent": "shop.order",
        })
        control_state = await agent.namespace_admin({
            "op": "count", "user_id": control, "order_id": "",
            "intent": "shop.order",
        })
        assert target["ok"] is True and target["count"] == 1
        assert control_state["ok"] is True and control_state["count"] == 1

        cleaned = await agent.namespace_admin({
            "op": "lifecycle_cleanup", "user_id": owner,
            "order_id": "", "intent": "shop.order",
        })
        assert cleaned["ok"] is True and cleaned["count"] == 0
    finally:
        await agent.namespace_admin({
            "op": "lifecycle_cleanup", "user_id": owner,
            "order_id": "", "intent": "shop.order",
        })
        await agent.namespace_admin({
            "op": "lifecycle_cleanup", "user_id": control,
            "order_id": "", "intent": "shop.order",
        })
        await client.close()


@pytest.mark.asyncio
async def test_live_schema_requires_every_required_argument_before_outbound_call():
    """实时 schema.required 有两个参数时，只填其中一个必须追问，不能打一发必败请求。"""
    a = McpBridgeAgent()
    fake = FakeClient(reply={"ok": True, "text": "x", "data": {}})
    server = ServerSpec(id="merchant", command=[], version="", tools=[])
    tool = ToolSpec(name="lookup", intent="merchant.lookup", slots=["account", "region"],
                    arg_map={"account": "accountId", "region": "regionCode"})
    a._bindings[tool.intent] = _Binding(
        server, fake, tool,
        {"type": "object",
         "properties": {"accountId": {"type": "string"},
                        "regionCode": {"type": "string"}},
         "required": ["accountId", "regionCode"]})
    try:
        res = await run_handle(a, tool.intent, slots={"account": "A-1"},
                               raw_text="查账号")
        assert res.status == "need_slot"
        assert res.missing_slots == ["region"]
        assert fake.calls == []
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_required_scopes_are_authoritative_and_remote_owner_is_not_forwarded():
    a = McpBridgeAgent()
    fake = FakeClient(reply={"ok": True, "text": "查到了", "data": {}})
    server = ServerSpec(id="official-merchant", command=[], version="", tools=[],
                        transport="streamable_http", demo=False)
    tool = ToolSpec(name="query-order", intent="merchant.order_status",
                    slots=["order_id"], required_scopes=["merchant.read"],
                    forward_owner=True)  # 故意误配：remote 仍不得下发内部 owner
    a._bindings[tool.intent] = _Binding(
        server, fake, tool,
        {"type": "object", "properties": {"order_id": {"type": "string"}},
         "required": ["order_id"]})
    try:
        ctx = make_context()
        denied = await run_handle(
            a, tool.intent, slots={"order_id": "O-1"}, raw_text="查单", ctx=ctx,
            meta={"speaker_id": "voiceprint-user", "granted_scopes": "profile.read"})
        assert "授权" in denied.speech
        assert fake.calls == [], "user_id/声纹都不能代替 merchant.read 授权"

        allowed = await run_handle(
            a, tool.intent, slots={"order_id": "O-1"}, raw_text="查单", ctx=ctx,
            meta={"granted_scopes": "profile.read,merchant.read"})
        assert allowed.status == "ok"
        assert "_owner_user_id" not in fake.calls[0][1]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_remote_write_needs_merchant_write_and_never_forwards_owner_or_replays():
    a = McpBridgeAgent()
    a.ledger = FakeLedger()
    fake = FakeClient()
    server = ServerSpec(id="official-merchant", command=[], version="", tools=[],
                        transport="streamable_http", demo=False)
    tool = ToolSpec(name="create-order", intent="merchant.order", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode="local_at_most_once", retry_policy="never",
                    required_scopes=["merchant.write"], forward_owner=True)
    a._bindings[tool.intent] = _Binding(
        server, fake, tool,
        {"type": "object", "properties": {}, "required": []})
    try:
        denied = await run_handle(
            a, tool.intent, raw_text="下单", meta={"confirmed": "true",
                                                  "granted_scopes": "merchant.read"})
        assert "授权" in denied.speech and fake.calls == []

        allowed = await run_handle(
            a, tool.intent, raw_text="下单", meta={"confirmed": "true",
                                                  "granted_scopes": "merchant.write"})
        assert allowed.status == "ok"
        assert "_owner_user_id" not in fake.calls[0][1]
        assert fake.retry_flags == [False]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_remote_read_strips_owner_reintroduced_by_ledger_backfill():
    from agents._sdk.ledger import LedgerTask

    history = [LedgerTask(
        task_id="old", user_id="u1", session_id="s1", agent_id="mcp-bridge",
        kind=LEDGER_KIND, goal="{}", idempotency_key="ledger-owner-value",
        status="failed", result_ref={"server": "official-merchant",
                                     "outcome": "uncertain"})]
    a = McpBridgeAgent()
    a.ledger = FakeLedger(history)
    fake = FakeClient(reply={"ok": True, "text": "查到了", "data": {}})
    server = ServerSpec(id="official-merchant", command=[], version="", tools=[],
                        transport="streamable_http", demo=False)
    # 绕过 admission 模拟存量/恶意 Binding：回填幂等引用时把目标参数映射成 owner。
    tool = ToolSpec(name="query", intent="merchant.query", slots=["order_id"],
                    arg_map={"idempotency_key": "_owner_user_id"})
    a._bindings[tool.intent] = _Binding(
        server, fake, tool, {"type": "object", "properties": {}, "required": []})
    try:
        res = await run_handle(a, tool.intent, raw_text="查单", ctx=make_context())
        assert res.status == "ok"
        assert "_owner_user_id" not in fake.calls[0][1]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_remote_write_and_hidden_admin_strip_owner_reintroduced_by_idem():
    a = McpBridgeAgent()
    a.ledger = FakeLedger()
    fake = FakeClient()
    server = ServerSpec(id="official-merchant", command=[], version="", tools=[],
                        transport="streamable_http", demo=False)
    # 同样绕过 admission：upstream 幂等参数恶意指向内部 owner 名。
    tool = ToolSpec(
        name="create", intent="merchant.create", write=True, require_confirm=True,
        compensate_policy="terminal", compensate_tool="cancel",
        idempotency_mode="upstream", idempotency_key_arg="_owner_user_id",
        forward_owner=True)
    a._bindings[tool.intent] = _Binding(
        server, fake, tool, {"type": "object", "properties": {}, "required": []})
    try:
        res = await run_handle(a, tool.intent, raw_text="下单", ctx=make_context(),
                               meta={"confirmed": "true"})
        assert res.status == "ok"
        assert "_owner_user_id" not in fake.calls[0][1]

        fake.calls.clear()
        await a.namespace_admin({
            "op": "count", "user_id": "context-owner", "order_id": "",
            "intent": tool.intent,
        })
        assert "_owner_user_id" not in fake.calls[0][1]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_abandon_unpaid_confirmation_appends_bridge_owned_expiry_notice():
    a = McpBridgeAgent()
    fake = FakeClient()
    server = ServerSpec(id="merchant", command=[], version="", tools=[], demo=False)
    tool = ToolSpec(
        name="create", intent="merchant.create", write=True, require_confirm=True,
        compensate_policy="abandon_unpaid", unpaid_expiry=True,
        confirm_prompt="准备下单：{args}，确认吗？")
    a._bindings[tool.intent] = _Binding(server, fake, tool, {})
    try:
        res = await run_handle(a, tool.intent, raw_text="下单")
        assert res.status == "need_confirm"
        assert "准备下单" in res.speech
        assert "本单不立即扣款；不支付会由商户自动失效" in res.speech
        assert fake.calls == []
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_non_exposed_tool_is_neither_registered_nor_dispatchable():
    a = McpBridgeAgent()
    fake = FakeClient()
    server = ServerSpec(id="merchant", command=[], version="", tools=[])
    tool = ToolSpec(name="cancel.internal", intent="merchant.cancel.internal",
                    expose=False)
    a._bindings[tool.intent] = _Binding(server, fake, tool, {})
    a._sync_capabilities()
    try:
        assert not list(a.manifest.capabilities)
        res = await run_handle(a, tool.intent, raw_text="取消")
        assert "还没接入" in res.speech
        assert fake.calls == []
    finally:
        await a.shutdown()


def test_card_filters_external_actions_and_overwrites_trusted_identity_fields():
    a = McpBridgeAgent()
    server = ServerSpec(id="official-merchant", command=[], version="", tools=[])
    tool = ToolSpec(name="query-order", intent="merchant.order_status")
    binding = SimpleNamespace(server=server, tool=tool)
    card = a._card(binding, "mcp_result", {
        "type": "external", "server": "evil", "tool": "evil",
        "merchant": "evil", "_prov": {"source": "evil"},
        "buttons": [{"title": "点我"}], "actions": [{"url": "https://evil"}],
        "demo_label": "伪造演示身份",
        "order_id": "O-1",
    })
    assert card["type"] == "mcp_result"
    assert card["server"] == card["merchant"] == "official-merchant"
    assert card["tool"] == "query-order"
    assert card["_prov"]["vendor"] == "official-merchant"
    assert card["order_id"] == "O-1"
    assert "buttons" not in card and "actions" not in card
    assert "demo_label" not in card


def test_card_drops_all_reserved_payload_keys_before_trusted_stamp(monkeypatch):
    """即使盖章 helper 被替换，外部 `_prov` 也不能在中间 card 中存活。"""
    monkeypatch.setattr("agents.mcp_bridge.src.agent.attach",
                        lambda card, *args, **kwargs: card)
    a = McpBridgeAgent()
    binding = SimpleNamespace(
        server=ServerSpec(id="merchant", command=[], version="", tools=[]),
        tool=ToolSpec(name="query", intent="merchant.query"))
    card = a._card(binding, "trusted", {
        "_prov": {"vendor": "evil"}, "type": "evil", "server": "evil",
        "tool": "evil", "merchant": "evil", "buttons": [1], "actions": [2],
        "demo_label": "evil",
        "value": 7,
    })
    assert "_prov" not in card
    assert card == {"value": 7, "type": "trusted", "server": "merchant",
                    "tool": "query", "merchant": "merchant"}


@pytest.mark.asyncio
async def test_mcp_connect_timeout_on_write_is_still_uncertain():
    """即便客户端判断请求可能没发出，写路径也不把它说成确定没发出。"""
    from agents.mcp_bridge.src import mcp_client

    timeout_type = getattr(mcp_client, "McpTimeout")

    class _Timeout(FakeClient):
        async def call_tool(self, name, args, timeout_s=None,
                            retry_on_session_loss=True):
            self.calls.append((name, dict(args)))
            self.retry_flags.append(retry_on_session_loss)
            raise timeout_type("merchant: HTTP timeout", sent=False)

    a, _ = await _agent()
    timeout_client = _Timeout()
    for binding in a._bindings.values():
        binding.client = timeout_client
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert "没有发出去" not in res.speech
        assert timeout_client.retry_flags == [False]
        assert a.ledger.closed[0][2]["outcome"] == "uncertain"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    McpError("merchant: response parse failed"),
    OSError("transport broke after send"),
])
async def test_nonlocal_write_errors_are_uncertain(failure):
    """进入 call_tool 后所有异常都不能断言上游确定未受理。"""
    class _McpFailure(FakeClient):
        async def call_tool(self, name, args, timeout_s=None,
                            retry_on_session_loss=True):
            self.calls.append((name, dict(args)))
            self.retry_flags.append(retry_on_session_loss)
            raise failure

    a, _ = await _agent()
    failed = _McpFailure()
    for binding in a._bindings.values():
        binding.client = failed
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert "没有发出去" not in res.speech
        assert a.ledger.closed[0][2]["outcome"] == "uncertain"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_runtime_error_after_entering_call_tool_is_uncertain():
    """RuntimeError 也可能发生在上游受理后；跨过 call_tool 边界就必须保守。"""
    class _Boom(FakeClient):
        async def call_tool(self, name, args, timeout_s=None,
                            retry_on_session_loss=True):
            raise RuntimeError("proc not started")

    a, _ = await _agent()
    for b in a._bindings.values():
        b.client = _Boom()
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert "没有发出去" not in res.speech
        assert a.ledger.closed[0][2]["outcome"] == "uncertain"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_tool_iserror_after_write_is_uncertain_and_remote_text_is_not_replayed():
    a, fake = await _agent(reply={
        "ok": False,
        "text": "商户报错但可能已受理 Bearer remote-secret",
        "data": {"order_id": "maybe-created"},
    })
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert "没有发出去" not in res.speech
        assert "remote-secret" not in res.speech
        assert a.ledger.closed[0][2] == {
            "error": "mcp_uncertain", "outcome": "uncertain"}
        assert fake.retry_flags == [False]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_cancelled_write_records_uncertain_then_preserves_cancellation():
    class _Cancelled(FakeClient):
        async def call_tool(self, name, args, timeout_s=None,
                            retry_on_session_loss=True):
            self.calls.append((name, dict(args)))
            self.retry_flags.append(retry_on_session_loss)
            raise asyncio.CancelledError()

    a, _ = await _agent()
    cancelled = _Cancelled()
    for binding in a._bindings.values():
        binding.client = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_handle(a, "shop.order", slots={"item": "拿铁"},
                             raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert a.ledger.closed[0][2]["outcome"] == "uncertain"
        assert cancelled.retry_flags == [False]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {},
    {"ok": True, "text": "x", "data": []},
    {"ok": True, "text": {"not": "text"}, "data": {}},
])
async def test_malformed_write_result_after_call_is_uncertain(reply):
    class _Malformed(FakeClient):
        async def call_tool(self, name, args, timeout_s=None,
                            retry_on_session_loss=True):
            self.calls.append((name, dict(args)))
            self.retry_flags.append(retry_on_session_loss)
            return reply

    a, _ = await _agent()
    client = _Malformed()
    for binding in a._bindings.values():
        binding.client = client
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" in res.speech
        assert a.ledger.closed[0][2]["outcome"] == "uncertain"
        assert client.retry_flags == [False]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ledger", "payment", "card"])
async def test_verified_write_success_is_not_downgraded_by_post_call_failure(
        stage, monkeypatch):
    a, fake = await _agent(reply={
        "ok": True, "text": "订单创建成功",
        "data": ({"order_id": "O-1", "payH5Url": "https://m.mcd.cn/pay/O-1"}
                 if stage == "payment" else {"order_id": "O-1"}),
    })
    binding = a._bindings["shop.order"]
    if stage == "ledger":
        class _FailingLedger(FakeLedger):
            async def close(self, task_id, status, **kw):
                self.closed.append((task_id, status, kw.get("result_ref")))
                raise RuntimeError("ledger unavailable")

        a.ledger = _FailingLedger()
    elif stage == "payment":
        binding.tool.pay_url_locator = "payH5Url"
        binding.server.pay_url_hosts = ["m.mcd.cn"]

        async def _payment_boom(*args, **kwargs):
            raise RuntimeError("post-call payment failure")

        monkeypatch.setattr(a, "_register_merchant_payment", _payment_boom)
    else:
        monkeypatch.setattr(a, "_card",
                            lambda *args, **kwargs: (_ for _ in ()).throw(
                                RuntimeError("post-call card failure")))
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有拿到确认结果" not in res.speech
        assert "没有发出去" not in res.speech
        assert fake.retry_flags == [False]
        assert res.data == {"order_id": "O-1"}
        assert not any(status == "failed" for _, status, _ in a.ledger.closed)
        assert len(a.ledger.closed) == 1
        assert a.ledger.closed[0][1] == "done"
        if stage == "ledger":
            assert "本地记录同步异常，请勿重复下单" in res.speech
        elif stage == "payment":
            assert res.ui_card["type"] == "mcp_order"
            assert "支付入口暂不可用" in res.speech
        else:
            assert res.ui_card is None
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_cancel_after_verified_success_records_done_not_uncertain(monkeypatch):
    a, _ = await _agent(reply={
        "ok": True, "text": "订单创建成功", "data": {"order_id": "O-2"},
    })

    async def _cancel_after_success(*args, **kwargs):
        raise asyncio.CancelledError()

    binding = a._bindings["shop.order"]
    binding.tool.pay_url_locator = "payH5Url"
    binding.server.pay_url_hosts = ["m.mcd.cn"]
    monkeypatch.setattr(a, "_register_merchant_payment", _cancel_after_success)
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_handle(a, "shop.order", slots={"item": "拿铁"},
                             raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert len(a.ledger.closed) == 1
        assert a.ledger.closed[0][1] == "done"
        assert not any((ref or {}).get("outcome") == "uncertain"
                       for _, _, ref in a.ledger.closed)
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_second_cancellation_during_uncertain_ledger_close_is_not_swallowed():
    class _BlockingLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def close(self, task_id, status, **kw):
            self.started.set()
            await self.release.wait()
            return await super().close(task_id, status, **kw)

    a, _ = await _agent()
    ledger = _BlockingLedger()
    a.ledger = ledger
    binding = a._bindings["shop.order"]
    task = SimpleNamespace(task_id="t-cancel")
    pending = asyncio.create_task(a._record_uncertain_write(binding, task))
    try:
        await ledger.started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        ledger.release.set()
        await asyncio.sleep(0)
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


# ── 查单 / 取消 / 补偿（M-D）───────────────────────────────
def _ledger_task(order_id="", idem="idem-1", *, user_id="u1",
                 session_id="s1", server="", task_status="done",
                 order_status="", outcome="", goal="{}"):
    from agents._sdk.ledger import LedgerTask
    result_ref = ({"order_id": order_id} if order_id else
                  {"error": "timeout", "outcome": "uncertain"})
    if server:
        result_ref["server"] = server
    if order_status:
        result_ref["status"] = order_status
    if outcome:
        result_ref["outcome"] = outcome
    return LedgerTask(task_id=f"t-{order_id or idem}", user_id=user_id,
                      session_id=session_id,
                      agent_id="mcp-bridge", kind=LEDGER_KIND,
                      goal=goal, idempotency_key=idem, status=task_status,
                      result_ref=result_ref)


@pytest.mark.asyncio
async def test_write_backfill_filters_owner_merchant_and_state_then_prefers_session():
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[
        _ledger_task("FOREIGN", user_id="u2"),
        _ledger_task("LUCKIN", server="luckin"),
        _ledger_task("FAILED", task_status="failed", order_status="failed"),
        _ledger_task("UNCERTAIN", order_status="uncertain",
                     outcome="uncertain"),
        _ledger_task("CANCELLED", order_status="cancelled"),
        _ledger_task("OTHER-SESSION", session_id="s-old"),
        _ledger_task("CURRENT-SESSION", session_id="s1",
                     order_status="created"),
    ])
    binding = a._bindings["shop.order_cancel"]
    ctx = SimpleNamespace(user_id="u1", session_id="s1")
    try:
        # 返回值形态在 Q10（2026-08-16）改成 `(slots, OrderRef)`——与读路径
        # `_resolve_order_ref` 对齐。**契约变了，不是这条测试坏了。**
        slots, ref = await a._backfill_write_slots(binding, ["order_id"], ctx)
        assert slots == {"order_id": "CURRENT-SESSION"}
        assert ref.found is True and ref.from_session is True
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [
    [_ledger_task("FOREIGN", user_id="u2")],
    [_ledger_task("LUCKIN", server="luckin")],
    [_ledger_task("FAILED", task_status="failed", order_status="failed")],
    [_ledger_task("UNCERTAIN", order_status="uncertain", outcome="uncertain")],
    [_ledger_task("CANCELLED-TASK", task_status="cancelled",
                  order_status="created")],
    [_ledger_task("CANCELLED", order_status="cancelled")],
    [_ledger_task("CANCELLED", order_status="cancelled"),
     _ledger_task("CANCELLED", order_status="created")],
    [_ledger_task("", task_status="failed", outcome="uncertain",
                  goal='{"order_id":"UNCERTAIN"}'),
     _ledger_task("UNCERTAIN", order_status="created")],
])
async def test_write_backfill_never_targets_foreign_or_non_cancellable_order(
        history):
    a, _ = await _agent()
    a.ledger = FakeLedger(history=history)
    binding = a._bindings["shop.order_cancel"]
    ctx = SimpleNamespace(user_id="u1", session_id="s1")
    try:
        slots, ref = await a._backfill_write_slots(binding, ["order_id"], ctx)
        assert slots == {} and ref.found is False
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_order_status_looks_up_the_last_order_id_without_asking():
    """用户说「查一下我的订单」时不带订单号——从账本取他最近这一单。"""
    a, fake = await _agent(reply={"ok": True, "text": "订单 DC1：拿铁中杯，submitted",
                                  "data": {"found": True, "status": "submitted"}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="DC1")])
    try:
        res = await run_handle(a, "shop.order_status", raw_text="查一下我的订单")
        assert res.status == "ok"
        args = fake.calls[0][1]
        assert args["order_id"] == "DC1"
        assert args["_owner_user_id"] == "u1", "owner 由已验证 Context 派生"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_uncertain_order_is_reconciled_by_idempotency_key():
    """**这是超时那一单唯一能核对的方式**：响应没回来 → 我们没有订单号，
    但幂等键是自己生成的、商户按它索引。此前只能让用户「先去商家处核实」。"""
    a, fake = await _agent(reply={"ok": True, "text": "没有查到这一单",
                                  "data": {"found": False}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="", idem="idem-9")])
    try:
        await run_handle(a, "shop.order_status", raw_text="查一下我的订单")
        args = fake.calls[0][1]
        assert "order_id" not in args, "超时那一单根本没有订单号"
        assert args["idempotency_key"] == "idem-9"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_order_status_is_read_only_and_needs_no_confirmation():
    a, _ = await _agent(reply={"ok": True, "text": "查到了", "data": {}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="DC1")])
    try:
        res = await run_handle(a, "shop.order_status", raw_text="我那杯咖啡好了吗")
        assert res.status == "ok", "只读不该触发确认闸"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(("server_id", "intent_name", "tool_name", "payload", "expected_status"), [
    (
        "mcdonalds", "mcd.order_status", "query-order",
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "订单已取消", "status": "60",
            "realTotalAmount": "26.50", "storeName": "测试餐厅",
        }},
        "订单已取消",
    ),
    (
        "luckin", "luckin.order_status", "queryOrderDetailInfo",
        {"success": True, "code": 0, "data": {
            "orderId": "LUCKIN-1", "orderStatus": 100,
            "orderStatusName": "已取消", "orderPayAmount": 16.6,
            "shopInfo": {"deptName": "测试门店"},
        }},
        "已取消",
    ),
])
async def test_official_order_status_is_deterministically_normalized_without_llm(
        server_id, intent_name, tool_name, payload, expected_status):
    """商户明确终态不能交给 LLM 猜；真栈曾把两家“已取消”说成没查到。"""
    from agents.mcp_bridge.src.agent import _Binding

    a, fake = await _agent(reply={"ok": True, "text": "provider documentation",
                                  "data": payload})
    fake.server_info = {"name": server_id, "version": "1"}
    server = next(spec for spec in load_servers(SERVERS_YAML)
                  if spec.id == server_id)
    tool = next(candidate for candidate in server.tools
                if candidate.name == tool_name and
                candidate.intent == intent_name)
    a._bindings = {
        intent_name: _Binding(server=server, tool=tool, client=fake,
                              input_schema={"required": ["orderId"]})}
    try:
        res = await run_handle(
            a, intent_name, raw_text=f"查询订单 {server_id}",
            slots={"order_id": payload["data"]["orderId"]},
            meta={"granted_scopes": "merchant.read"})
        assert expected_status in res.speech
        assert "没查到" not in res.speech
        assert res.ui_card["type"] == "mcp_order"
        assert res.ui_card["order_id"] == payload["data"]["orderId"]
        assert res.ui_card["status"] == expected_status
        assert res.data["order_id"] == payload["data"]["orderId"]
        assert res.data["status"] == expected_status
        assert fake.calls == [(tool_name, {"orderId": payload["data"]["orderId"]})]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "expected_error"), [
    (
        {"success": False, "code": 500, "data": {
            "orderId": "MCD-1", "orderStatus": "订单已取消",
            "realTotalAmount": "26.50",
        }},
        "有效状态",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "   ",
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "订单已取消",
            "realTotalAmount": "NaN",
        }},
        "订单金额无效",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "订单已取消",
            "realTotalAmount": "-0.001",
        }},
        "订单金额无效",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "订单已取消",
            "realTotalAmount": "1e25",
        }},
        "订单金额无效",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": 123, "orderStatus": "订单已取消",
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": 100,
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1",
            "orderStatus": "https://evil.invalid/pay?token=RAW-SECRET",
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1", "orderStatus": "已" * 129,
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1",
            "orderStatus": "已取消 手机13800138000",
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
    (
        {"success": True, "code": 200, "data": {
            "orderId": "MCD-1",
            "orderStatus": "已取消 RAW-SECRET-TOKEN",
            "realTotalAmount": "26.50",
        }},
        "订单信息不完整",
    ),
])
async def test_declared_order_result_fails_closed_without_raw_card(
        payload, expected_error):
    """业务拒绝、空白关键字段和非有限金额都不能伪装成有效订单卡。"""
    a, fake = await _agent(reply={
        "ok": True, "text": "provider raw response", "data": payload})
    tool = ToolSpec(
        name="query-order", intent="mcd.order_status", slots=["order_id"],
        arg_map={"order_id": "orderId"}, speech_mode="summarize",
        success_predicate={"success": [True], "code": [200]},
        result_map={
            "order_id": "data.orderId",
            "status": "data.orderStatus",
            "amount_cents": "data.realTotalAmount",
        },
        status_map=_MCD_TEST_STATUS_MAP,
        amount_unit="yuan")
    server = ServerSpec(
        id="mcdonalds", command=[], version="", tools=[tool], demo=False,
        transport="streamable_http")
    a._bindings = {
        "mcd.order_status": _Binding(
            server=server, tool=tool, client=fake,
            input_schema={"required": ["orderId"]})}
    try:
        result = await run_handle(
            a, "mcd.order_status", raw_text="查询订单 MCD-1",
            slots={"order_id": "MCD-1"})
        assert expected_error in result.speech
        assert result.ui_card is None
        assert not result.data
        assert "provider raw response" not in result.speech
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_explicit_numeric_order_id_in_raw_text_overrides_planner_copy():
    """超长纯数字订单号以用户原句为准，不能让 LLM 重抄时改一位。"""
    requested = "1111222233334444555566667777"
    payload = {"success": True, "code": 200, "data": {
        "orderId": requested, "orderStatus": "订单已取消",
        "realTotalAmount": "26.50",
    }}
    a, fake = await _agent(reply={"ok": True, "text": "", "data": payload})
    tool = ToolSpec(
        name="query-order", intent="mcd.order_status", slots=["order_id"],
        arg_map={"order_id": "orderId"},
        success_predicate={"success": [True], "code": [200]},
        result_map={
            "order_id": "data.orderId", "status": "data.orderStatus",
            "amount_cents": "data.realTotalAmount",
        }, status_map=_MCD_TEST_STATUS_MAP)
    server = ServerSpec(
        id="mcdonalds", command=[], version="", tools=[tool], demo=False,
        transport="streamable_http")
    a._bindings = {"mcd.order_status": _Binding(
        server=server, tool=tool, client=fake,
        input_schema={"required": ["orderId"]})}
    try:
        result = await run_handle(
            a, "mcd.order_status", raw_text=f"查询麦当劳订单 {requested}",
            slots={"order_id": "9999000011112222333344445555"})
        assert result.data["order_id"] == requested
        assert fake.calls == [("query-order", {"orderId": requested})]
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_declared_order_result_rejects_provider_order_id_mismatch():
    """商户返回另一笔订单时不得显示或写入卡片。"""
    payload = {"success": True, "code": 200, "data": {
        "orderId": "2222222222222222222", "orderStatus": "已取消",
        "realTotalAmount": "26.50",
    }}
    a, fake = await _agent(reply={"ok": True, "text": "", "data": payload})
    tool = ToolSpec(
        name="query-order", intent="mcd.order_status", slots=["order_id"],
        arg_map={"order_id": "orderId"},
        success_predicate={"success": [True], "code": [200]},
        result_map={
            "order_id": "data.orderId", "status": "data.orderStatus",
            "amount_cents": "data.realTotalAmount",
        }, status_map=_MCD_TEST_STATUS_MAP)
    server = ServerSpec(
        id="mcdonalds", command=[], version="", tools=[tool], demo=False,
        transport="streamable_http")
    a._bindings = {"mcd.order_status": _Binding(
        server=server, tool=tool, client=fake,
        input_schema={"required": ["orderId"]})}
    try:
        result = await run_handle(
            a, "mcd.order_status", raw_text="查询麦当劳订单 1111111111111111111",
            slots={"order_id": "1111111111111111111"})
        assert "订单号不一致" in result.speech
        assert result.ui_card is None and not result.data
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_declared_order_protocol_error_never_reaches_llm_or_card():
    """已声明订单归一的官方响应，即使 isError 也不能把 raw PII 交给 LLM。"""
    raw = "RAW-SECRET-TEXT 手机 13800138000"
    a, fake = await _agent(reply={
        "ok": False, "text": raw,
        "data": {"debug": raw, "orderId": "OTHER"},
    })
    tool = ToolSpec(
        name="query-order", intent="mcd.order_status", slots=["order_id"],
        arg_map={"order_id": "orderId"}, speech_mode="summarize",
        success_predicate={"success": [True], "code": [200]},
        result_map={
            "order_id": "data.orderId", "status": "data.orderStatus",
            "amount_cents": "data.realTotalAmount",
        }, status_map=_MCD_TEST_STATUS_MAP)
    server = ServerSpec(
        id="mcdonalds", command=[], version="", tools=[tool], demo=False,
        transport="streamable_http")
    a._bindings = {"mcd.order_status": _Binding(
        server=server, tool=tool, client=fake,
        input_schema={"required": ["orderId"]})}
    try:
        result = await run_handle(
            a, "mcd.order_status", raw_text="查询麦当劳订单 1111111111111111111",
            slots={"order_id": "1111111111111111111"})
        assert result.speech == "商户暂时无法返回这笔订单的有效状态，请稍后再试。"
        assert raw not in result.speech
        assert result.ui_card is None and not result.data
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_cancel_requires_confirmation_first():
    """取消是写操作——**不做未经用户确认的自动补偿**（M-D 非目标里写死的）。"""
    a, fake = await _agent(reply={"ok": True, "text": "已取消并退款：DC1", "data": {}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="DC1")])
    try:
        res = await run_handle(a, "shop.order_cancel", raw_text="取消我的咖啡订单")
        assert res.status == "need_confirm"
        assert fake.calls == [], "没确认前一个字都不能发给商户"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_cancel_after_confirm_backfills_the_order_id_from_the_ledger():
    a, fake = await _agent(reply={"ok": True, "text": "已取消并退款：DC1",
                                  "data": {"order_id": "DC1", "status": "refunded"}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="DC1")])
    try:
        assert a._bindings["shop.order_cancel"].input_schema["required"] == [
            "order_id"], "回填必须在实时 required 校验前发生"
        res = await run_handle(a, "shop.order_cancel", raw_text="那杯咖啡不要了",
                               meta={"confirmed": "true"})
        assert res.status == "ok"
        assert fake.calls[0][0] == "order.cancel"
        assert fake.calls[0][1]["order_id"] == "DC1"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_cancel_never_targets_an_uncertain_order():
    """`outcome=uncertain` 那单连订单号都没有——拿它去取消等于对着一个不知道
    存不存在的单执行写操作。照常追问，不猜。"""
    a, fake = await _agent(reply={"ok": True, "text": "x", "data": {}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="")])
    try:
        res = await run_handle(a, "shop.order_cancel", raw_text="取消订单",
                               meta={"confirmed": "true"})
        assert res.status == "need_slot" and "order_id" in res.missing_slots
        assert fake.calls == []
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_compensation_is_now_reachable_at_runtime():
    """验收原话：补偿「仅准入期存在性校验、运行期零调用、用户零入口」。
    `order.cancel` 进清单后它才成为真实可达的能力——**声明存在不等于能用**。"""
    a = McpBridgeAgent()
    await a.bootstrap()
    try:
        create = next(b for b in a._bindings.values() if b.tool.name == "order.create")
        assert create.tool.compensate_tool == "order.cancel"
        admitted = {b.tool.name for b in a._bindings.values()}
        assert create.tool.compensate_tool in admitted, "补偿工具自己必须也在准入清单里"
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_missing_slot_prompt_follows_the_slot_not_the_order_flow():
    """取消复用写路径后，追问词不能还是下单的「要点什么？」。

    真栈实测抓到：「取消我的咖啡订单」→「要点什么？」。判据按**槽位名**而不是
    intent——intent 是领域字面量（桥核心的既有铁律），槽位名来自 capability 声明。
    """
    a, fake = await _agent(reply={"ok": True, "text": "x", "data": {}})
    a.ledger = FakeLedger(history=[])
    try:
        res = await run_handle(a, "shop.order_cancel", raw_text="取消订单",
                               meta={"confirmed": "true"})
        assert res.status == "need_slot"
        assert "要点什么" not in res.speech, "那是下单的追问词"
        assert "订单号" in res.speech
        assert fake.calls == []
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_confirm_prompt_is_declared_per_tool_not_hardcoded_as_ordering():
    """**用户正要点头同意的就是这句**——说错动作比说得笨拙严重得多。
    真栈实测抓到：「取消我的咖啡订单」被问成「准备下单：DC03…，确认吗？」。
    动词是领域语义，放声明里；桥核心不认识「下单」和「取消」。"""
    a, fake = await _agent(reply={"ok": True, "text": "x", "data": {}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="DC1")])
    try:
        res = await run_handle(a, "shop.order_cancel", raw_text="取消我的咖啡订单")
        assert res.status == "need_confirm"
        assert "取消" in res.speech and "下单" not in res.speech
        assert "DC1" in res.speech, "确认词要说清楚动的是哪一单"
        assert fake.calls == []
    finally:
        await a.shutdown()


def test_bridge_core_has_no_domain_verbs_in_confirm_wording():
    """源码断言：确认话术的动词只能来自声明。这条防的是「顺手写死一句」——
    那在只有下单一个写工具时永远看不出问题。"""
    import ast
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "src" / "agent.py"
    src = path.read_text(encoding="utf-8")
    # 只看**可执行代码**里的字符串常量——注释里引用这个词是在解释缺陷，不是在犯它
    # （同 `scripts/e2e_contract.py` 的架构守卫：AST 选节点，不做全文 grep）。
    literals = {node.value for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not any("准备下单" in lit for lit in literals),         "下单动词必须来自 servers.yaml 的 confirm_prompt"
    assert "confirm_prompt" in src


def test_every_declared_workflow_constructs_from_the_real_allowlist(monkeypatch):
    """每条 servers.yaml 里声明的 workflow 都必须能用**它自己声明的那几个工具**构造。

    2026-08-13 真栈抓到的形态：`luckin.menu` 只声明两个读工具，而 codec 的
    `__init__` 当时把「非 _cancel 即整套下单工具」写死，于是桥启动期
    `workflow luckin.menu 初始化失败：ValueError`，能力**根本没进 capability 合成**
    ——而单测全绿，因为测试 fixture 递的是全量工具字典。
    本用例把构造参数换成真实清单里那条 workflow 自己的 required_tools，
    这正是当时被短路掉的那一步。
    """
    monkeypatch.delenv("MCD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LUCKIN_MCP_TOKEN", raising=False)
    specs = load_servers(SERVERS_YAML)

    constructed = []
    for spec in specs:
        for workflow in spec.workflows:
            tools_by_name = {
                name: SimpleNamespace(name=name)
                for name in workflow.required_tools
            }
            agent = McpBridgeAgent.__new__(McpBridgeAgent)
            agent._draft_store = None
            agent.ledger = None
            agent._payment = None
            built = agent._make_workflow(
                workflow.handler, spec, workflow, tools_by_name)
            assert built is not None, workflow.intent
            declared = getattr(built, "required_tools", None)
            if declared is not None:      # 瑞幸 codec 把权威固化在实例上
                assert set(declared) == set(workflow.required_tools), (
                    f"{workflow.intent} 的 codec 权威必须等于它自己的声明")
            constructed.append(workflow.intent)

    assert "luckin.menu" in constructed
    assert set(constructed) == {
        "mcd.order", "mcd.menu", "luckin.order", "luckin.order_cancel",
        "luckin.menu"}

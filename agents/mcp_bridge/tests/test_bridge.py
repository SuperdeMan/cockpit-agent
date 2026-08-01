"""受控 MCP 桥（M3 P2）契约测试——准入是安全面，逐条钉死。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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

def test_allowlist_loads_and_locks_version_and_schema():
    specs = load_servers(SERVERS_YAML)
    assert [s.id for s in specs] == ["demo-coffee"]
    s = specs[0]
    assert s.version and s.demo is True and s.trust == "third_party"
    # M-D：查单与取消补进准入清单。`order.cancel` 从一开始就在商户侧存在、也被
    # order.create 声明为 compensate_tool，但从没进过清单——**于是补偿只在准入期被
    # 校验存在性，运行期零调用、用户零入口**（验收原话）。
    assert {t.intent for t in s.tools} == {
        "shop.menu", "shop.order", "shop.order_status", "shop.order_cancel"}
    create = next(t for t in s.tools if t.intent == "shop.order")
    assert create.require_confirm and create.compensate_tool and create.idempotency_key_arg
    assert create.arg_map.get("item") == "sku", "槽位词表与商户参数名解耦要有映射"
    cancel = next(t for t in s.tools if t.intent == "shop.order_cancel")
    assert cancel.write and cancel.require_confirm, "取消是写操作，必须走确认闸"
    assert cancel.compensate_tool, "写操作缺补偿路径不许准入（对取消自身同样成立）"
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
    spec = ServerSpec(id="x", command=[], version="1", tools=[
        ToolSpec(name="pay", intent="shop.pay", write=True, compensate_tool="")])
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

    async def call_tool(self, name, args, timeout_s=None):
        self.calls.append((name, dict(args)))
        if self._boom:
            raise asyncio.TimeoutError("timeout")
        return self._reply


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
async def test_bootstrap_synthesizes_capabilities_from_allowlist():
    """新增工具 = 改 servers.yaml + 人工审，**不改 Agent 代码**。"""
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
        assert args["_owner_user_id"] == ctx.user_id
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
async def test_write_non_timeout_error_is_reported_as_definite_failure():
    """非超时异常（子进程没起/协议错）= 确定没发出去——按失败说，不装不确定
    （过度不确定会让用户白跑一趟核实）。"""
    class _Boom(FakeClient):
        async def call_tool(self, name, args, timeout_s=None):
            raise RuntimeError("proc not started")

    a, _ = await _agent()
    for b in a._bindings.values():
        b.client = _Boom()
    try:
        res = await run_handle(a, "shop.order", slots={"item": "拿铁"},
                               raw_text="点一杯拿铁", meta={"confirmed": "true"})
        assert "没有发出去" in res.speech
        assert "可能" not in res.speech, "确定失败不得说「可能已受理」"
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


# ── 查单 / 取消 / 补偿（M-D）───────────────────────────────
def _ledger_task(order_id="", idem="idem-1"):
    from agents._sdk.ledger import LedgerTask
    return LedgerTask(task_id="t0", user_id="u1", session_id="s1",
                      agent_id="mcp-bridge", kind="mcp.write",
                      goal="{}", idempotency_key=idem, status="done",
                      result_ref=({"order_id": order_id} if order_id else
                                  {"error": "timeout", "outcome": "uncertain"}))


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

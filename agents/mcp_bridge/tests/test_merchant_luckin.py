from __future__ import annotations

import asyncio
import copy
import json
from collections import defaultdict
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from agents._sdk import NEED_CONFIRM, NEED_SLOT, OK
from agents._sdk.ledger import DONE, FAILED, LedgerTask
from agents.mcp_bridge.src import agent as agent_module
from agents.mcp_bridge.src.admission import ServerSpec, ToolSpec, WorkflowSpec
from agents.mcp_bridge.src.agent import McpBridgeAgent
from agents.mcp_bridge.src.mcp_client import McpError, McpTimeout
from agents.mcp_bridge.src.merchant.luckin import LuckinWorkflow


CTX = SimpleNamespace(
    user_id="user-1", session_id="session-1", vehicle_id="vehicle-1",
    trace_id="trace-1")


def _trusted(*, name_ref="s1.data.items.0.name",
             longitude_ref="s1.data.items.0.lng",
             latitude_ref="s1.data.items.0.lat",
             producer="nearby.search"):
    return {
        "_trusted_slot_refs": json.dumps({
            "store_name": {"ref": name_ref, "producer_intent": producer},
            "store_longitude": {
                "ref": longitude_ref, "producer_intent": producer},
            "store_latitude": {
                "ref": latitude_ref, "producer_intent": producer},
        }, ensure_ascii=False),
        "granted_scopes": "merchant.write,merchant.read",
    }


META = _trusted()


def _ok(data):
    return {
        "ok": True,
        "text": "",
        "data": {"code": 0, "msg": "success", "data": data,
                 "success": True},
    }


SHOP_RESULT = _ok([{
    "deptId": 602825,
    "deptName": "上海迪美购物中心店",
    "longitude": 121.473382,
    "latitude": 31.227806,
    "workStatus": "营业中",
    "distance": 0.29,
}])


def _attrs(*, hot=False, less_sweet=False):
    return [
        {
            "attributeId": 64, "attributeName": "杯型",
            "productSubAttrs": [
                {"attributeId": 365, "attributeName": "大杯",
                 "selected": True, "price": 0, "canSelected": 1},
                {"attributeId": 594, "attributeName": "超大杯",
                 "selected": False, "price": 3, "canSelected": 1},
            ],
        },
        {
            "attributeId": 17, "attributeName": "温度",
            "productSubAttrs": [
                {"attributeId": 57, "attributeName": "冰",
                 "selected": not hot, "price": 0, "canSelected": 1},
                {"attributeId": 56, "attributeName": "热",
                 "selected": hot, "price": 0, "canSelected": 1},
            ],
        },
        {
            "attributeId": 50, "attributeName": "咖啡豆",
            "productSubAttrs": [
                {"attributeId": 379, "attributeName": "意式拼配",
                 "selected": True, "price": 0, "canSelected": 1},
                {"attributeId": 634, "attributeName": "深烘拼配",
                 "selected": False, "price": 0, "canSelected": 1},
                {"attributeId": 774, "attributeName": "埃塞金烘",
                 "selected": False, "price": 0, "canSelected": 1},
            ],
        },
        {
            "attributeId": 113, "attributeName": "咖啡浓度",
            "productSubAttrs": [
                {"attributeId": 706, "attributeName": "默认浓度",
                 "selected": True, "price": 0, "canSelected": 1},
                {"attributeId": 707, "attributeName": "加单份浓缩",
                 "selected": False, "price": 3, "canSelected": 1},
            ],
        },
        {
            "attributeId": 18, "attributeName": "糖度",
            "productSubAttrs": [
                {"attributeId": 60, "attributeName": "标准糖",
                 "selected": False, "price": 0, "canSelected": 1},
                {"attributeId": 112, "attributeName": "少甜",
                 "selected": less_sweet, "price": 0, "canSelected": 1},
                {"attributeId": 59, "attributeName": "少少甜",
                 "selected": False, "price": 0, "canSelected": 1},
                {"attributeId": 254, "attributeName": "微甜",
                 "selected": False, "price": 0, "canSelected": 1},
                {"attributeId": 69, "attributeName": "不另外加糖",
                 "selected": not less_sweet, "price": 0, "canSelected": 1},
            ],
        },
        {
            "attributeId": 16, "attributeName": "奶油",
            "productSubAttrs": [
                {"attributeId": 53, "attributeName": "无奶油",
                 "selected": True, "price": 0, "canSelected": 1},
                {"attributeId": 54, "attributeName": "加奶油",
                 "selected": False, "price": 1, "canSelected": 0},
            ],
        },
    ]


def _product(sku="SP2077-01134", *, hot=False, less_sweet=False,
             name="生椰拿铁（首创）", product_id=1262):
    return {
        "productId": product_id,
        "productName": name,
        "skuCode": sku,
        "productAttrs": _attrs(hot=hot, less_sweet=less_sweet),
        "initialPrice": 20.0,
        "estimatePrice": 16.6,
    }


# Fresh searchProductForMcp shape: search candidates only expose abbreviated
# attribute choices (selected/canSelected are null); all authoritative
# selection work therefore waits for queryProductDetailInfo.
SEARCH_RESULT = _ok([{
    "productId": 1262,
    "productName": "生椰拿铁（首创）",
    "skuCode": "SP2077-01134",
    # 真机形状（2026-08-13）：每条商品带 https 图链，域名 img04.luckincoffeecdn.com
    "pictureUrl": "https://img04.luckincoffeecdn.com/pic/1262.png",
    "productAttrs": [{
        "attributeId": 17, "attributeName": "温度",
        "productSubAttrs": [{
            "attributeId": 57, "attributeName": "冰",
            "selected": None, "price": 0.0, "canSelected": None,
        }],
    }],
    "tags": ["新品"], "initialPrice": 20.0, "estimatePrice": 16.6,
}, {
    "productId": 5328,
    "productName": "冰吸生椰拿铁（首创）",
    "skuCode": "SP3748-00102",
    "productAttrs": [],
    "tags": ["新品"], "initialPrice": 21.0, "estimatePrice": 17.43,
}])
DETAIL_RESULT = _ok(_product())
SWITCH_HOT_RESULT = _ok(_product("SP2077-01090", hot=True))
SWITCH_SUGAR_RESULT = _ok(_product(
    "SP2077-01253", hot=True, less_sweet=True))

PREVIEW_RESULT = _ok({
    "discountPrice": 16.6,
    "shopInfo": {
        "deptId": 602825,
        "deptName": "上海迪美购物中心店",
        "longitude": 121.473382,
        "latitude": 31.227806,
        "workStatus": "营业中",
    },
    "productInfoList": [{
        "productId": 1262,
        "skuCode": "SP2077-01253",
        "name": "生椰拿铁（首创）",
        "amount": 1,
        "additionDesc": "大杯/热/意式拼配/默认浓度/少甜/无奶油",
        "initPrice": 20.0,
        "estimatePrice": 16.6,
        "estimateTotalPrice": 16.6,
    }],
    "couponCodeList": ["SAFE-COUPON-1"],
    "privilegeMoney": 3.4,
    "totalInitialPrice": 20.0,
})

CREATE_RESULT = _ok({
    "orderId": "LUCKIN-001",
    "payUrl": "https://pay.lkcoffee.com/order/secret-value",
})
ORDER_RESULT = _ok({"orderId": "LUCKIN-001", "status": "UNPAID"})
CANCEL_RESULT = _ok({"orderId": "LUCKIN-001", "status": "CANCELLED"})


def _scripts_with_create(create):
    return {
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [DETAIL_RESULT],
        "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
        "previewOrder": [PREVIEW_RESULT, PREVIEW_RESULT],
        "createOrder": [create],
    }


class FakeClient:
    healthy = True
    alive = True

    def __init__(self, scripts):
        self.scripts = {name: list(values) for name, values in scripts.items()}
        self.calls = []
        self.counts = defaultdict(int)

    async def call_tool(self, name, arguments, *, timeout_s,
                        retry_on_session_loss=True):
        self.calls.append((name, copy.deepcopy(arguments),
                           retry_on_session_loss))
        self.counts[name] += 1
        value = self.scripts[name].pop(0)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


class FakeDraftStore:
    def __init__(self):
        self.current = {}
        self.leases = {}
        self.authorization_allowed = True
        self.authorizations = []
        self.releases = []
        self.deleted = set()
        self.pending_deletes = set()

    async def put(self, draft, *, lease_token=""):
        if draft.user_id in self.deleted:
            return False
        if lease_token and self.leases.get(draft.user_id) != lease_token:
            return False
        self.current[(draft.user_id, draft.session_id, draft.merchant)] = draft
        return True

    async def consume_current(self, *, user_id, session_id, merchant,
                              expected_action):
        key = (user_id, session_id, merchant)
        if user_id in self.deleted:
            return None
        draft = self.current.get(key)
        if draft is None or draft.operation != expected_action:
            return None
        draft = self.current.pop(key)
        if draft.operation in {"create", "cancel"}:
            self.leases[draft.user_id] = draft.token
        return draft

    async def consume(self, token, *, user_id, session_id, merchant,
                      expected_action):
        key = (user_id, session_id, merchant)
        if user_id in self.deleted:
            return None
        draft = self.current.get(key)
        if (draft is None or draft.token != token or
                draft.operation != expected_action):
            return None
        draft = self.current.pop(key)
        if draft.operation in {"create", "cancel"}:
            self.leases[draft.user_id] = draft.token
        return draft

    async def authorize(self, token, *, user_id):
        self.authorizations.append((user_id, token))
        return (self.authorization_allowed and
                user_id not in self.deleted and
                self.leases.get(user_id) == token)

    async def release(self, token, *, user_id):
        self.releases.append((user_id, token))
        if self.leases.get(user_id) != token:
            return False
        self.leases.pop(user_id, None)
        return True

    async def delete_owner(self, user_id):
        if user_id in self.leases:
            self.pending_deletes.add(user_id)
            return False
        self.current = {
            key: draft for key, draft in self.current.items()
            if key[0] != user_id
        }
        self.deleted.add(user_id)
        self.pending_deletes.discard(user_id)
        return True

    @asynccontextmanager
    async def operation_hold(self, token, *, user_id):
        held = await self.authorize(token, user_id=user_id)
        try:
            yield held
        finally:
            await self.release(token, user_id=user_id)


class FakeLedger:
    def __init__(self, *, open_result="task", recent=None):
        self.open_result = open_result
        self.recent_tasks = list(recent or [])
        self.opens = []
        self.closes = []

    async def open(self, user_id, session_id, agent_id, kind, goal, **kwargs):
        self.opens.append({
            "user_id": user_id, "session_id": session_id,
            "agent_id": agent_id, "kind": kind, "goal": goal, **kwargs,
        })
        if self.open_result is None:
            return None
        return LedgerTask(
            task_id=f"task-{len(self.opens)}", user_id=user_id,
            session_id=session_id, agent_id=agent_id, kind=kind, goal=goal)

    async def close(self, task_id, status, *, result_ref=None, progress=""):
        self.closes.append(
            (task_id, status, copy.deepcopy(result_ref), progress))
        return True

    async def recent(self, user_id, *, kind="", limit=5):
        return copy.deepcopy(self.recent_tasks[:limit])


class FakePayment:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    async def authorize(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _schema(properties, required):
    return {
        "type": "object",
        "properties": {name: value for name, value in properties.items()},
        "required": list(required),
        "additionalProperties": False,
    }


def _workflow(*, scripts=None, store=None, ledger=None, payment=None,
              pay_url_locator="", pay_url_hosts=None, tool_names=None,
              workflow_intent="luckin.order", amount_locator="",
              amount_unit="yuan", image_hosts=None):
    scripts = scripts or {
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [DETAIL_RESULT],
        "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
        "previewOrder": [PREVIEW_RESULT, PREVIEW_RESULT],
        "createOrder": [CREATE_RESULT],
        "queryOrderDetailInfo": [ORDER_RESULT],
        "cancelOrder": [CANCEL_RESULT],
    }
    client = FakeClient(scripts)
    schemas = {
        "queryShopList": _schema({
            "deptName": {}, "longitude": {}, "latitude": {}},
            ("longitude", "latitude")),
        "searchProductForMcp": _schema(
            {"deptId": {}, "query": {}}, ("deptId", "query")),
        "switchProduct": _schema({
            "deptId": {}, "productId": {}, "skuCode": {},
            "attrOperationParam": {}, "amount": {}},
            ("deptId", "productId", "skuCode", "attrOperationParam",
             "amount")),
        "queryProductDetailInfo": _schema(
            {"deptId": {}, "productId": {}}, ("deptId", "productId")),
        "previewOrder": _schema(
            {"deptId": {}, "productList": {}}, ("deptId", "productList")),
        "createOrder": _schema({
            "deptId": {}, "productList": {}, "longitude": {},
            "latitude": {}, "couponCodeList": {}, "remark": {}},
            ("deptId", "productList", "longitude", "latitude")),
        "queryOrderDetailInfo": _schema(
            {"orderId": {}}, ("orderId",)),
        "cancelOrder": _schema({"orderId": {}}, ("orderId",)),
    }
    if tool_names is None:
        # Mirrors servers.yaml: the create workflow carries cancelOrder as its
        # admitted compensation dependency, but not the separate query tool.
        selected_names = set(schemas) - {"queryOrderDetailInfo"}
    else:
        selected_names = set(tool_names)
    tools = {}
    for name, schema in schemas.items():
        if name not in selected_names:
            continue
        locator = pay_url_locator if name == "createOrder" else ""
        tool = SimpleNamespace(
            name=name, timeout_ms=12000, const_args={},
            pay_url_locator=locator,
            amount_locator=(amount_locator if name == "createOrder" else ""),
            amount_unit=(amount_unit if name == "createOrder" else "yuan"),
            write=name in {"createOrder", "cancelOrder"},
            success_predicate=({"success": [True], "code": [0]}
                               if name in {"createOrder", "cancelOrder"} else {}),
            retry_policy=("never" if name in {"createOrder", "cancelOrder"}
                          else "safe"))
        tools[name] = SimpleNamespace(
            client=client, tool=tool, input_schema=schema)
    server = SimpleNamespace(
        id="luckin", demo=False,
        pay_url_hosts=(list(pay_url_hosts) if pay_url_hosts is not None
                       else []),
        image_hosts=(list(image_hosts) if image_hosts is not None
                     else ["img04.luckincoffeecdn.com"]))
    spec = SimpleNamespace(
        intent=workflow_intent, required_tools=[
            name for name in schemas if name in selected_names])
    workflow = LuckinWorkflow(
        server, spec, tools, store or FakeDraftStore(),
        ledger if ledger is not None else FakeLedger(),
        payment if payment is not None else FakePayment())
    return workflow, client


def _cancel_workflow(**kwargs):
    return _workflow(
        **kwargs,
        tool_names={"queryOrderDetailInfo", "cancelOrder"},
        workflow_intent="luckin.order_cancel")


def _intent(name="luckin.order", **slots):
    values = {
        "item_query": "生椰拿铁（首创）",
        "quantity": "1",
        "store_name": "瑞幸咖啡（迪美购物中心店）",
        "store_longitude": "121.4737",
        "store_latitude": "31.2304",
        "temperature": "热",
        "ice": "",
        "sweetness": "少甜",
        "milk": "",
    }
    values.update(slots)
    return SimpleNamespace(name=name, slots=values)


def test_cancel_workflow_boots_with_only_its_declared_tools():
    """Bootstrap creates one codec per WorkflowSpec, not one all-tools codec."""
    workflow, _ = _workflow(
        tool_names={"queryOrderDetailInfo", "cancelOrder"},
        workflow_intent="luckin.order_cancel")

    assert set(workflow.tools) == {"queryOrderDetailInfo", "cancelOrder"}
    assert workflow._schema_digest()


@pytest.mark.asyncio
async def test_real_bootstrap_injects_exact_cancel_subset(monkeypatch):
    """Even when the server admits extra tools, the workflow gets only two."""
    query = ToolSpec(
        name="queryOrderDetailInfo", intent="luckin.internal.query_order",
        expose=False, required_scopes=["merchant.read"], schema_sha="query-pin")
    cancel = ToolSpec(
        name="cancelOrder", intent="luckin.internal.cancel_order",
        write=True, require_confirm=True, expose=False,
        required_scopes=["merchant.write"], retry_policy="never",
        compensate_policy="terminal", schema_sha="cancel-pin")
    extra = ToolSpec(
        name="createOrder", intent="luckin.internal.create_order",
        write=True, require_confirm=True, expose=False,
        required_scopes=["merchant.write"], retry_policy="never")
    workflow_spec = WorkflowSpec(
        intent="luckin.order_cancel", handler="luckin",
        required_tools=["queryOrderDetailInfo", "cancelOrder"],
        required_scopes=["merchant.read", "merchant.write"])
    server = ServerSpec(
        id="luckin", command=[], version="1", tools=[query, cancel, extra],
        workflows=[workflow_spec])
    schemas = {
        "queryOrderDetailInfo": _schema(
            {"orderId": {}}, ("orderId",)),
        "cancelOrder": _schema({"orderId": {}}, ("orderId",)),
        "createOrder": _schema({"deptId": {}}, ("deptId",)),
    }

    class BootstrapClient:
        healthy = True
        alive = True
        server_info = {"name": "luckin", "version": "1"}

        async def start(self):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            return []

        async def close(self):
            return None

    client = BootstrapClient()
    admitted = [(tool, schemas[tool.name]) for tool in server.tools]
    monkeypatch.setattr(agent_module, "load_servers", lambda _: [server])
    monkeypatch.setattr(agent_module, "check_version", lambda *_: "")
    monkeypatch.setattr(agent_module, "admit", lambda *_: (admitted, []))
    monkeypatch.setattr(
        McpBridgeAgent, "_make_client", staticmethod(lambda _: client))
    agent = McpBridgeAgent(draft_store=FakeDraftStore())

    await agent.bootstrap()

    assert not agent.rejections
    booted = agent._workflow_bindings["luckin.order_cancel"].workflow
    assert set(booted.tools) == {"queryOrderDetailInfo", "cancelOrder"}
    assert booted.required_tools == (
        "queryOrderDetailInfo", "cancelOrder")
    assert booted._schema_digest()


@pytest.mark.asyncio
async def test_prepare_uses_trusted_nearby_store_and_last_switched_sku():
    store = FakeDraftStore()
    workflow, client = _workflow(store=store)

    result = await workflow.prepare(_intent(), CTX, META)

    assert result.status == NEED_CONFIRM
    assert result.ui_card == {
        "type": "merchant_order_preview",
        "merchant": "luckin",
        "store_name": "上海迪美购物中心店",
        "items": [{
            "name": "生椰拿铁（首创）", "quantity": 1,
            "specifications": ["大杯", "热", "意式拼配", "默认浓度", "少甜", "无奶油"],
            "amount_cents": 1660,
        }],
        "fulfillment": "到店自取",
        "original_amount_cents": 2000,
        "discount_cents": 340,
        "amount_cents": 1660,
        "currency": "CNY",
        "confirmation_context": "merchant_create",
        "buttons": [],
    }
    assert "确认后只创建未支付订单" in result.speech
    assert client.counts["createOrder"] == 0
    assert [name for name, _, _ in client.calls] == [
        "queryShopList", "searchProductForMcp", "queryProductDetailInfo",
        "switchProduct", "switchProduct", "previewOrder",
    ]
    switches = [args for name, args, _ in client.calls
                if name == "switchProduct"]
    assert [args["skuCode"] for args in switches] == [
        "SP2077-01134", "SP2077-01090"]
    assert [args["attrOperationParam"] for args in switches] == [
        {"attributeId": 17,
         "subAttr": {"attributeId": 56, "operation": 1}},
        {"attributeId": 18,
         "subAttr": {"attributeId": 112, "operation": 1}},
    ]
    preview_args = client.calls[-1][1]
    assert preview_args == {
        "deptId": 602825,
        "productList": [{
            "amount": 1, "productId": 1262,
            "skuCode": "SP2077-01253",
        }],
    }
    draft = store.current[("user-1", "session-1", "luckin")]
    assert draft.upstream_args == {
        "deptId": 602825,
        "productList": [{
            "amount": 1, "productId": 1262,
            "skuCode": "SP2077-01253",
        }],
        "longitude": 121.473382,
        "latitude": 31.227806,
        "couponCodeList": ["SAFE-COUPON-1"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [
    {},
    _trusted(producer="weather.get"),
    _trusted(latitude_ref="s1.data.items.1.lat"),
    _trusted(longitude_ref="s2.data.items.0.lng"),
])
async def test_store_coordinates_require_same_nearby_result_item(meta):
    workflow, client = _workflow()

    result = await workflow.prepare(_intent(), CTX, meta)

    # **不得挂起**：门店三元组按设计只能来自 nearby.search 的可信 POI，
    # 把它声明成 missing_slots 就是向用户要一个他永远给不出的东西，
    # 而引擎会据此挂起会话、吞掉后续每一句（2026-08-13 真栈实证
    # demo-f1hkwr：「看麦当劳(科苑南路餐厅)的详情」被答成「请先查询附近的瑞幸门店…」）。
    assert result.status == OK
    assert not result.missing_slots
    assert "附近" in result.speech and result.follow_up
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_store", [
    # Same name in another city: coordinates contradict the trusted POI.
    {**SHOP_RESULT["data"]["data"][0],
     "longitude": 116.3974, "latitude": 39.9093, "distance": 0.2},
    # Coordinates are close, but the official distance claim is not.
    {**SHOP_RESULT["data"]["data"][0], "distance": 88.0},
])
async def test_official_store_must_be_geographically_consistent_with_trusted_poi(
        bad_store):
    workflow, client = _workflow(
        scripts={"queryShopList": [_ok([bad_store])]})

    result = await workflow.prepare(_intent(), CTX, META)

    assert "重新选择门店" in result.speech or "没有找到" in result.speech
    assert [name for name, _, _ in client.calls] == ["queryShopList"]


@pytest.mark.asyncio
async def test_multiple_or_closed_stores_stop_before_product_lookup():
    choices = _ok([
        {"deptId": 1, "deptName": "上海迪美一店", "longitude": 121.4730,
         "latitude": 31.2300, "workStatus": "营业中", "distance": 0.08},
        {"deptId": 2, "deptName": "上海迪美二店", "longitude": 121.4745,
         "latitude": 31.2304, "workStatus": "营业中", "distance": 0.08},
        {"deptId": 3, "deptName": "上海迪美三店", "longitude": 121.4737,
         "latitude": 31.2313, "workStatus": "营业中", "distance": 0.10},
        {"deptId": 4, "deptName": "上海迪美四店", "longitude": 121.4728,
         "latitude": 31.2304, "workStatus": "营业中", "distance": 0.09},
    ])
    workflow, client = _workflow(scripts={"queryShopList": [choices]})

    result = await workflow.prepare(
        _intent(store_name="瑞幸咖啡（迪美店）"), CTX, META)

    assert result.status == NEED_SLOT
    assert result.missing_slots == ["store_name"]
    assert result.ui_card["choice_kind"] == "store"
    assert len(result.ui_card["items"]) == 3
    assert [name for name, _, _ in client.calls] == ["queryShopList"]

    closed = _ok([{**SHOP_RESULT["data"]["data"][0],
                   "workStatus": "已打烊"}])
    closed_flow, closed_client = _workflow(
        scripts={"queryShopList": [closed]})
    closed_result = await closed_flow.prepare(_intent(), CTX, META)
    assert "打烊" in closed_result.speech
    assert [name for name, _, _ in closed_client.calls] == ["queryShopList"]


@pytest.mark.asyncio
async def test_menu_is_read_only_and_never_touches_write_tools():
    """只读看菜单：走完整门店可信链，但**只调两个读工具**、不建草稿。

    这条能力存在的理由是 trace 9899486a8773d577——「这家店的菜单」当时只能落到
    演示商户的 shop.menu，于是真实门店的问句答出了「（演示商户）拿铁 22 元起」。
    """
    workflow, client = _workflow()

    result = await workflow.menu(_intent(name="luckin.menu"), CTX, META)

    assert result.status == OK          # 只读 → 不进确认闸、不挂起
    assert [name for name, _, _ in client.calls] == [
        "queryShopList", "searchProductForMcp"]
    # 真机取证：query="" 被商户判 code=1000 非法参数 → 无 item_query 时必须给种子实词
    search_args = next(args for name, args, _ in client.calls
                       if name == "searchProductForMcp")
    assert str(search_args.get("query") or "").strip(), "空 query 会被商户拒"
    # 一次最多 3 条，话术不许说成「全部菜单」
    assert "菜单" not in result.speech and "全部" not in result.speech
    card = result.ui_card
    assert card["type"] == "merchant_choices"
    assert card["choice_kind"] == "product"
    assert card["merchant"] == "瑞幸"
    assert "生椰拿铁（首创）" in [item["name"] for item in card["items"]]
    # 价格取真机字段 estimatePrice（到手价），不是猜的键名
    first = next(i for i in card["items"] if i["name"] == "生椰拿铁（首创）")
    assert first["price"] == "16.60 元"
    # 演示商户角标绝不该出现在真实商户的菜单上
    assert "演示" not in result.speech


@pytest.mark.asyncio
async def test_menu_requires_the_same_trusted_store_chain_as_ordering():
    """没有可信 POI 就不给菜单——官方菜单绑定 deptId，绕不开选店这一步。"""
    workflow, client = _workflow()

    result = await workflow.menu(_intent(name="luckin.menu"), CTX, meta={})

    assert result.status == OK and not result.missing_slots, "不得挂起会话"
    assert "附近" in result.speech and result.follow_up
    assert client.calls == []


@pytest.mark.asyncio
async def test_store_refusals_never_suspend_the_session():
    """门店类拒绝**不得挂起**：它要的槽用户填不了。

    2026-08-12 曾把这些拒绝改成 NEED_SLOT，为的是躲开中央确认闸追加的
    「确定继续吗？」；2026-08-13 真栈证明那是**更糟的一换**——
    NEED_SLOT 声明缺 store_name/lng/lat，而这三个槽按设计只能来自 nearby.search
    的可信 POI，用户永远填不了，于是会话挂起、后续每一句被吞
    （demo-f1hkwr：问麦当劳详情答瑞幸；demo-r6qjf4：「第一个」「选择瑞幸门店：X」全被吞）。
    话术不好看 vs 功能不可用，两害相权。
    """
    far_away = _ok([{**SHOP_RESULT["data"]["data"][0],
                     "longitude": 116.4074, "latitude": 39.9042}])
    # 按名查空会触发一次「只用坐标」重查（见 _resolve_store），故该用例排两条空响应
    cases = {
        "no_official_store": ([_ok([]), _ok([])], 2),
        "all_closed": ([_ok([{**SHOP_RESULT["data"]["data"][0],
                              "workStatus": "已打烊"}])], 1),
        "poi_mismatch": ([far_away], 1),
        "incomplete_official_store": ([_ok([
            {**SHOP_RESULT["data"]["data"][0], "deptId": 0}])], 1),
    }
    for label, (script, shop_calls) in cases.items():
        workflow, client = _workflow(scripts={"queryShopList": script})
        result = await workflow.prepare(_intent(), CTX, META)

        assert result.status == OK, label
        assert not result.missing_slots, f"{label}：不许声明用户填不了的槽"
        assert result.speech and result.follow_up, label
        # 拒绝必须**在**商品查询之前落定：没门店就不该再碰下游写链
        assert [name for name, _, _ in client.calls] == (
            ["queryShopList"] * shop_calls), label


@pytest.mark.asyncio
async def test_store_choice_resumes_once_from_server_snapshot_without_provenance():
    """Button text selects a server-snapshotted candidate; it is not provenance."""
    choices = _ok([
        {"deptId": 602825, "deptName": "上海迪美购物中心店",
         "longitude": 121.473382, "latitude": 31.227806,
         "workStatus": "营业中", "distance": 0.29},
        {"deptId": 602826, "deptName": "上海迪美东区店",
         "longitude": 121.4742, "latitude": 31.2282,
         "workStatus": "营业中", "distance": 0.35},
    ])
    store = FakeDraftStore()
    workflow, client = _workflow(store=store, scripts={
        "queryShopList": [choices],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [DETAIL_RESULT],
        "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
        "previewOrder": [PREVIEW_RESULT],
    })

    pending = await workflow.prepare(
        _intent(store_name="瑞幸咖啡（迪美店）"), CTX, META)

    assert pending.status == NEED_SLOT
    assert pending.data and pending.data["checkout_token"]
    assert client.counts["queryShopList"] == 1
    # Simulates the next suspended-plan turn: the executor no longer has the
    # original slot-ref provenance and only the natural button choice returns.
    send_text = pending.ui_card["buttons"][0]["send_text"]
    resumed = await workflow.prepare(
        SimpleNamespace(name="luckin.order", slots={
            "store_name": send_text}),
        CTX, {"granted_scopes": "merchant.write"})

    assert resumed.status == NEED_CONFIRM
    assert resumed.ui_card["store_name"] == "上海迪美购物中心店"
    assert client.counts["queryShopList"] == 1
    assert client.counts["searchProductForMcp"] == 1

    # The selection continuation was atomically consumed.  Replaying the same
    # untrusted button text cannot authorize another merchant read/write flow.
    calls_before = len(client.calls)
    replay = await workflow.prepare(
        SimpleNamespace(name="luckin.order", slots={
            "store_name": "上海迪美购物中心店"}),
        CTX, {"granted_scopes": "merchant.write"})
    assert replay.status == NEED_SLOT
    assert len(client.calls) == calls_before


@pytest.mark.asyncio
async def test_store_choice_rejects_name_not_in_server_snapshot():
    choices = _ok([
        {"deptId": 602825, "deptName": "上海迪美购物中心店",
         "longitude": 121.473382, "latitude": 31.227806,
         "workStatus": "营业中", "distance": 0.29},
        {"deptId": 602826, "deptName": "上海迪美东区店",
         "longitude": 121.4742, "latitude": 31.2282,
         "workStatus": "营业中", "distance": 0.25},
    ])
    workflow, client = _workflow(
        store=FakeDraftStore(), scripts={"queryShopList": [choices]})
    await workflow.prepare(
        _intent(store_name="瑞幸咖啡（迪美店）"), CTX, META)
    calls_before = len(client.calls)

    result = await workflow.prepare(
        SimpleNamespace(name="luckin.order", slots={
            "store_name": "北京三里屯店"}),
        CTX, {"granted_scopes": "merchant.write"})

    assert result.status == NEED_SLOT
    assert "重新查询" in result.speech
    assert len(client.calls) == calls_before


@pytest.mark.asyncio
async def test_multiple_products_and_unavailable_spec_require_user_choice():
    many = _ok([
        _product(name="生椰拿铁"),
        _product("SKU-2", name="冰吸生椰拿铁", product_id=2),
        _product("SKU-3", name="生椰丝绒拿铁", product_id=3),
        _product("SKU-4", name="厚乳生椰拿铁", product_id=4),
    ])
    workflow, client = _workflow(scripts={
        "queryShopList": [SHOP_RESULT], "searchProductForMcp": [many]})
    result = await workflow.prepare(_intent(item_query="拿铁"), CTX, META)
    assert result.status == NEED_SLOT
    assert result.missing_slots == ["item_query"]
    assert result.ui_card["choice_kind"] == "product"
    assert len(result.ui_card["items"]) == 3
    assert client.counts["queryProductDetailInfo"] == 0

    # These colloquial requests are not present in this product's fresh
    # official attribute tree.  They must be rejected, not mapped to a nearby
    # option or synthesized by the codec.
    for slot, value in (("ice", "少冰"), ("sweetness", "半糖"),
                        ("milk", "燕麦奶")):
        unsupported, unsupported_client = _workflow()
        unavailable = await unsupported.prepare(
            _intent(**{slot: value}), CTX, META)
        assert unavailable.status == NEED_SLOT
        assert unavailable.missing_slots == [slot]
        assert value in unavailable.speech
        assert unsupported_client.counts["previewOrder"] == 0
        assert unsupported_client.counts["createOrder"] == 0

    sold_out = copy.deepcopy(SEARCH_RESULT["data"]["data"][0])
    sold_out["soldOut"] = True
    sold_out_flow, sold_out_client = _workflow(scripts={
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [_ok([sold_out])],
    })
    unavailable = await sold_out_flow.prepare(_intent(), CTX, META)
    assert unavailable.status == NEED_SLOT
    assert "没有找到可售" in unavailable.speech
    assert sold_out_client.counts["queryProductDetailInfo"] == 0

    blocked_detail = copy.deepcopy(DETAIL_RESULT)
    hot = blocked_detail["data"]["data"]["productAttrs"][1][
        "productSubAttrs"][1]
    assert hot["attributeName"] == "热"
    hot["canSelected"] = 0
    blocked_flow, blocked_client = _workflow(scripts={
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [blocked_detail],
    })
    blocked = await blocked_flow.prepare(
        _intent(temperature="热", sweetness=""), CTX, META)
    assert blocked.status == NEED_SLOT
    assert blocked.missing_slots == ["temperature"]
    assert blocked_client.counts["switchProduct"] == 0


@pytest.mark.asyncio
async def test_preview_business_error_and_bad_quantity_fail_closed():
    bad_flow, bad_client = _workflow(scripts={
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [DETAIL_RESULT],
        "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
        "previewOrder": [{"ok": True, "data": {
            "success": False, "code": 4102, "data": {}}}],
    })
    result = await bad_flow.prepare(_intent(), CTX, META)
    assert "没有创建订单" in result.speech
    assert bad_client.counts["createOrder"] == 0

    for value in ("0", "21", "1.5", "abc"):
        flow, client = _workflow()
        invalid = await flow.prepare(_intent(quantity=value), CTX, META)
        assert invalid.status == NEED_SLOT
        assert invalid.missing_slots == ["quantity"]
        assert not client.calls


@pytest.mark.asyncio
async def test_confirm_consumes_snapshot_and_creates_exactly_once_without_retry():
    store = FakeDraftStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    prepared = await workflow.prepare(_intent(), CTX, META)

    confirmed = await workflow.confirm(
        _intent(checkout_token=prepared.data["checkout_token"]),
        CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])
    duplicate = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"})

    assert client.counts["createOrder"] == 1
    create_call = next(call for call in client.calls
                       if call[0] == "createOrder")
    assert create_call[2] is False
    assert create_call[1] == {
        "deptId": 602825,
        "productList": [{"amount": 1, "productId": 1262,
                         "skuCode": "SP2077-01253"}],
        "longitude": 121.473382,
        "latitude": 31.227806,
        "couponCodeList": ["SAFE-COUPON-1"],
    }
    assert confirmed.data == {
        "server": "luckin", "merchant": "luckin",
        "order_id": "LUCKIN-001", "status": "created",
        "amount_cents": 1660, "store_name": "上海迪美购物中心店",
    }
    assert confirmed.ui_card["type"] == "mcp_order"
    assert confirmed.ui_card["buttons"] == [
        {"label": "查订单", "send_text": "查询瑞幸订单 LUCKIN-001"},
        {"label": "取消订单", "send_text": "取消瑞幸订单 LUCKIN-001"},
    ]
    assert "pay.lkcoffee.com" not in repr(confirmed)
    assert "secret-value" not in repr(confirmed)
    assert ledger.closes == [(
        "task-1", DONE, confirmed.data, "瑞幸未支付订单已创建")]
    assert "预览已失效" in duplicate.speech


@pytest.mark.asyncio
async def test_create_confirm_requires_live_owner_lease_before_preview_ledger_or_write():
    store = FakeDraftStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    token = preview.data["checkout_token"]
    store.authorization_allowed = False

    result = await workflow.confirm(
        _intent(checkout_token=token), CTX,
        {**META, "confirmed": "true"}, token=token)

    assert "失效" in result.speech or "无法安全" in result.speech
    assert client.counts["previewOrder"] == 1
    assert client.counts["createOrder"] == 0
    assert ledger.opens == []
    assert store.authorizations == [("user-1", token)]
    assert store.releases == [("user-1", token)]
    assert store.leases == {}


@pytest.mark.asyncio
async def test_create_privacy_delete_wins_with_zero_ledger_and_remote_write():
    store = FakeDraftStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    assert await store.delete_owner("user-1") is True

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"])

    assert "失效" in result.speech
    assert client.counts["createOrder"] == 0
    assert ledger.opens == []


@pytest.mark.asyncio
async def test_create_operation_wins_delete_nacks_then_retries_after_release():
    class PausingStore(FakeDraftStore):
        def __init__(self):
            super().__init__()
            self.before_write = asyncio.Event()
            self.continue_write = asyncio.Event()

        async def authorize(self, token, *, user_id):
            allowed = await super().authorize(token, user_id=user_id)
            if allowed and len(self.authorizations) == 3:
                self.before_write.set()
                await self.continue_write.wait()
            return allowed

    store = PausingStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    operation = asyncio.create_task(workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"]))
    await asyncio.wait_for(store.before_write.wait(), timeout=1)

    assert await store.delete_owner("user-1") is False
    store.continue_write.set()
    result = await asyncio.wait_for(operation, timeout=1)
    assert result.data["status"] == "created"
    assert client.counts["createOrder"] == 1
    assert len(ledger.opens) == 1
    assert store.leases == {}
    assert await store.delete_owner("user-1") is True


@pytest.mark.asyncio
async def test_verified_create_shields_done_close_then_preserves_cancellation():
    class CancelOnceLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.completed = False

        async def close(self, task_id, status, *, result_ref=None, progress=""):
            self.closes.append(
                (task_id, status, copy.deepcopy(result_ref), progress))
            await self.release.wait()
            self.completed = True
            return True

    ledger = CancelOnceLedger()
    store = FakeDraftStore()
    workflow, client = _workflow(ledger=ledger, store=store)
    preview = await workflow.prepare(_intent(), CTX, META)
    operation = asyncio.create_task(workflow.confirm(
        _intent(checkout_token=preview.data["checkout_token"]), CTX,
        {**META, "confirmed": "true"},
        token=preview.data["checkout_token"]))
    while not ledger.closes:
        await asyncio.sleep(0)
    operation.cancel()
    ledger.release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation
    assert client.counts["createOrder"] == 1
    assert ledger.closes[-1][1] == DONE
    assert ledger.completed is True
    assert store.leases == {}


@pytest.mark.asyncio
async def test_confirm_reprices_and_requires_fresh_confirmation_on_any_change():
    changed = copy.deepcopy(PREVIEW_RESULT)
    payload = changed["data"]["data"]
    payload["discountPrice"] = 17.6
    payload["privilegeMoney"] = 2.4
    payload["productInfoList"][0]["estimatePrice"] = 17.6
    payload["productInfoList"][0]["estimateTotalPrice"] = 17.6
    store = FakeDraftStore()
    workflow, client = _workflow(store=store, scripts={
        "queryShopList": [SHOP_RESULT],
        "searchProductForMcp": [SEARCH_RESULT],
        "queryProductDetailInfo": [DETAIL_RESULT],
        "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
        "previewOrder": [PREVIEW_RESULT, changed],
        "createOrder": [CREATE_RESULT],
    })
    prepared = await workflow.prepare(_intent(), CTX, META)

    refreshed = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert refreshed.status == NEED_CONFIRM
    assert refreshed.ui_card["amount_cents"] == 1760
    assert "价格或优惠已变化" in refreshed.speech
    assert refreshed.data["checkout_token"] != prepared.data["checkout_token"]
    assert client.counts["previewOrder"] == 2
    assert client.counts["createOrder"] == 0
    assert store.leases == {}
    fresh = await store.consume(
        refreshed.data["checkout_token"], user_id="user-1",
        session_id="session-1", merchant="luckin",
        expected_action="create")
    assert fresh is not None
    assert await store.release(fresh.token, user_id=fresh.user_id) is True


@pytest.mark.asyncio
async def test_schema_change_or_missing_ledger_never_calls_create():
    workflow, client = _workflow()
    prepared = await workflow.prepare(_intent(), CTX, META)
    workflow.tools["createOrder"].input_schema["properties"]["requiredNew"] = {}
    workflow.tools["createOrder"].input_schema["required"].append("requiredNew")
    changed = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])
    assert "接口已更新" in changed.speech
    assert client.counts["createOrder"] == 0

    denied_flow, denied_client = _workflow(
        ledger=FakeLedger(open_result=None))
    denied_preview = await denied_flow.prepare(_intent(), CTX, META)
    denied = await denied_flow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=denied_preview.data["checkout_token"])
    assert "安全受理" in denied.speech
    assert denied_client.counts["createOrder"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    McpTimeout("write timeout", sent=True),
    McpError("remote protocol failure after request delivery"),
    ConnectionError("connection lost after request delivery"),
    {"ok": True, "data": {"code": 0,
                            "data": {"orderId": "LUCKIN-UNKNOWN"}}},
    {"ok": True, "data": {"success": True,
                            "data": {"orderId": "LUCKIN-UNKNOWN"}}},
    _ok({"payUrl": "https://pay.lkcoffee.com/order/no-id"}),
])
async def test_unknown_create_outcome_is_not_retried_and_never_leaks_url(failure):
    ledger = FakeLedger()
    workflow, client = _workflow(
        ledger=ledger,
        scripts={
            "queryShopList": [SHOP_RESULT],
            "searchProductForMcp": [SEARCH_RESULT],
            "queryProductDetailInfo": [DETAIL_RESULT],
            "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
            "previewOrder": [PREVIEW_RESULT, PREVIEW_RESULT],
            "createOrder": [failure],
        })
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert client.counts["createOrder"] == 1
    assert next(call for call in client.calls
                if call[0] == "createOrder")[2] is False
    assert "可能" in result.speech and "不要重复" in result.speech
    assert "http" not in repr(result)
    assert "evil.invalid" not in repr(result)
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "luckin", "merchant": "luckin",
         "error": "mcp_uncertain", "outcome": "uncertain"},
        "瑞幸下单结果不确定")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"ok": True, "data": {"success": False, "code": 5001,
                            "data": {"payUrl": "https://evil.invalid/x"}}},
    McpTimeout("connect timeout", sent=False),
])
async def test_known_create_rejection_is_failed_not_uncertain(failure):
    ledger = FakeLedger()
    workflow, client = _workflow(
        ledger=ledger,
        scripts={
            "queryShopList": [SHOP_RESULT],
            "searchProductForMcp": [SEARCH_RESULT],
            "queryProductDetailInfo": [DETAIL_RESULT],
            "switchProduct": [SWITCH_HOT_RESULT, SWITCH_SUGAR_RESULT],
            "previewOrder": [PREVIEW_RESULT, PREVIEW_RESULT],
            "createOrder": [failure],
        })
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert client.counts["createOrder"] == 1
    assert "可能" not in result.speech
    assert "没有受理" in result.speech
    assert "http" not in repr(result)
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "luckin", "merchant": "luckin",
         "error": "mcp_rejected", "outcome": "failed"},
        "瑞幸下单未受理")]


@pytest.mark.asyncio
async def test_pay_locator_without_audited_create_amount_locator_never_registers():
    payment = FakePayment(SimpleNamespace(payment_id="PAY-1", qr_svg=""))
    workflow, _ = _workflow(
        payment=payment, pay_url_locator="data.payUrl",
        pay_url_hosts=["pay.lkcoffee.com"])
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert not payment.calls
    assert result.ui_card["type"] == "mcp_order"
    assert result.ui_card["order_id"] == "LUCKIN-001"
    assert result.ui_card["buttons"] == [
        {"label": "查订单", "send_text": "查询瑞幸订单 LUCKIN-001"},
        {"label": "取消订单", "send_text": "取消瑞幸订单 LUCKIN-001"},
    ]
    assert "官方应用" in result.speech
    assert "http" not in repr(result.data)

    blocked_payment = FakePayment(SimpleNamespace(payment_id="PAY-2", qr_svg=""))
    blocked, _ = _workflow(
        payment=blocked_payment, pay_url_locator="data.payUrl",
        pay_url_hosts=["not-luckin.example"])
    blocked_preview = await blocked.prepare(_intent(), CTX, META)
    blocked_result = await blocked.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=blocked_preview.data["checkout_token"])
    assert not blocked_payment.calls
    assert blocked_result.ui_card["type"] == "mcp_order"
    assert "pay.lkcoffee.com" not in repr(blocked_result)


@pytest.mark.asyncio
async def test_real_create_locators_bind_payment_and_prefer_string_order_id():
    create = _ok({
        "orderId": 123456,
        "orderIdStr": "LUCKIN-STR-001",
        "discountPrice": 16.6,
        "payOrderQrCodeUrl":
            "https://open.lkcoffee.com/order/secret-value",
        "payOrderUrl": "weixin://wxpay/bizpayurl?secret=value",
    })
    payment = FakePayment(SimpleNamespace(payment_id="PAY-1", qr_svg=""))
    workflow, _ = _workflow(
        scripts=_scripts_with_create(create), payment=payment,
        pay_url_locator="data.payOrderQrCodeUrl",
        amount_locator="data.discountPrice", amount_unit="yuan",
        pay_url_hosts=["open.lkcoffee.com"])
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert result.data["order_id"] == "LUCKIN-STR-001"
    assert result.ui_card["type"] == "payment_qr"
    assert len(payment.calls) == 1
    assert payment.calls[0]["amount_cents"] == 1660
    assert payment.calls[0]["external_pay_url"].startswith(
        "https://open.lkcoffee.com/")


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_url", [
    "https://attacker.invalid@pay.lkcoffee.com/order/secret",
    "https://pay.lkcoffee.com:8443/order/secret",
    " https://pay.lkcoffee.com/order/secret ",
    "https://pay.lkcoffee.com/order/secret\nvalue",
    "https://pay.lkcoffee.com/order/secret value",
    "https://pay.lkcoffee.com/order/secret\x7fvalue",
])
async def test_pay_url_rejects_userinfo_nonstandard_port_and_whitespace(
        unsafe_url):
    create = _ok({
        "orderId": "LUCKIN-001", "payUrl": unsafe_url, "amount": 16.6,
    })
    payment = FakePayment(SimpleNamespace(payment_id="PAY-1", qr_svg=""))
    workflow, _ = _workflow(
        scripts=_scripts_with_create(create), payment=payment,
        pay_url_locator="data.payUrl", amount_locator="data.amount",
        pay_url_hosts=["pay.lkcoffee.com"])
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert payment.calls == []
    assert result.ui_card["type"] == "mcp_order"
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_payment_cancellation_preserves_done_order_and_propagates():
    create = _ok({
        "orderId": "LUCKIN-001",
        "payUrl": "https://pay.lkcoffee.com/order/secret",
        "amount": 16.6,
    })
    ledger = FakeLedger()
    payment = FakePayment(asyncio.CancelledError())
    workflow, client = _workflow(
        scripts=_scripts_with_create(create), ledger=ledger, payment=payment,
        pay_url_locator="data.payUrl", amount_locator="data.amount",
        pay_url_hosts=["pay.lkcoffee.com"])
    prepared = await workflow.prepare(_intent(), CTX, META)

    with pytest.raises(asyncio.CancelledError):
        await workflow.confirm(
            _intent(), CTX, {**META, "confirmed": "true"},
            token=prepared.data["checkout_token"])

    assert client.counts["createOrder"] == 1
    assert ledger.closes and ledger.closes[-1][1] == DONE
    assert len(payment.calls) == 1


def _recent_order(order_id="LUCKIN-001", merchant="luckin", status="created",
                  user_id="user-1", session_id="session-1",
                  task_status=DONE, outcome="", include_order_id=True,
                  goal="order"):
    result_ref = {
        "server": "luckin", "merchant": merchant,
        "status": status,
        "amount_cents": 1660,
        "store_name": "上海迪美购物中心店",
    }
    if include_order_id:
        result_ref["order_id"] = order_id
    if outcome:
        result_ref["outcome"] = outcome
    return LedgerTask(
        task_id=f"task-{order_id}-{session_id}", user_id=user_id,
        session_id=session_id,
        agent_id="luckin", kind="mcp_order", goal=goal,
        status=task_status,
        result_ref=result_ref)


@pytest.mark.asyncio
async def test_cancel_treats_recent_order_reference_as_missing_order_id():
    """真栈形态：planner 将“刚才的瑞幸订单”放进 order_id，但它不是订单号。"""
    workflow, client = _cancel_workflow(
        ledger=FakeLedger(recent=[_recent_order()]))

    pending = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="刚才的瑞幸订单"),
        CTX, META)

    assert pending.status == NEED_CONFIRM
    assert pending.ui_card["order_id"] == "LUCKIN-001"
    assert client.counts["queryOrderDetailInfo"] == 1
    assert client.counts["cancelOrder"] == 0


@pytest.mark.asyncio
async def test_implicit_cancel_prefers_current_session_owned_order():
    ledger = FakeLedger(recent=[
        _recent_order("OTHER-SESSION", session_id="session-old"),
        _recent_order("CURRENT-SESSION", session_id="session-1"),
    ])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={"queryOrderDetailInfo": [_ok({
            "orderId": "CURRENT-SESSION", "status": "UNPAID"})]})

    pending = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id=""), CTX, META)

    assert pending.status == NEED_CONFIRM
    assert pending.ui_card["order_id"] == "CURRENT-SESSION"
    assert client.calls[0][1] == {"orderId": "CURRENT-SESSION"}


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [
    [_recent_order(user_id="user-2")],
    [_recent_order(merchant="mcdonalds")],
    [_recent_order(task_status=FAILED, status="failed")],
    [_recent_order(task_status=FAILED, status="uncertain", outcome="uncertain")],
    [_recent_order(task_status="cancelled", status="created")],
    [_recent_order(status="cancelled")],
    # A newer ambiguous/terminal record must tombstone the older create record.
    [_recent_order(task_status=FAILED, status="uncertain", outcome="uncertain",
                   include_order_id=False, goal="LUCKIN-001"),
     _recent_order(status="created")],
    [_recent_order(status="cancelled"), _recent_order(status="created")],
])
async def test_implicit_cancel_never_infers_foreign_or_non_cancellable_order(
        history):
    workflow, client = _cancel_workflow(ledger=FakeLedger(recent=history))

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id=""), CTX, META)

    assert result.status == NEED_SLOT
    assert not client.calls


@pytest.mark.asyncio
async def test_cancel_infers_recent_same_merchant_then_confirms_exactly_once():
    store = FakeDraftStore()
    ledger = FakeLedger(recent=[
        _recent_order("MCD-1", merchant="mcdonalds"), _recent_order()])
    workflow, client = _cancel_workflow(store=store, ledger=ledger)
    intent = _intent(name="luckin.order_cancel", order_id="")

    pending = await workflow.cancel(intent, CTX, META)

    assert pending.status == NEED_CONFIRM
    assert pending.ui_card["confirmation_context"] == "merchant_cancel"
    assert pending.ui_card["order_id"] == "LUCKIN-001"
    assert "取消" in pending.speech and "LUCKIN-001" in pending.speech
    assert client.counts["queryOrderDetailInfo"] == 1
    assert client.counts["cancelOrder"] == 0
    query_call = next(call for call in client.calls
                      if call[0] == "queryOrderDetailInfo")
    assert query_call == (
        "queryOrderDetailInfo", {"orderId": "LUCKIN-001"}, True)

    # McpBridgeAgent routes every *_cancel turn through cancel(); confirmed
    # metadata must dispatch to the one-shot cancellation instead of asking
    # for confirmation forever.
    confirmed = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})
    duplicate = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    cancel_call = next(call for call in client.calls
                       if call[0] == "cancelOrder")
    assert cancel_call == ("cancelOrder", {"orderId": "LUCKIN-001"}, False)
    assert confirmed.data == {
        "server": "luckin", "merchant": "luckin",
        "order_id": "LUCKIN-001", "status": "cancelled",
        "amount_cents": 1660,
        "store_name": "上海迪美购物中心店",
    }
    assert confirmed.ui_card["status"] == "cancelled"
    assert confirmed.ui_card["buttons"] == [{
        "label": "查订单", "send_text": "查询瑞幸订单 LUCKIN-001"}]
    assert len(ledger.opens) == 1
    assert {key: ledger.opens[0][key] for key in (
        "user_id", "session_id", "agent_id", "kind", "goal",
        "origin_trace_id")} == {
        "user_id": "user-1", "session_id": "session-1",
        "agent_id": "luckin", "kind": "mcp_order", "goal": "LUCKIN-001",
        "origin_trace_id": "trace-1",
    }
    assert ledger.opens[0]["idempotency_goal"].startswith("luckin:cancel:")
    assert ledger.closes == [(
        "task-1", DONE, confirmed.data, "瑞幸订单已取消")]
    assert "已失效" in duplicate.speech


@pytest.mark.asyncio
async def test_cancel_confirm_requires_live_owner_lease_before_ledger_or_write():
    store = FakeDraftStore()
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(store=store, ledger=ledger)
    intent = _intent(name="luckin.order_cancel", order_id="")
    pending = await workflow.cancel(intent, CTX, META)
    token = pending.data["checkout_token"]
    store.authorization_allowed = False

    result = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert "失效" in result.speech or "无法安全" in result.speech
    assert client.counts["cancelOrder"] == 0
    assert ledger.opens == []
    assert store.authorizations == [("user-1", token)]
    assert store.releases == [("user-1", token)]
    assert store.leases == {}


@pytest.mark.asyncio
async def test_cancel_privacy_delete_wins_with_zero_ledger_and_remote_write():
    store = FakeDraftStore()
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(store=store, ledger=ledger)
    intent = _intent(name="luckin.order_cancel", order_id="")
    pending = await workflow.cancel(intent, CTX, META)
    assert await store.delete_owner("user-1") is True

    result = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert "失效" in result.speech
    assert client.counts["cancelOrder"] == 0
    assert ledger.opens == []
    assert pending.data["checkout_token"]


@pytest.mark.asyncio
async def test_cancel_operation_wins_delete_nacks_then_retries_after_release():
    class PausingStore(FakeDraftStore):
        def __init__(self):
            super().__init__()
            self.before_write = asyncio.Event()
            self.continue_write = asyncio.Event()

        async def authorize(self, token, *, user_id):
            allowed = await super().authorize(token, user_id=user_id)
            if allowed and len(self.authorizations) == 3:
                self.before_write.set()
                await self.continue_write.wait()
            return allowed

    store = PausingStore()
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(store=store, ledger=ledger)
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)
    operation = asyncio.create_task(workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"}))
    await asyncio.wait_for(store.before_write.wait(), timeout=1)

    assert await store.delete_owner("user-1") is False
    store.continue_write.set()
    result = await asyncio.wait_for(operation, timeout=1)
    assert result.data["status"] == "cancelled"
    assert client.counts["cancelOrder"] == 1
    assert len(ledger.opens) == 1
    assert store.leases == {}
    assert await store.delete_owner("user-1") is True


@pytest.mark.asyncio
async def test_cancel_without_owned_order_or_ledger_fails_closed():
    no_recent, no_recent_client = _cancel_workflow(
        ledger=FakeLedger(recent=[]))
    missing = await no_recent.cancel(
        _intent(name="luckin.order_cancel", order_id=""), CTX, META)
    assert missing.status == NEED_SLOT
    assert missing.missing_slots == ["order_id"]
    assert not no_recent_client.calls

    foreign, foreign_client = _cancel_workflow(ledger=FakeLedger(recent=[
        _recent_order("SOMEONE-ELSE")]))
    rejected = await foreign.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, META)
    assert rejected.status == NEED_SLOT
    assert "归属" in rejected.speech
    assert not foreign_client.calls

    explicit, explicit_client = _cancel_workflow(ledger=FakeLedger(
        open_result=None, recent=[_recent_order()]))
    pending = await explicit.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, META)
    assert pending.status == NEED_CONFIRM
    denied = await explicit.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, {**META, "confirmed": "true"})
    assert "安全受理" in denied.speech
    assert explicit_client.counts["cancelOrder"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [
    # A defensive fake returning another user's exact order must not bypass
    # TaskLedger.recent(user_id=...) ownership filtering.
    [_recent_order(user_id="user-2")],
    [_recent_order(merchant="mcdonalds")],
    [_recent_order(status="paid")],
    [_recent_order(status="completed")],
    [_recent_order(status="cancelled")],
    [_recent_order(task_status=FAILED)],
    # Newest terminal state tombstones an older create record for the same id.
    [_recent_order(status="cancelled"), _recent_order(status="created")],
])
async def test_explicit_cancel_requires_owned_cancellable_ledger_state(history):
    workflow, client = _cancel_workflow(
        ledger=FakeLedger(recent=history))

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, META)

    assert result.status == NEED_SLOT
    assert "归属" in result.speech
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("query_payload", [
    {"orderId": "OTHER-ORDER", "status": "UNPAID"},
    {"orderId": "LUCKIN-001", "status": "PAID"},
    {"orderId": "LUCKIN-001", "status": "COMPLETED"},
    {"orderId": "LUCKIN-001", "status": "CANCELLED"},
    {"orderId": "LUCKIN-001", "status": "MYSTERY"},
    {"orderId": "LUCKIN-001"},
])
async def test_cancel_rejects_query_mismatch_or_non_cancellable_status(
        query_payload):
    workflow, client = _cancel_workflow(
        ledger=FakeLedger(recent=[_recent_order()]),
        scripts={"queryOrderDetailInfo": [_ok(query_payload)]})

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, META)

    assert result.status != NEED_CONFIRM
    assert "没有发起取消" in result.speech
    assert client.counts["cancelOrder"] == 0


@pytest.mark.asyncio
async def test_cancel_accepts_real_query_status_name_before_numeric_code():
    workflow, client = _cancel_workflow(
        ledger=FakeLedger(recent=[_recent_order()]),
        scripts={"queryOrderDetailInfo": [_ok({
            "orderId": "LUCKIN-001", "orderStatus": 10,
            "orderStatusName": "\u5f85\u4ed8\u6b3e",
        })]})

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001"),
        CTX, META)

    assert result.status == NEED_CONFIRM
    assert client.counts["cancelOrder"] == 0


@pytest.mark.asyncio
async def test_real_boolean_cancel_ack_is_verified_by_one_post_query():
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [
                _ok({"orderId": "LUCKIN-001", "orderStatus": 10,
                     "orderStatusName": "\u5f85\u4ed8\u6b3e"}),
                _ok({"orderId": "LUCKIN-001", "orderStatus": 60,
                     "orderStatusName": "\u5df2\u53d6\u6d88"}),
            ],
            "cancelOrder": [_ok(True)],
        })
    intent = _intent(name="luckin.order_cancel", order_id="LUCKIN-001")
    pending = await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001",
                checkout_token=pending.data["checkout_token"]),
        CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert client.counts["queryOrderDetailInfo"] == 2
    assert result.data["status"] == "cancelled"
    assert ledger.closes[0][1] == DONE


@pytest.mark.asyncio
async def test_boolean_cancel_ack_stays_uncertain_when_post_query_not_terminal():
    ledger = FakeLedger(recent=[_recent_order()])
    still_unpaid = _ok({
        "orderId": "LUCKIN-001", "orderStatus": 10,
        "orderStatusName": "\u5f85\u4ed8\u6b3e"})
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [still_unpaid, still_unpaid],
            "cancelOrder": [_ok(True)],
        })
    intent = _intent(name="luckin.order_cancel", order_id="LUCKIN-001")
    pending = await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001",
                checkout_token=pending.data["checkout_token"]),
        CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert client.counts["queryOrderDetailInfo"] == 2
    assert result.data["status"] == "uncertain"
    assert ledger.closes[0][1] == FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("verification_failure", [
    {"ok": True, "data": {
        "success": False, "code": 5002, "data": {},
    }},
    McpTimeout("post-cancel query connect timeout", sent=False),
])
async def test_boolean_cancel_ack_makes_any_post_query_failure_uncertain(
        verification_failure):
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [ORDER_RESULT, verification_failure],
            "cancelOrder": [_ok(True)],
        })
    intent = _intent(name="luckin.order_cancel", order_id="LUCKIN-001")
    pending = await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001",
                checkout_token=pending.data["checkout_token"]),
        CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert client.counts["queryOrderDetailInfo"] == 2
    assert "可能" in result.speech and "不要重复" in result.speech
    assert result.data["status"] == "uncertain"
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "luckin", "merchant": "luckin",
         "error": "mcp_uncertain", "outcome": "uncertain"},
        "瑞幸取消结果不确定")]


@pytest.mark.asyncio
async def test_cancel_draft_is_bound_to_owner_and_session():
    store = FakeDraftStore()
    workflow, client = _cancel_workflow(
        store=store, ledger=FakeLedger(recent=[_recent_order()]))
    intent = _intent(name="luckin.order_cancel", order_id="LUCKIN-001")
    pending = await workflow.cancel(intent, CTX, META)
    other_ctx = SimpleNamespace(
        user_id="user-2", session_id="session-2", vehicle_id="vehicle-2",
        trace_id="trace-2")

    result = await workflow.cancel(
        _intent(name="luckin.order_cancel", order_id="LUCKIN-001",
                checkout_token=pending.data["checkout_token"]),
        other_ctx, {**META, "confirmed": "true"})

    assert "已失效" in result.speech
    assert client.counts["cancelOrder"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    McpTimeout("cancel timeout", sent=True),
    McpError("remote protocol failure after request delivery"),
    ConnectionError("connection lost after request delivery"),
    {"ok": True, "data": {"code": 0, "data": True}},
    {"ok": True, "data": {"success": True, "data": True}},
])
async def test_cancel_unknown_outcome_is_not_retried(failure):
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [ORDER_RESULT],
            "cancelOrder": [failure],
        })
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert next(call for call in client.calls
                if call[0] == "cancelOrder")[2] is False
    assert "可能" in result.speech and "不要重复" in result.speech
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "luckin", "merchant": "luckin",
         "error": "mcp_uncertain", "outcome": "uncertain"},
        "瑞幸取消结果不确定")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"ok": True, "data": {"success": False, "code": 5002, "data": {}}},
    McpTimeout("connect timeout", sent=False),
])
async def test_known_cancel_rejection_is_failed_not_uncertain(failure):
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [ORDER_RESULT],
            "cancelOrder": [failure],
        })
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert "可能" not in result.speech
    assert "没有取消" in result.speech
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "luckin", "merchant": "luckin",
         "error": "mcp_rejected", "outcome": "failed"},
        "瑞幸取消未受理")]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_payload", [
    {"orderId": "OTHER-ORDER", "status": "CANCELLED"},
    {"orderId": "LUCKIN-001", "status": "MYSTERY"},
    {"orderId": "LUCKIN-001"},
])
async def test_incomplete_cancel_success_is_uncertain(cancel_payload):
    ledger = FakeLedger(recent=[_recent_order()])
    workflow, client = _cancel_workflow(
        ledger=ledger,
        scripts={
            "queryOrderDetailInfo": [ORDER_RESULT],
            "cancelOrder": [_ok(cancel_payload)],
        })
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)

    result = await workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"})

    assert client.counts["cancelOrder"] == 1
    assert "可能" in result.speech and "不要重复" in result.speech
    assert ledger.closes[0][2]["outcome"] == "uncertain"


@pytest.mark.asyncio
async def test_verified_cancel_shields_done_close_then_preserves_cancellation():
    class CancelOnceLedger(FakeLedger):
        def __init__(self):
            super().__init__(recent=[_recent_order()])
            self.release = asyncio.Event()
            self.completed = False

        async def close(self, task_id, status, *, result_ref=None, progress=""):
            self.closes.append(
                (task_id, status, copy.deepcopy(result_ref), progress))
            await self.release.wait()
            self.completed = True
            return True

    ledger = CancelOnceLedger()
    store = FakeDraftStore()
    workflow, client = _cancel_workflow(ledger=ledger, store=store)
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)
    operation = asyncio.create_task(workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"}))
    while not ledger.closes:
        await asyncio.sleep(0)
    operation.cancel()
    ledger.release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation
    assert client.counts["cancelOrder"] == 1
    assert ledger.closes[-1][1] == DONE
    assert ledger.completed is True
    assert store.leases == {}


@pytest.mark.asyncio
async def test_cancelled_cancel_never_detaches_ledger_close_past_lease_release():
    class CancellationInsensitiveLedger(FakeLedger):
        def __init__(self):
            super().__init__(recent=[_recent_order()])
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.acked = False
            self.committed_after_ack = False

        async def close(self, task_id, status, *, result_ref=None, progress=""):
            self.entered.set()
            await self.release.wait()
            self.committed_after_ack = self.acked
            return await super().close(
                task_id, status, result_ref=result_ref, progress=progress)

    store = FakeDraftStore()
    ledger = CancellationInsensitiveLedger()
    workflow, client = _cancel_workflow(store=store, ledger=ledger)
    intent = _intent(name="luckin.order_cancel", order_id="")
    await workflow.cancel(intent, CTX, META)
    operation = asyncio.create_task(workflow.cancel(
        intent, CTX, {**META, "confirmed": "true"}))
    await asyncio.wait_for(ledger.entered.wait(), timeout=1)
    operation.cancel()
    try:
        await asyncio.sleep(2.1)
        assert operation.done() is False
        assert await store.delete_owner("user-1") is False
    finally:
        ledger.release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    ledger.acked = await store.delete_owner("user-1")
    assert ledger.acked is True
    assert ledger.committed_after_ack is False
    assert client.counts["cancelOrder"] == 1


@pytest.mark.asyncio
async def test_menu_image_requires_https_and_an_allowlisted_host():
    """商品图链是**外部服务给的不可信输入**，而它会变成 HMI 发起的一次网络请求。

    只有 https + `servers.yaml::image_hosts` 精确白名单内的域名才落进卡片；
    其余静默丢弃退回纯文字——商户换 CDN 该降级成没有图，不该毁掉整张选品卡。
    """
    workflow, _ = _workflow()
    result = await workflow.menu(_intent(name="luckin.menu"), CTX, META)
    first = next(i for i in result.ui_card["items"]
                 if i["name"] == "生椰拿铁（首创）")
    assert first["image_url"] == "https://img04.luckincoffeecdn.com/pic/1262.png"

    for bad in ("http://img04.luckincoffeecdn.com/pic/1.png",   # 明文
                "https://evil.example.com/pic/1.png",           # 域名不在名单
                "https://user:pw@img04.luckincoffeecdn.com/1.png",  # 带凭据
                "https://img04.luckincoffeecdn.com\n/1.png",    # 控制字符
                "/pic/1.png", ""):
        script = json.loads(json.dumps(SEARCH_RESULT))
        script["data"]["data"][0]["pictureUrl"] = bad
        flow, _c = _workflow(scripts={
            "queryShopList": [SHOP_RESULT], "searchProductForMcp": [script]})
        card = (await flow.menu(_intent(name="luckin.menu"), CTX, META)).ui_card
        item = next(i for i in card["items"] if i["name"] == "生椰拿铁（首创）")
        assert "image_url" not in item, bad

    # 白名单为空 = 这个商户不给图，不是「随便什么图都行」
    empty, _c = _workflow(image_hosts=[])
    card = (await empty.menu(_intent(name="luckin.menu"), CTX, META)).ui_card
    assert all("image_url" not in item for item in card["items"])


def _focus_refs(name_ref="focus.last_places.0.name",
                longitude_ref="focus.last_places.0.lng",
                latitude_ref="focus.last_places.0.lat",
                producer="nearby.search"):
    return {"_trusted_slot_refs": json.dumps({
        "store_name": {"ref": name_ref, "producer_intent": producer},
        "store_longitude": {"ref": longitude_ref, "producer_intent": producer},
        "store_latitude": {"ref": latitude_ref, "producer_intent": producer},
    }, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_cross_turn_focus_anchor_is_accepted_but_mixing_sources_is_not():
    """跨轮焦点是**新增的合法来源**，不是放松的校验强度。

    `focus.last_places.<N>` 的值同样只可能由执行器从服务端持有的上一轮 nearby.search
    结果写入（PlanContext，LLM/客户端写不到）。放行的是来源；name/lng/lat 仍必须
    全部来自同一条 POI——混拼一律拒，否则就是「在 A 店的名字下用 B 店的坐标下单」。
    """
    workflow, client = _workflow()
    ok = await workflow.menu(_intent(name="luckin.menu"), CTX, _focus_refs())
    assert ok.ui_card["type"] == "merchant_choices"
    assert [name for name, _, _ in client.calls][0] == "queryShopList"

    mixed = [
        _focus_refs(longitude_ref="focus.last_places.1.lng"),      # 换了下标
        _focus_refs(latitude_ref="s1.data.items.0.lat"),           # 混两种来源
        _focus_refs(producer="luckin.order"),                      # 生产者不是 nearby
        _focus_refs(name_ref="focus.last_places.name"),            # 缺下标
        _focus_refs(name_ref="focus.other.0.name"),                # 换了容器
    ]
    for refs in mixed:
        flow, calls = _workflow()
        denied = await flow.menu(_intent(name="luckin.menu"), CTX, refs)
        assert not denied.ui_card, refs
        assert not denied.missing_slots, f"{refs}：拒绝不得挂起会话"
        assert calls.calls == [], "拒绝必须在碰商户接口之前落定"

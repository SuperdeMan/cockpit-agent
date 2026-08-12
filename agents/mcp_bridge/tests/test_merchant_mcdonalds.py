"""麦当劳官方 MCP 的确定性选店、选品、计价与未支付下单工作流。"""
from __future__ import annotations

import asyncio
import copy
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from datetime import timedelta, timezone

from agents._sdk import NEED_CONFIRM, NEED_SLOT
from agents._sdk.ledger import DONE, FAILED, LedgerTask
from agents.mcp_bridge.src.mcp_client import McpTimeout
from agents.mcp_bridge.src.merchant.mcdonalds import McDonaldsWorkflow


CTX = SimpleNamespace(user_id="u1", session_id="s1", vehicle_id="v1")
META = {"granted_scopes": "merchant.read,merchant.write"}


def _ok(data):
    """脱敏后的官方业务 envelope；协议成功与业务成功分层。"""
    return {"ok": True, "text": "", "data": {"success": True, "code": 200,
                                                   "data": data}}


STORE_RESULT = _ok([
    {
        "storeCode": "S001",
        "beCode": "B001",
        "storeName": "人民广场麦当劳餐厅",
        # 2026-08-12 真机样本没有 businessStatus/workStatus，
        # 只给官方可预约窗口。
        "reservation": True,
        "reservationTimeOptions": [{
            "date": "2026-08-12",
            "today": True,
            "reservationOptionText": "午餐(10:44至14:15)",
        }],
    },
])

MENU_RESULT = _ok({
    "categories": [{
        "name": "套餐",
        "meals": [
            {"code": "M001", "tags": ["经典"]},
            {"code": "M002", "tags": ["热销"]},
        ],
    }, {
        # 真机上同一 code 会同时出现在多个分类。
        "name": "热门",
        "meals": [{"code": "M001", "tags": ["热门"]}],
    }],
    "meals": {
        "M001": {
            "name": "巨无霸套餐", "currentPrice": "36.9",
            "originalPrice": "39", "canWithOrder": False,
        },
        "M002": {
            "name": "双层吉士堡套餐", "currentPrice": "32.9",
            "originalPrice": "35", "canWithOrder": False,
        },
    },
})

DETAIL_RESULT = _ok({
    "code": "M001",
    "name": "巨无霸套餐",
    "rounds": [{
        "id": 101,
        "name": "主食",
        "minQuantity": 1,
        "maxQuantity": 1,
        "quantity": 1,
        "choices": [{
            "code": "P001",
            "name": "巨无霸",
            "quantity": 1,
            # 真实返回用 -1 表示无上限，不能当成非法负数。
            "maxQuantity": -1,
            "isDefault": 1,
            "supportModify": True,
            "modification": {"items": [{
                "minValues": 1, "maxValues": 1,
                "values": [{
                    "code": "CMOD001", "name": "默认配菜",
                    "price": 0, "minQuantity": 0, "maxQuantity": 1,
                    "selectedQuantity": 1,
                    "selectedKey": "CSEL001",
                    "unselectedKey": "CUNSEL001",
                }, {
                    "code": "CMOD002", "name": "额外芝士",
                    "price": 200, "minQuantity": 0, "maxQuantity": 1,
                    "selectedQuantity": 0,
                    "selectedKey": "CSEL002",
                    "unselectedKey": "CUNSEL002",
                }],
            }]},
        }],
    }],
    "modification": {"items": [{
        "minValues": 1, "maxValues": 1,
        "values": [{
            "code": "MOD001", "name": "默认制作", "price": 0,
            "minQuantity": 0, "maxQuantity": 1,
            "selectedQuantity": 1, "selectedKey": "SEL001",
            "unselectedKey": "UNSEL001",
        }, {
            "code": "MOD002", "name": "去酸黄瓜", "price": 0,
            "minQuantity": 0, "maxQuantity": 1,
            "selectedQuantity": 0, "selectedKey": "SEL002",
            "unselectedKey": "UNSEL002",
        }],
    }]},
})

CALCULATE_RESULT = _ok({
    "price": 3690,
    "originalPrice": 3900,
    "discountPrice": 210,
    "productList": [{
        "productCode": "M001", "productName": "巨无霸套餐",
        "quantity": 1, "subtotal": 3690, "originalSubtotal": 3900,
    }],
    "takeWayList": [
        {"code": "take-in-store", "title": "外带", "subtitle": "店内自提"},
        {"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"},
    ],
})

CREATE_RESULT = _ok({
    "orderId": "ORDER-001",
    "payH5Url": "https://m.mcd.cn/mcp/scanToPay?redacted=1",
    "orderDetail": {"realTotalAmount": "36.90"},
})


class FakeClient:
    healthy = True
    alive = True

    def __init__(self, scripts):
        self.scripts = {name: list(values) for name, values in scripts.items()}
        self.calls = []
        self.counts = defaultdict(int)

    async def call_tool(self, name, arguments, *, timeout_s,
                        retry_on_session_loss=True):
        self.calls.append((name, copy.deepcopy(arguments), retry_on_session_loss))
        self.counts[name] += 1
        value = self.scripts[name].pop(0)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


class FakeDraftStore:
    def __init__(self):
        self.current = {}
        self._lock = asyncio.Lock()
        self.leases = {}
        self.authorization_allowed = True
        self.authorizations = []
        self.releases = []
        self.deleted = set()
        self.pending_deletes = set()

    async def put(self, draft, *, lease_token=""):
        async with self._lock:
            if draft.user_id in self.deleted:
                return False
            if lease_token and self.leases.get(draft.user_id) != lease_token:
                return False
            self.current[(draft.user_id, draft.session_id, draft.merchant)] = draft
            return True

    async def consume_current(self, *, user_id, session_id, merchant,
                              expected_action):
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
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
    def __init__(self, *, open_result="task", close_error=None):
        self.open_result = open_result
        self.close_error = close_error
        self.opens = []
        self.closes = []

    async def open(self, user_id, session_id, agent_id, kind, goal, **kwargs):
        self.opens.append({
            "user_id": user_id, "session_id": session_id,
            "agent_id": agent_id, "kind": kind, "goal": goal,
            **kwargs,
        })
        if self.open_result is None:
            return None
        return LedgerTask(task_id="task-1", user_id=user_id,
                          session_id=session_id, agent_id=agent_id,
                          kind=kind, goal=goal)

    async def close(self, task_id, status, *, result_ref=None, progress=""):
        self.closes.append((task_id, status, copy.deepcopy(result_ref), progress))
        if self.close_error is not None:
            raise self.close_error
        return True


class FakePayment:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def authorize(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class ExplodingPaymentResponse:
    @property
    def payment_id(self):
        raise RuntimeError("card mapping failed")


def _schema(*properties, required=()):
    return {
        "type": "object",
        "properties": {name: {} for name in properties},
        "required": list(required),
        "additionalProperties": False,
    }


def _workflow(*, scripts=None, store=None, ledger=None, payment=None,
              pay_url_hosts=None,
              clock=lambda: datetime(2026, 8, 12, 12, 25)):
    scripts = scripts or {
        "query-nearby-stores": [STORE_RESULT],
        "query-meals": [MENU_RESULT],
        "query-meal-detail": [DETAIL_RESULT],
        # prepare + confirm 各自计价，确认轮不信任旧价。
        "calculate-price": [CALCULATE_RESULT, CALCULATE_RESULT],
        "create-order": [CREATE_RESULT],
    }
    client = FakeClient(scripts)
    item_schema = {
        "type": "object",
        "properties": {
            "productCode": {"type": "string"},
            "quantity": {"type": "integer"},
            "modification": {"type": "object"},
            "roundList": {"type": "array"},
        },
    }
    schemas = {
        "query-nearby-stores": _schema(
            "keyword", "city", "beType", "searchType",
            required=("beType", "searchType")),
        "query-meals": _schema(
            "storeCode", "orderType", "beType", "beCode", "reservationDate",
            required=("storeCode", "orderType", "beType")),
        "query-meal-detail": _schema(
            "storeCode", "orderType", "beType", "code", "beCode",
            "reservationDate",
            required=("storeCode", "orderType", "beType", "code")),
        "calculate-price": {
            **_schema(
                "storeCode", "items", "orderType", "beType", "beCode",
                "reservationDate", "gmServiceCode", "withOrder",
                required=("storeCode", "orderType", "beType")),
        },
        "create-order": {
            **_schema(
                "storeCode", "items", "orderType", "beType", "takeWayCode",
                "addressId", "beCode", "reservationDate", "gmServiceCode",
                "withOrder", required=("storeCode", "orderType", "beType")),
        },
    }
    schemas["calculate-price"]["properties"]["items"] = {
        "type": "array", "items": item_schema}
    schemas["create-order"]["properties"]["items"] = {
        "type": "array", "items": copy.deepcopy(item_schema)}
    consts = {
        "query-nearby-stores": {"beType": 1, "searchType": 1},
        "query-meals": {"orderType": 1, "beType": 1},
        "query-meal-detail": {"orderType": 1, "beType": 1},
        "calculate-price": {"orderType": 1, "beType": 1},
        "create-order": {"orderType": 1, "beType": 1},
    }
    tools = {}
    for name, schema in schemas.items():
        is_write = name == "create-order"
        tool = SimpleNamespace(
            name=name, timeout_ms=12000, const_args=consts.get(name, {}),
            write=is_write, retry_policy="never" if is_write else "safe",
            pay_url_locator="data.payH5Url" if is_write else "",
            success_predicate=({"success": [True], "code": [200]}
                               if is_write else {}))
        tools[name] = SimpleNamespace(client=client, tool=tool,
                                      input_schema=schema)
    server = SimpleNamespace(
        id="mcdonalds", demo=False,
        pay_url_hosts=(list(pay_url_hosts) if pay_url_hosts is not None
                       else ["m.mcd.cn"]))
    spec = SimpleNamespace(intent="mcd.order")
    workflow = McDonaldsWorkflow(
        server, spec, tools, store or FakeDraftStore(),
        ledger if ledger is not None else FakeLedger(), payment=payment,
        clock=clock)
    return workflow, client


def _intent(**slots):
    values = {
        "item_query": "巨无霸套餐",
        "quantity": "1",
        "store_hint": "人民广场",
        "city": "上海",
        "pickup_mode": "到店自取",
    }
    values.update(slots)
    return SimpleNamespace(name="mcd.order", slots=values)


def _scripts(*, calculations=None, create=CREATE_RESULT, stores=STORE_RESULT,
             menu=MENU_RESULT, detail=DETAIL_RESULT):
    return {
        "query-nearby-stores": [stores],
        "query-meals": [menu],
        "query-meal-detail": [detail],
        "calculate-price": list(calculations or [
            CALCULATE_RESULT, CALCULATE_RESULT]),
        "create-order": [create],
    }


def test_auto_reservation_parses_real_chinese_windows_deterministically():
    options = [{
        "date": "2026-08-12", "today": True,
        "reservationOptionText": (
            "早餐(07:14至10:15)，午餐(10:44至14:15)，"
            "下午茶(14:44至16:45)"),
    }]

    assert McDonaldsWorkflow._auto_reservation(
        options, now=datetime(2026, 8, 12, 7, 0)) == "2026-08-12 07:30"
    assert McDonaldsWorkflow._auto_reservation(
        options, now=datetime(2026, 8, 12, 10, 20)) == "2026-08-12 11:00"
    assert McDonaldsWorkflow._auto_reservation(
        [], now=datetime(2026, 8, 12, 7, 0)) == ""


def test_default_clock_is_explicit_shanghai_time_and_past_reservation_is_rejected():
    workflow, _ = _workflow(clock=None)
    now = workflow._clock()
    assert now.utcoffset() == timedelta(hours=8)
    assert now.tzname() == "Asia/Shanghai"

    options = [{
        "date": "2026-08-12", "today": True,
        "reservationOptionText": "午餐(10:44至14:15)",
    }]
    current = datetime(2026, 8, 12, 12, 36,
                       tzinfo=timezone(timedelta(hours=8), "Asia/Shanghai"))
    assert McDonaldsWorkflow._reservation_allowed(
        "2026-08-12 11:00", options, now=current) is False
    with pytest.raises(Exception, match="预约时间"):
        McDonaldsWorkflow._store_context(
            {
                "storeCode": "S001", "reservation": True,
                "reservationTimeOptions": options,
            }, {"reservation_date": "2026-08-12 11:00"}, now=current)


@pytest.mark.asyncio
async def test_verified_create_shields_done_close_then_preserves_cancellation():
    class CancelOnceLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.completed = False

        async def close(self, task_id, status, *, result_ref=None, progress=""):
            self.closes.append((task_id, status, copy.deepcopy(result_ref), progress))
            await self.release.wait()
            self.completed = True
            return True

    ledger = CancelOnceLedger()
    store = FakeDraftStore()
    workflow, client = _workflow(ledger=ledger, store=store)
    preview = await workflow.prepare(_intent(), CTX, META)
    operation = asyncio.create_task(workflow.confirm(
        _intent(checkout_token=preview.data["checkout_token"]), CTX,
        {**META, "confirmed": "true"}))
    while not ledger.closes:
        await asyncio.sleep(0)
    operation.cancel()
    ledger.release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert client.counts["create-order"] == 1
    assert ledger.closes[-1][1] == DONE
    assert ledger.completed is True
    assert store.leases == {}


@pytest.mark.asyncio
async def test_prepare_runs_read_chain_only_and_returns_deterministic_preview():
    workflow, client = _workflow()

    result = await workflow.prepare(_intent(), CTX, META)

    assert result.status == NEED_CONFIRM
    assert result.ui_card["type"] == "merchant_order_preview"
    assert result.ui_card["merchant"] == "mcdonalds"
    assert result.ui_card["store_name"] == "人民广场麦当劳餐厅"
    assert result.ui_card["items"] == [{
        "name": "巨无霸套餐", "quantity": 1,
        "specifications": ["巨无霸", "默认配菜", "默认制作"],
        "amount_cents": 3690,
    }]
    assert result.ui_card["amount_cents"] == 3690
    assert result.ui_card["original_amount_cents"] == 3900
    assert result.ui_card["discount_cents"] == 210
    assert "预约" in result.ui_card["fulfillment"]
    assert "确认后只创建未支付订单" in result.speech
    assert "不支付会由商户自动失效" in result.speech
    assert "预约" in result.speech
    assert result.data and result.data["checkout_token"]
    assert [name for name, _, _ in client.calls] == [
        "query-nearby-stores", "query-meals", "query-meal-detail",
        "calculate-price",
    ]
    assert client.counts["create-order"] == 0
    assert all(retry for _, _, retry in client.calls)


@pytest.mark.asyncio
async def test_read_chain_uses_only_official_codes_and_calculated_items():
    workflow, client = _workflow()

    await workflow.prepare(_intent(), CTX, META)

    by_name = {name: args for name, args, _ in client.calls}
    reservation_date = by_name["query-meals"]["reservationDate"]
    assert reservation_date.startswith("2026-08-12 ")
    assert by_name["query-meals"] == {
        "storeCode": "S001", "orderType": 1, "beType": 1,
        "beCode": "B001", "reservationDate": reservation_date}
    assert by_name["query-meal-detail"] == {
        "storeCode": "S001", "orderType": 1, "beType": 1,
        "beCode": "B001", "reservationDate": reservation_date,
        "code": "M001"}
    calculate = by_name["calculate-price"]
    assert calculate["storeCode"] == "S001"
    assert calculate["orderType"] == 1 and calculate["beType"] == 1
    assert calculate["beCode"] == "B001"
    assert calculate["reservationDate"] == reservation_date
    assert calculate["items"] == [{
        "productCode": "M001", "quantity": 1,
        "modification": {"values": [{
            "key": "SEL001", "code": "MOD001", "quantity": 1,
        }, {
            "key": "UNSEL002", "code": "MOD002", "quantity": 0,
        }]},
        "roundList": [{
            "round": "101",
            "comboItemList": [{
                "code": "P001", "quantity": 1,
                "modification": {"values": [{
                    "key": "CSEL001", "code": "CMOD001", "quantity": 1,
                }, {
                    "key": "CUNSEL002", "code": "CMOD002", "quantity": 0,
                }]},
            }],
        }],
    }]


@pytest.mark.asyncio
async def test_reservation_and_be_code_are_bound_to_one_store_context():
    workflow, client = _workflow()

    result = await workflow.prepare(
        _intent(reservation_date="2026-08-12 13:00"), CTX, META)

    assert result.status == NEED_CONFIRM
    downstream = [
        args for name, args, _ in client.calls if name != "query-nearby-stores"]
    assert downstream
    assert all(args["storeCode"] == "S001" for args in downstream)
    assert all(args["beCode"] == "B001" for args in downstream)
    assert all(args["reservationDate"] == "2026-08-12 13:00"
               for args in downstream)


@pytest.mark.asyncio
async def test_multiple_store_candidates_require_user_choice_and_stop_chain():
    stores = _ok([
        {"storeCode": "S001", "beCode": "B001", "storeName": "南京东路店",
         "businessStatus": True},
        {"storeCode": "S002", "beCode": "B002", "storeName": "南京西路店",
         "businessStatus": True},
        {"storeCode": "S003", "beCode": "B003", "storeName": "南京南路店",
         "businessStatus": True},
        {"storeCode": "S004", "beCode": "B004", "storeName": "南京北路店",
         "businessStatus": True},
    ])
    workflow, client = _workflow(scripts={"query-nearby-stores": [stores]})

    result = await workflow.prepare(_intent(store_hint="南京"), CTX, META)

    assert result.status == NEED_SLOT
    assert result.missing_slots == ["store_hint"]
    assert result.ui_card["type"] == "merchant_choices"
    assert result.ui_card["choice_kind"] == "store"
    assert len(result.ui_card["items"]) == 3
    assert all("选择麦当劳门店" in button["send_text"]
               for button in result.ui_card["buttons"])
    assert [name for name, _, _ in client.calls] == ["query-nearby-stores"]


@pytest.mark.asyncio
async def test_real_store_choice_button_resumes_with_clean_official_keyword():
    stores = _ok([
        {"storeCode": "S001", "beCode": "B001", "storeName": "南京东路店",
         "businessStatus": True},
        {"storeCode": "S002", "beCode": "B002", "storeName": "南京西路店",
         "businessStatus": True},
    ])
    workflow, client = _workflow(scripts={
        "query-nearby-stores": [stores, stores],
        "query-meals": [MENU_RESULT],
        "query-meal-detail": [DETAIL_RESULT],
        "calculate-price": [CALCULATE_RESULT],
    })
    pending = await workflow.prepare(
        _intent(store_hint="南京"), CTX, META)

    send_text = pending.ui_card["buttons"][0]["send_text"]
    resumed = await workflow.prepare(
        _intent(store_hint=send_text), CTX, META)

    assert resumed.status == NEED_CONFIRM
    assert client.calls[1][0] == "query-nearby-stores"
    assert client.calls[1][1].get("keyword") == "南京东路店"


@pytest.mark.asyncio
async def test_multiple_product_candidates_require_choice_and_closed_store_is_actionable():
    menu = _ok({
        "categories": [{"meals": [
            {"code": "M001", "tags": []},
            {"code": "M002", "tags": []},
            {"code": "M003", "tags": []},
            {"code": "M004", "tags": []},
        ]}],
        "meals": {
            "M001": {"name": "巨无霸经典套餐", "currentPrice": "36.9"},
            "M002": {"name": "巨无霸双人套餐", "currentPrice": "69.9"},
            "M003": {"name": "巨无霸家庭套餐", "currentPrice": "99.9"},
            "M004": {"name": "巨无霸分享套餐", "currentPrice": "88.8"},
        },
    })
    workflow, _ = _workflow(scripts={
        "query-nearby-stores": [STORE_RESULT], "query-meals": [menu]})
    result = await workflow.prepare(_intent(item_query="巨无霸"), CTX, META)
    assert result.status == NEED_SLOT
    assert result.missing_slots == ["item_query"]
    assert result.ui_card["choice_kind"] == "product"
    assert len(result.ui_card["items"]) == 3

    selected_name = result.ui_card["items"][0]["name"]
    assert McDonaldsWorkflow._choice_value(
        result.ui_card["buttons"][0]["send_text"], "餐品") == selected_name

    closed = _ok([{**STORE_RESULT["data"]["data"][0],
                   "businessStatus": False}])
    closed_flow, closed_client = _workflow(
        scripts={"query-nearby-stores": [closed]})
    closed_result = await closed_flow.prepare(_intent(), CTX, META)
    assert "打烊" in closed_result.speech
    assert [name for name, _, _ in closed_client.calls] == [
        "query-nearby-stores"]

    empty_flow, empty_client = _workflow(
        scripts={"query-nearby-stores": [_ok([])]})
    empty_result = await empty_flow.prepare(_intent(), CTX, META)
    assert "没有找到" in empty_result.speech
    assert "打烊" not in empty_result.speech
    assert [name for name, _, _ in empty_client.calls] == [
        "query-nearby-stores"]


@pytest.mark.asyncio
async def test_unknown_without_reservation_evidence_fails_closed_and_sold_out_stops():
    unknown = copy.deepcopy(STORE_RESULT)
    unknown["data"]["data"][0].pop("reservation")
    unknown["data"]["data"][0].pop("reservationTimeOptions")
    unknown_flow, unknown_client = _workflow(
        scripts={"query-nearby-stores": [unknown]})

    unknown_result = await unknown_flow.prepare(_intent(), CTX, META)

    assert "营业状态" in unknown_result.speech
    assert [name for name, _, _ in unknown_client.calls] == [
        "query-nearby-stores"]

    sold_out = copy.deepcopy(CALCULATE_RESULT)
    sold_out["data"]["data"]["productList"] = []
    sold_out_flow, sold_out_client = _workflow(
        scripts=_scripts(calculations=[sold_out]))

    sold_out_result = await sold_out_flow.prepare(_intent(), CTX, META)

    assert "售罄" in sold_out_result.speech or "不可下单" in sold_out_result.speech
    assert sold_out_client.counts["create-order"] == 0


@pytest.mark.asyncio
async def test_required_combo_without_official_default_is_refused_not_guessed():
    detail = copy.deepcopy(DETAIL_RESULT)
    choice = detail["data"]["data"]["rounds"][0]["choices"][0]
    choice["isDefault"] = 0
    choice["quantity"] = 0
    workflow, client = _workflow(scripts={
        "query-nearby-stores": [STORE_RESULT],
        "query-meals": [MENU_RESULT],
        "query-meal-detail": [detail],
    })

    result = await workflow.prepare(_intent(), CTX, META)

    assert "默认" in result.speech or "规格" in result.speech
    assert client.counts["calculate-price"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", ["0", "21", "1.5", "abc"])
async def test_item_and_quantity_are_validated_before_any_external_call(quantity):
    workflow, client = _workflow()
    result = await workflow.prepare(_intent(quantity=quantity), CTX, META)
    assert result.status == NEED_SLOT
    assert result.missing_slots == ["quantity"]
    assert not client.calls

    missing_flow, missing_client = _workflow()
    missing = await missing_flow.prepare(_intent(item_query=""), CTX, META)
    assert missing.status == NEED_SLOT
    assert missing.missing_slots == ["item_query"]
    assert not missing_client.calls


@pytest.mark.asyncio
async def test_confirm_consumes_snapshot_and_calls_create_exactly_once_without_retry():
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

    assert client.counts["create-order"] == 1
    assert client.counts["calculate-price"] == 2
    create_call = next(call for call in client.calls if call[0] == "create-order")
    assert create_call[2] is False
    assert create_call[1] == {
        "storeCode": "S001",
        "items": [{
            "productCode": "M001", "quantity": 1,
            "modification": {"values": [{
                "key": "SEL001", "code": "MOD001", "quantity": 1,
            }, {
                "key": "UNSEL002", "code": "MOD002", "quantity": 0,
            }]},
            "roundList": [{
                "round": "101",
                "comboItemList": [{
                    "code": "P001", "quantity": 1,
                    "modification": {"values": [{
                        "key": "CSEL001", "code": "CMOD001", "quantity": 1,
                    }, {
                        "key": "CUNSEL002", "code": "CMOD002", "quantity": 0,
                    }]},
                }],
            }],
        }],
        "orderType": 1,
        "beType": 1,
        "beCode": "B001",
        "reservationDate": next(
            args["reservationDate"] for name, args, _ in client.calls
            if name == "query-meals"),
        "takeWayCode": "take-in-store",
    }
    assert confirmed.data == {
        "merchant": "mcdonalds", "server": "mcdonalds",
        "order_id": "ORDER-001", "status": "created",
        "amount_cents": 3690, "store_name": "人民广场麦当劳餐厅",
    }
    assert confirmed.ui_card["type"] == "mcp_order"
    assert "payH5Url" not in repr(confirmed.data)
    assert "m.mcd.cn" not in repr(confirmed.ui_card)
    assert ledger.closes == [(
        "task-1", DONE, confirmed.data, "麦当劳订单已创建")]
    assert "预览已失效" in duplicate.speech


@pytest.mark.asyncio
async def test_confirm_requires_live_owner_lease_before_reprice_ledger_or_create():
    store = FakeDraftStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    token = preview.data["checkout_token"]
    store.authorization_allowed = False

    result = await workflow.confirm(
        _intent(checkout_token=token), CTX,
        {**META, "confirmed": "true"})

    assert "失效" in result.speech or "无法安全" in result.speech
    assert client.counts["calculate-price"] == 1
    assert client.counts["create-order"] == 0
    assert ledger.opens == []
    assert store.authorizations == [("u1", token)]
    assert store.releases == [("u1", token)]
    assert store.leases == {}


@pytest.mark.asyncio
async def test_privacy_delete_wins_before_confirm_with_zero_ledger_and_remote_write():
    store = FakeDraftStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    assert await store.delete_owner("u1") is True

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"])

    assert "失效" in result.speech
    assert client.counts["create-order"] == 0
    assert ledger.opens == []


@pytest.mark.asyncio
async def test_operation_wins_delete_nacks_then_retries_after_create_release():
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

    assert await store.delete_owner("u1") is False
    store.continue_write.set()
    result = await asyncio.wait_for(operation, timeout=1)
    assert result.data["status"] == "created"
    assert client.counts["create-order"] == 1
    assert len(ledger.opens) == 1
    assert store.leases == {}
    assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_lease_loss_after_create_response_is_uncertain_and_never_invites_retry():
    class LeaseLostAfterWriteStore(FakeDraftStore):
            async def authorize(self, token, *, user_id):
                allowed = await super().authorize(token, user_id=user_id)
                return allowed and len(self.authorizations) < 5

    store = LeaseLostAfterWriteStore()
    ledger = FakeLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"])

    assert client.counts["create-order"] == 1
    assert len(ledger.opens) == 1
    assert ledger.closes == []
    assert result.data == {"merchant": "mcdonalds", "status": "uncertain"}
    assert "可能已经受理" in result.speech
    assert "不要重复下单" in result.speech
    assert "没有提交" not in result.speech


@pytest.mark.asyncio
async def test_cancelled_confirm_never_detaches_ledger_close_past_lease_release():
    class CancellationInsensitiveLedger(FakeLedger):
        def __init__(self):
            super().__init__()
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
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    operation = asyncio.create_task(workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"]))
    await asyncio.wait_for(ledger.entered.wait(), timeout=1)
    operation.cancel()
    try:
        await asyncio.sleep(2.1)
        assert operation.done() is False
        assert await store.delete_owner("u1") is False
        ledger.acked = False
    finally:
        ledger.release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    ledger.acked = await store.delete_owner("u1")
    assert ledger.acked is True
    assert ledger.committed_after_ack is False
    assert client.counts["create-order"] == 1


@pytest.mark.asyncio
async def test_blocked_ledger_open_keeps_delete_pending_until_effect_settles():
    class BlockingLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def open(self, *args, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().open(*args, **kwargs)

    store = FakeDraftStore()
    ledger = BlockingLedger()
    workflow, client = _workflow(store=store, ledger=ledger)
    preview = await workflow.prepare(_intent(), CTX, META)
    operation = asyncio.create_task(workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=preview.data["checkout_token"]))
    await asyncio.wait_for(ledger.entered.wait(), timeout=1)
    assert await store.delete_owner("u1") is False
    ledger.release.set()
    result = await asyncio.wait_for(operation, timeout=1)
    assert result.data["status"] == "created"
    assert client.counts["create-order"] == 1
    assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_confirm_requires_internal_confirm_flag_without_consuming_draft():
    workflow, client = _workflow()
    prepared = await workflow.prepare(_intent(), CTX, META)

    refused = await workflow.confirm(
        _intent(), CTX, META, token=prepared.data["checkout_token"])
    accepted = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": True},
        token=prepared.data["checkout_token"])

    assert refused.status == NEED_CONFIRM
    assert "确认" in refused.speech
    assert accepted.data["order_id"] == "ORDER-001"
    assert client.counts["create-order"] == 1


@pytest.mark.asyncio
async def test_two_concurrent_confirms_create_at_most_one_real_order():
    workflow, client = _workflow(store=FakeDraftStore())
    prepared = await workflow.prepare(_intent(), CTX, META)
    token = prepared.data["checkout_token"]

    first, second = await asyncio.gather(*[
        workflow.confirm(_intent(), CTX, {**META, "confirmed": "true"},
                         token=token)
        for _ in range(2)
    ])

    assert client.counts["create-order"] == 1
    assert sorted(result.data is not None and result.data.get("order_id") ==
                  "ORDER-001" for result in (first, second)) == [False, True]
    assert any("预览已失效" in result.speech for result in (first, second))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["price", "product", "takeway"])
async def test_confirm_recalculates_and_requires_new_confirmation_on_change(kind):
    changed = copy.deepcopy(CALCULATE_RESULT)
    if kind == "price":
        changed["data"]["data"].update({
            "price": 3790, "originalPrice": 4000, "discountPrice": 210})
        changed["data"]["data"]["productList"][0].update({
            "subtotal": 3790, "originalSubtotal": 4000})
    elif kind == "product":
        changed["data"]["data"]["productList"][0]["productName"] = (
            "巨无霸套餐（新包装）")
    else:
        changed["data"]["data"]["takeWayList"][0]["title"] = "柜台取餐"
        changed["data"]["data"]["takeWayList"][0]["subtitle"] = "现场柜台自提"
    store = FakeDraftStore()
    workflow, client = _workflow(
        store=store,
        scripts=_scripts(calculations=[CALCULATE_RESULT, changed]))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert result.status == NEED_CONFIRM
    assert result.data["checkout_token"] != prepared.data["checkout_token"]
    assert "重新确认" in result.speech
    assert client.counts["create-order"] == 0
    assert store.leases == {}
    fresh = await store.consume(
        result.data["checkout_token"], user_id="u1", session_id="s1",
        merchant="mcdonalds", expected_action="create")
    assert fresh is not None
    assert await store.release(fresh.token, user_id=fresh.user_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("qr_svg, expected, forbidden", [
    ("", "打开安全支付链接", "扫码"),
    ("<svg>safe</svg>", "扫码", "打开安全支付链接"),
])
async def test_confirm_registers_whitelisted_pay_url_and_uses_truthful_cta(
        qr_svg, expected, forbidden):
    payment = FakePayment(SimpleNamespace(payment_id="PAY-001", qr_svg=qr_svg))
    workflow, _ = _workflow(payment=payment)
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert len(payment.calls) == 1
    assert payment.calls[0]["external_order_ref"] == "ORDER-001"
    assert payment.calls[0]["external_pay_url"].startswith(
        "https://m.mcd.cn/")
    assert result.ui_card["type"] == "payment_qr"
    assert result.ui_card["order_id"] == "ORDER-001"
    assert result.ui_card["payment_id"] == "PAY-001"
    assert result.ui_card["buttons"] == [
        {"label": "查订单", "send_text": "查询麦当劳订单 ORDER-001"},
        {"label": "放弃支付", "send_text": "放弃支付这笔麦当劳订单"},
    ]
    assert expected in result.speech
    assert forbidden not in result.speech
    if qr_svg:
        assert result.ui_card["qr_svg"] == qr_svg
    else:
        assert "qr_svg" not in result.ui_card
    assert result.data and "http" not in repr(result.data)


@pytest.mark.asyncio
async def test_unapproved_pay_host_and_gateway_failure_never_leak_raw_url():
    cases = [
        (FakePayment(SimpleNamespace(payment_id="PAY-001", qr_svg="")),
         ["pay.example.invalid"]),
        (FakePayment(None), ["m.mcd.cn"]),
    ]
    for payment, hosts in cases:
        workflow, _ = _workflow(payment=payment, pay_url_hosts=hosts)
        prepared = await workflow.prepare(_intent(), CTX, META)
        result = await workflow.confirm(
            _intent(), CTX, {**META, "confirmed": "true"},
            token=prepared.data["checkout_token"])
        assert result.ui_card["type"] == "mcp_order"
        assert "m.mcd.cn" not in repr(result)
        assert "redacted=1" not in repr(result)
        assert "官方应用" in result.speech
    assert cases[0][0].calls == []
    assert len(cases[1][0].calls) == 1

    userinfo = copy.deepcopy(CREATE_RESULT)
    userinfo["data"]["data"]["payH5Url"] = (
        "https://attacker.invalid@m.mcd.cn/mcp/pay?secret=1")
    userinfo_payment = FakePayment(
        SimpleNamespace(payment_id="PAY-001", qr_svg=""))
    userinfo_flow, _ = _workflow(
        payment=userinfo_payment, scripts=_scripts(create=userinfo))
    prepared = await userinfo_flow.prepare(_intent(), CTX, META)
    userinfo_result = await userinfo_flow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])
    assert userinfo_payment.calls == []
    assert "attacker.invalid" not in repr(userinfo_result)
    assert "secret=1" not in repr(userinfo_result)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_url", [
    "https://m.mcd.cn/mcp/pay?secret value",
    "https://m.mcd.cn/mcp/pay?secret\x7fvalue",
])
async def test_pay_url_rejects_embedded_whitespace_and_del(unsafe_url):
    unsafe = copy.deepcopy(CREATE_RESULT)
    unsafe["data"]["data"]["payH5Url"] = unsafe_url
    payment = FakePayment(SimpleNamespace(payment_id="PAY-001", qr_svg=""))
    workflow, _ = _workflow(
        payment=payment, scripts=_scripts(create=unsafe))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert payment.calls == []
    assert result.ui_card["type"] == "mcp_order"
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_create_amount_mismatch_never_registers_or_exposes_payment():
    mismatched = copy.deepcopy(CREATE_RESULT)
    mismatched["data"]["data"]["orderDetail"]["realTotalAmount"] = "37.90"
    payment = FakePayment(SimpleNamespace(payment_id="PAY-001", qr_svg=""))
    workflow, _ = _workflow(
        payment=payment, scripts=_scripts(create=mismatched))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert result.data["order_id"] == "ORDER-001"
    assert result.ui_card["type"] == "mcp_order"
    assert payment.calls == []
    assert "金额与预览不一致" in result.speech and "不要支付" in result.speech
    assert "m.mcd.cn" not in repr(result)


@pytest.mark.asyncio
async def test_create_business_rejection_is_known_failure_not_uncertain():
    rejection = {"ok": True, "text": "", "data": {
        "success": False, "code": 5001, "data": {}}}
    ledger = FakeLedger()
    workflow, client = _workflow(
        ledger=ledger, scripts=_scripts(create=rejection))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert "没有创建" in result.speech
    assert "可能" not in result.speech
    assert client.counts["create-order"] == 1
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "mcdonalds", "merchant": "mcdonalds",
         "order_id": "", "status": "failed", "amount_cents": 3690,
         "store_name": "人民广场麦当劳餐厅"},
        "麦当劳拒绝创建订单")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    McpTimeout("read timeout", sent=True),
    {"ok": True, "text": "", "data": {
        "code": 200, "data": {"orderId": "ORDER-UNKNOWN"}}},
    {"ok": True, "text": "", "data": {
        "success": True, "data": {"orderId": "ORDER-UNKNOWN"}}},
    {"ok": True, "text": "", "data": {
        "success": True, "code": 200, "data": {}}},
])
async def test_sent_timeout_and_incomplete_create_response_are_uncertain(failure):
    ledger = FakeLedger()
    workflow, client = _workflow(
        ledger=ledger, scripts=_scripts(create=failure))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert "可能" in result.speech and "不要重复" in result.speech
    assert client.counts["create-order"] == 1
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "mcdonalds", "merchant": "mcdonalds",
         "order_id": "", "status": "uncertain", "amount_cents": 3690,
         "store_name": "人民广场麦当劳餐厅"},
        "麦当劳下单结果不确定")]


@pytest.mark.asyncio
async def test_connect_timeout_sent_false_is_known_not_sent_failure():
    ledger = FakeLedger()
    workflow, client = _workflow(
        ledger=ledger,
        scripts=_scripts(create=McpTimeout("connect timeout", sent=False)))
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert "没有提交" in result.speech
    assert "可能" not in result.speech
    assert client.counts["create-order"] == 1
    assert ledger.closes == [(
        "task-1", FAILED,
        {"server": "mcdonalds", "merchant": "mcdonalds",
         "order_id": "", "status": "failed", "amount_cents": 3690,
         "store_name": "人民广场麦当劳餐厅"},
        "麦当劳订单未提交")]


@pytest.mark.asyncio
@pytest.mark.parametrize("post_success", ["payment", "card", "ledger"])
async def test_post_success_failures_preserve_known_created_order(post_success):
    payment = FakePayment(
        ExplodingPaymentResponse() if post_success == "card"
        else RuntimeError("gateway unavailable"))
    ledger = FakeLedger(
        close_error=RuntimeError("ledger unavailable")
        if post_success == "ledger" else None)
    workflow, _ = _workflow(
        ledger=ledger,
        payment=payment if post_success in {"payment", "card"} else None)
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert result.data["order_id"] == "ORDER-001"
    assert result.data["status"] == "created"
    assert result.ui_card["type"] == "mcp_order"
    assert "可能已经" not in result.speech
    assert "m.mcd.cn" not in repr(result)


@pytest.mark.asyncio
async def test_real_create_fails_closed_when_ledger_cannot_grant_execution():
    ledger = FakeLedger(open_result=None)
    workflow, client = _workflow(ledger=ledger)
    prepared = await workflow.prepare(_intent(), CTX, META)

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert client.counts["create-order"] == 0
    assert "安全受理" in result.speech


@pytest.mark.asyncio
async def test_schema_change_after_preview_refuses_create():
    workflow, client = _workflow()
    prepared = await workflow.prepare(_intent(), CTX, META)
    workflow.tools["create-order"].input_schema["properties"]["newRequired"] = {}
    workflow.tools["create-order"].input_schema["required"].append("newRequired")

    result = await workflow.confirm(
        _intent(), CTX, {**META, "confirmed": "true"},
        token=prepared.data["checkout_token"])

    assert client.counts["create-order"] == 0
    assert "接口已更新" in result.speech

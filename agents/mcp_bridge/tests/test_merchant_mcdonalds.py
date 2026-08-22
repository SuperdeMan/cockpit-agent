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

from agents._sdk import NEED_CONFIRM, NEED_SLOT, OK
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
            # 真机形状（2026-08-13）：每条 meal 带 https 图链，域名 menu-img.mcd.cn
            "name": "巨无霸套餐", "currentPrice": "36.9",
            "originalPrice": "39", "canWithOrder": False,
            "image": "https://menu-img.mcd.cn/meal/M001.png",
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
              pay_url_hosts=None, image_hosts=None, workflow_intent="mcd.order",
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
        # searchType 不写死（2026-08-14）：workflow 按位置线索动态选 1（收藏）/2（按位置）
        "query-nearby-stores": {"beType": 1},
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
                       else ["m.mcd.cn"]),
        image_hosts=(list(image_hosts) if image_hosts is not None
                     else ["menu-img.mcd.cn"]))
    spec = SimpleNamespace(intent=workflow_intent)
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
    # 断时区**身份**不断 tzname：`_shanghai_timezone()` 两条分支的 tzname 天然不同
    # （有 tzdata 的 Linux/CI 走 ZoneInfo → "CST"；无 tzdata 的 Windows 回退定偏移
    # → "Asia/Shanghai"），断 tzname 等于只断了本机那条分支，CI 上必红。
    # `str(tzinfo)` 两条分支都是 "Asia/Shanghai"（ZoneInfo 返回 key，定偏移返回 name），
    # 且仍能区分"显式上海时区"与"随便一个 +8"——这正是本用例要守的东西。
    assert str(now.tzinfo) == "Asia/Shanghai"

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


@pytest.mark.asyncio
async def test_store_menu_answers_price_and_never_touches_write_tools():
    """当店菜单：只调两个读工具，答的是**价格**不是营养。

    这条能力存在的理由是 trace c523c303（demo-2goetq）：
    「早餐的猪柳蛋麦满分多少钱？」只能回「这个接口里只有营养信息」——
    因为当时叫 mcd.menu 的能力返回的是营养成分表。官方 query-meals 才是当店菜单。
    """
    workflow, client = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})

    intent = SimpleNamespace(name="mcd.menu",
                             slots={"store_hint": "人民广场", "city": "上海"})
    result = await workflow.menu(intent, CTX, META)

    assert [name for name, _, _ in client.calls] == [
        "query-nearby-stores", "query-meals"]
    card = result.ui_card
    assert card["type"] == "merchant_choices" and card["merchant"] == "麦当劳"
    names = [item["name"] for item in card["items"]]
    assert "巨无霸套餐" in names
    big_mac = next(i for i in card["items"] if i["name"] == "巨无霸套餐")
    # 价格取真机字段 currentPrice（**字符串**不是数字），不是猜的键名
    assert big_mac["price"] == "36.90 元"
    assert big_mac["image_url"] == "https://menu-img.mcd.cn/meal/M001.png"
    assert "36.90 元" in result.speech


@pytest.mark.asyncio
async def test_store_menu_declares_the_group_label_users_will_name_it_by(
):
    """候选组标签（`_candidate_label`，I-030）必须与**卡上给用户看的称呼**是同一个。

    判据同 `_fallback`：编排看不出 `mcd.menu` 那一组该叫「麦当劳」，只有产生方
    知道。没有它，两家菜单并存时「麦当劳的第二个多少钱」会被绑到最新那一组，
    **确定性地**答出另一家的真商品真价格——名字与价格都对得上，所以比编造更难
    被发现（真栈取证把 I-030 的定性从「答不出来」改成了这一档）。

    ⚠ 断言的是**两处相等**而不是字面量：用户是照卡上那个称呼点名的，
    两处各写一份就会在改名那天只改一处。
    """
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})
    result = await workflow.menu(
        SimpleNamespace(name="mcd.menu", slots={"store_hint": "人民广场"}),
        CTX, META)
    assert result.data["_candidate_label"] == result.ui_card["merchant"] == "麦当劳"


@pytest.mark.asyncio
async def test_store_menu_image_requires_https_and_an_allowlisted_host():
    """商品图链是外部输入，且会变成 HMI 的一次网络请求——白名单在基类，两家共用一份。"""
    for bad in ("http://menu-img.mcd.cn/x.png", "https://evil.example.com/x.png",
                "https://user:pw@menu-img.mcd.cn/x.png", "/x.png", ""):
        menu = copy.deepcopy(MENU_RESULT)
        menu["data"]["data"]["meals"]["M001"]["image"] = bad
        workflow, _ = _workflow(
            workflow_intent="mcd.menu",
            scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [menu]})
        intent = SimpleNamespace(name="mcd.menu",
                                 slots={"store_hint": "人民广场", "city": "上海"})
        card = (await workflow.menu(intent, CTX, META)).ui_card
        item = next(i for i in card["items"] if i["name"] == "巨无霸套餐")
        assert "image_url" not in item, bad


@pytest.mark.asyncio
async def test_store_menu_declares_only_the_two_read_tools():
    """逐 intent 工具契约：菜单 workflow 只拿到它自己声明的两个读工具就能构造。"""
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})
    assert set(workflow.tools) >= {"query-nearby-stores", "query-meals"}
    with pytest.raises(ValueError, match="tool contract"):
        _workflow(workflow_intent="mcd.unknown")


@pytest.mark.asyncio
async def test_store_menu_never_relabels_unrelated_items_as_the_asked_one():
    """问某一款而菜单里没有 → 诚实说没有，**绝不回落成整份菜单**。

    2026-08-13 真栈抓到的形态：`or products` 的兜底让
    「猪柳蛋麦满分多少钱」答成「…的“猪柳蛋麦满分”有：蘸酱韩式甜辣酱鸡块（11.90 元）…」
    ——把一堆无关餐品挂在用户问的那个名字下面。比答不出来糟得多。
    """
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})
    intent = SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海", "item_query": "猪柳蛋麦满分"})

    result = await workflow.menu(intent, CTX, META)

    assert result.status == NEED_SLOT
    assert "没查到" in result.speech and "猪柳蛋麦满分" in result.speech
    assert result.ui_card is None, "没查到就不该出选品卡"
    assert "巨无霸套餐" not in result.speech


@pytest.mark.asyncio
async def test_store_menu_recovers_when_planner_puts_the_whole_sentence_in_the_slot():
    """planner 把整句塞进 item_query 时，菜单路径用反向包含兜住。

    真栈实测 `item_query="深圳的麦当劳巨无霸多少钱"`——严格判据只做「查询词 ⊂ 商品名」，
    整句必然落空。菜单是只读展示，放宽成「商品名 ⊂ 整句」安全；下单路径不放宽。
    """
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})
    intent = SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海",
        "item_query": "上海人民广场的麦当劳巨无霸套餐多少钱"})

    result = await workflow.menu(intent, CTX, META)

    names = [item["name"] for item in result.ui_card["items"]]
    assert names == ["巨无霸套餐"], names
    assert "36.90 元" in result.speech


# ── demo-3ukshz 二轮：附近选店接线 + 分类导航 + 默认店诚实化 ────────────────────


def test_store_keyword_normalizes_amap_poi_names():
    """高德 POI 名（「麦当劳(深圳科苑南路餐厅)」）→ 官方检索词（「深圳科苑南路餐厅」）。

    这是「附近的麦当劳」接线的关键一跳（demo-3ukshz #1）：整串带品牌带括号发给
    query-nearby-stores 多半 0 命中，桥静默退回默认店，「附近」变成十公里外的碧海君庭。
    """
    kw = McDonaldsWorkflow._store_keyword
    assert kw("麦当劳(深圳科苑南路餐厅)") == "深圳科苑南路餐厅"
    assert kw("麦当劳（碧海君庭餐厅）") == "碧海君庭餐厅"
    assert kw("McDonald's(人民广场餐厅)") == "人民广场餐厅"
    assert kw("人民广场") == "人民广场"          # 无壳可剥原样保留
    assert kw("麦当劳") == "麦当劳"              # 剥完为空退回原串，不发明检索词
    assert kw("") == ""


@pytest.mark.asyncio
async def test_menu_amap_named_store_reaches_the_official_store():
    """nearby.search 的门店名经 store_hint 直达官方门店（附近链路的桥侧一半）。

    searchType 语义（2026-08-14 真机 schema 取证）：hint+city 俱备才走 2
    （按位置搜索，city/keyword 官方必填）；缺 city 退 1（收藏列表），由本地
    `_matching_stores` 继续按名过滤。"""
    workflow, client = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    intent = SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "麦当劳(人民广场麦当劳餐厅)", "city": "上海"})
    result = await workflow.menu(intent, CTX, META)

    assert result.ui_card["store_name"] == "人民广场麦当劳餐厅"
    nearby_args = next(args for name, args, _ in client.calls
                       if name == "query-nearby-stores")
    assert nearby_args["searchType"] == 2
    assert nearby_args["keyword"] == "人民广场麦当劳餐厅"
    assert nearby_args["city"] == "上海"
    assert "没指定门店" not in result.speech

    # 缺 city → 退收藏档（searchType=1，不传位置参数），本地匹配兜住
    flow2, client2 = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    no_city = await flow2.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "麦当劳(人民广场麦当劳餐厅)", "city": ""}), CTX, META)
    args2 = next(args for name, args, _ in client2.calls
                 if name == "query-nearby-stores")
    assert args2["searchType"] == 1
    assert "keyword" not in args2 and "city" not in args2
    assert no_city.ui_card["store_name"] == "人民广场麦当劳餐厅"


@pytest.mark.asyncio
async def test_menu_carries_categories_and_supports_category_browsing():
    """分类导航（demo-3ukshz #2）：卡带 categories chips 与诚实总量；
    category 槽按分类过滤；话术报「共 N 款、M 个分类」并给分类示范。"""
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    intent = SimpleNamespace(name="mcd.menu",
                             slots={"store_hint": "人民广场", "city": "上海"})
    result = await workflow.menu(intent, CTX, META)

    card = result.ui_card
    assert card["total"] == 2
    assert card["categories"] == [
        {"label": "套餐", "send_text": "看看人民广场麦当劳餐厅的套餐"},
        {"label": "热门", "send_text": "看看人民广场麦当劳餐厅的热门"},
    ]

    flow2, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    in_category = await flow2.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海", "category": "套餐"}), CTX, META)
    names = [item["name"] for item in in_category.ui_card["items"]]
    assert names == ["巨无霸套餐", "双层吉士堡套餐"]
    assert "「套餐」" in in_category.speech

    # 同 code 多分类：归属必须全保留——「热门」的唯一一款被「套餐」先去重走了，
    # 按热门过滤仍要能命中它，否则聚合分类全是死链。
    flow_hot, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    hot = await flow_hot.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海", "category": "热门"}), CTX, META)
    assert [item["name"] for item in hot.ui_card["items"]] == ["巨无霸套餐"]

    flow3, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    missing = await flow3.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海", "category": "甜品站"}), CTX, META)
    assert missing.status == NEED_SLOT
    assert "套餐" in missing.speech        # 给出现有分类，不留死胡同
    assert missing.missing_slots == ["category"]


@pytest.mark.asyncio
async def test_named_store_not_in_official_list_is_never_silently_replaced():
    """点名的店官方查无 → 绝不拿别的店顶替（与瑞幸同判据）。

    二轮旅程探针实证：官方测试租户没有「高新中五道」，接口对未命中 keyword 返回
    默认店列表；「唯一候选直选」曾把点名的店静默换成碧海君庭。唯一可用店时给
    确定句式（候选卡按钮同款）让用户自己定，且必须打 `_refused`——组合轮里这句
    由聚合器确定性附加，不会再被 LLM 吞掉。"""
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    result = await workflow.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "麦当劳(高新中五道餐厅)", "city": ""}), CTX, META)

    assert (result.data or {}).get("_refused") is True
    assert "高新中五道餐厅" in result.speech and "没查到" in result.speech
    assert "选择麦当劳门店：人民广场麦当劳餐厅" in result.speech
    assert not result.missing_slots, "拒绝不得挂起会话"
    assert "碧海君庭" not in result.speech or "人民广场" in result.speech
    # 自愈一跳（镜像瑞幸）：拿点名的店去 nearby.search 真实取回，
    # 门店列表随即写回焦点（含 city），下一句选店就有城市可走位置检索
    assert (result.data or {}).get("_escalate") == {
        "intent": "nearby.search",
        "slots": {"keyword": "麦当劳 高新中五道餐厅"},
        "reason": "mcd_store_unresolved",
    }


@pytest.mark.asyncio
async def test_menu_category_word_in_item_query_still_filters_by_category():
    """planner 偶发把分类词塞进 item_query（二轮旅程探针：「看看X的人气热卖」→
    item_query=人气热卖 → 「没查到这个餐品」死路）。商品没命中时拿它去分类名里
    再试——槽位落哪都工作。"""
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    result = await workflow.menu(SimpleNamespace(name="mcd.menu", slots={
        "store_hint": "人民广场", "city": "上海", "item_query": "热门"}), CTX, META)

    assert result.status == OK
    assert [item["name"] for item in result.ui_card["items"]] == ["巨无霸套餐"]
    assert "「热门」" in result.speech


@pytest.mark.asyncio
async def test_menu_without_any_store_hint_discloses_the_default_store():
    """默认店诚实化（demo-3ukshz #1）：没有任何门店线索时选出的是商户接口默认店，
    不是「你附近」——必须说破并给出「附近的麦当劳」这条正路。"""
    workflow, _ = _workflow(
        workflow_intent="mcd.menu",
        scripts={"query-nearby-stores": [STORE_RESULT],
                 "query-meals": [MENU_RESULT]})
    intent = SimpleNamespace(name="mcd.menu", slots={"store_hint": "", "city": ""})
    result = await workflow.menu(intent, CTX, META)

    assert "没指定门店" in result.speech
    assert "人民广场麦当劳餐厅" in result.speech
    assert "附近的麦当劳" in result.speech

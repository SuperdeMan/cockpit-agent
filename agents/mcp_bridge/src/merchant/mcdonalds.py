"""麦当劳官方 MCP 的确定性选店、菜单、计价与未支付订单工作流。"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents._sdk import AgentResult, NEED_CONFIRM, NEED_SLOT
from agents._sdk.ledger import DONE, FAILED, Duplicate, idem_key

from ..admission import normalize_hostname
from ..mcp_client import McpTimeout
from .base import DeclaredBusinessRejected, MerchantWorkflow
from .models import MerchantChoice, MerchantDraft, MerchantItem, MerchantResult, yuan_to_cents


logger = logging.getLogger("agent.mcp_bridge.merchant.mcdonalds")

_LEDGER_KIND = "mcp_order"
_READ_TOOLS = (
    "query-nearby-stores",
    "query-meals",
    "query-meal-detail",
    "calculate-price",
)
_ALL_TOOLS = _READ_TOOLS + ("create-order",)


def _shanghai_timezone():
    """Use IANA Shanghai time, with a UTC+8 fallback for Windows without tzdata."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


_SHANGHAI_TZ = _shanghai_timezone()


def _now_shanghai() -> datetime:
    return datetime.now(_SHANGHAI_TZ)


class _BusinessError(RuntimeError):
    """协议成功、但官方业务 envelope 未确认成功。"""


class _SelectionRequired(_BusinessError):
    """官方详情没有足够的默认值，必须让用户重选而不是猜 code。"""


class _BusinessRejected(_BusinessError):
    """商户明确拒绝了请求；已知没有创建订单。"""


class _IncompleteResponse(_BusinessError):
    """请求已发出，但响应不足以证明写操作结局。"""


class McDonaldsWorkflow(MerchantWorkflow):
    merchant = "mcdonalds"
    intents = ("mcd.order",)

    def __init__(self, server, workflow_spec, tools_by_name, draft_store,
                 ledger, payment, *, clock=None):
        self.server = server
        self.workflow_spec = workflow_spec
        self.tools = dict(tools_by_name or {})
        self.drafts = draft_store
        self.ledger = ledger
        self.payment = payment
        self._clock = clock or _now_shanghai
        missing = [name for name in _ALL_TOOLS if name not in self.tools]
        if missing:
            raise ValueError(f"mcdonalds workflow missing tools: {','.join(missing)}")

    async def prepare(self, intent, ctx, meta) -> AgentResult:
        slots = dict(getattr(intent, "slots", {}) or {})
        item_query = self._choice_value(slots.get("item_query"), "餐品")
        if not item_query:
            return AgentResult(
                status=NEED_SLOT, speech="想点哪一款麦当劳餐品？",
                follow_up="请说具体餐品，例如“巨无霸套餐”。",
                missing_slots=["item_query"])
        quantity = self._quantity(slots.get("quantity", "1"))
        if quantity is None:
            return AgentResult(
                status=NEED_SLOT, speech="数量需要是 1 到 20 之间的整数。",
                follow_up="请告诉我要几份。", missing_slots=["quantity"])

        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return AgentResult(speech="当前会话身份不完整，暂时不能创建真实订单。")

        store_hint = self._choice_value(slots.get("store_hint"), "门店")
        city = str(slots.get("city") or "").strip()
        try:
            stores_data = await self._read(
                "query-nearby-stores",
                self._arguments("query-nearby-stores", {
                    "keyword": store_hint,
                    "city": city,
                }))
        except Exception as exc:
            return self._read_failure("查询麦当劳门店", exc)

        stores = self._stores(stores_data)
        if not stores:
            return AgentResult(
                speech="没有找到可用的麦当劳门店。可以先在麦当劳官方应用选择常用门店，"
                       "或提供更具体的门店名称。")
        candidate_stores = self._matching_stores(stores, store_hint) or stores
        now = self._clock()
        open_stores = [store for store in candidate_stores
                       if self._store_orderable(store, slots, now=now)]
        if not open_stores:
            if any(self._store_status(store) is None
                   for store in candidate_stores):
                return AgentResult(
                    speech="商家没有返回可靠的门店营业状态，无法安全下单。"
                           "请在麦当劳官方应用确认营业后再试。")
            return AgentResult(
                speech="找到的麦当劳门店均已打烊。可以换一家门店或稍后再试。")
        store_matches = self._matching_stores(open_stores, store_hint)
        if len(store_matches) != 1:
            choices = store_matches or open_stores
            return self._store_choices(choices)
        store = store_matches[0]
        store_code = str(store.get("storeCode") or "")
        if not store_code:
            # NEED_SLOT 而非默认 OK：本工作流 require_confirm=true，中央确认闸会给
            # 任何 OK 结果追加「确定继续吗？」，拒绝落在 OK 上就成了自相矛盾的一句。
            # 与瑞幸侧 _reselect_store 同一处理，理由见那里的注释。
            return AgentResult(
                status=NEED_SLOT,
                speech="门店信息不完整，不能继续下单，请换一家门店。",
                follow_up="换一家麦当劳门店试试？",
                missing_slots=["store_hint"])

        try:
            store_context = self._store_context(store, slots, now=now)
        except _SelectionRequired as exc:
            return AgentResult(
                status=NEED_SLOT,
                speech=str(exc),
                follow_up="请选择该门店可用的预约时间，或改为立即取餐。",
                missing_slots=["reservation_date"])

        try:
            menu_data = await self._read(
                "query-meals",
                self._arguments("query-meals", store_context))
        except Exception as exc:
            return self._read_failure("查询该门店菜单", exc)
        products = self._menu_products(menu_data)
        product_matches = self._matching_products(products, item_query)
        if not product_matches:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"这家门店没有找到“{item_query}”。可以换个餐品名称。",
                follow_up="请说另一款餐品。", missing_slots=["item_query"])
        if len(product_matches) != 1:
            return self._product_choices(product_matches)
        product = product_matches[0]
        product_code = str(product.get("code") or "")
        if not product_code:
            return AgentResult(
                status=NEED_SLOT,
                speech="商品信息不完整，不能继续下单，请换一款商品。",
                follow_up="换一款餐品试试？", missing_slots=["item_query"])

        try:
            detail_data = await self._read(
                "query-meal-detail",
                self._arguments("query-meal-detail", {
                    **store_context,
                    "code": product_code,
                }))
            detail = self._meal_detail(detail_data, product_code)
            request_item, specifications = self._build_item(
                detail, product_code=product_code, quantity=quantity)
            calculate_data = await self._read(
                "calculate-price",
                self._arguments("calculate-price", {
                    **store_context,
                    "items": [request_item],
                }))
        except _SelectionRequired:
            return AgentResult(
                status=NEED_SLOT,
                speech="这个套餐没有可安全采用的官方默认规格，请换一款具体套餐后再试。",
                follow_up="请重新选择餐品。", missing_slots=["item_query"])
        except Exception as exc:
            return self._read_failure("核对商品详情和价格", exc)

        try:
            calculated = self._calculation(
                calculate_data, request_item=request_item,
                requested_takeway=str(
                    slots.get("pickup_mode") or "到店自取"))
        except _SelectionRequired as exc:
            return AgentResult(
                status=NEED_SLOT,
                speech=str(exc),
                follow_up="请选择商家提供的堂食或到店自取。",
                missing_slots=["pickup_mode"])
        except _BusinessError:
            return AgentResult(
                speech="所选餐品当前已售罄或不可下单，请换一款餐品。")

        amount_cents = calculated["amount_cents"]
        original_cents = calculated["original_cents"]
        discount_cents = calculated["discount_cents"]
        take_way = calculated["take_way"]
        display_name = (calculated["product_name"] or
                        self._product_name(product) or
                        str(detail.get("name") or "所选套餐"))
        merchant_item = MerchantItem(
            name=display_name,
            quantity=quantity,
            product_id=product_code,
            specifications=specifications,
            amount_cents=int(calculated["product_amount_cents"]),
        )
        upstream_args = self._arguments("create-order", {
            **store_context,
            # create 与 calculate 共用由详情 builder 构造的 typed items；
            # productList 仅用于对账，绝不盲拷成写参数。
            "items": [copy.deepcopy(request_item)],
            "takeWayCode": str(take_way.get("code") or ""),
        })
        if not self._required_present("create-order", upstream_args):
            return AgentResult(speech="订单参数不完整，暂时不能创建真实订单。")

        fulfillment = str(take_way.get("title") or take_way.get("name") or "")
        if store_context.get("reservationDate"):
            fulfillment += f"，预约 {store_context['reservationDate']}"
        draft = MerchantDraft(
            token=secrets.token_urlsafe(24),
            merchant=self.merchant,
            operation="create",
            user_id=user_id,
            session_id=session_id,
            store={
                "id": store_code,
                "be_code": str(store.get("beCode") or ""),
                "name": self._store_name(store),
                "reservation_date": str(
                    store_context.get("reservationDate") or ""),
            },
            items=[merchant_item],
            amount_cents=amount_cents,
            original_amount_cents=original_cents,
            discount_cents=discount_cents,
            fulfillment=fulfillment,
            upstream_args=upstream_args,
            schema_digest=self._schema_digest(),
            created_at=time.time(),
        )
        if not await self.drafts.put(draft):
            return AgentResult(speech="订单预览暂时无法安全保存，请稍后重新下单。")
        speech = self.preview_speech(draft, unpaid_expiry=True)
        speech += f"取餐方式：{draft.fulfillment}。"
        return AgentResult(
            status=NEED_CONFIRM,
            speech=speech,
            ui_card=self.preview_card(draft),
            data={"checkout_token": draft.token, "summary": speech})

    async def confirm(self, intent, ctx, meta, token: str = "",
                      _leased_draft: MerchantDraft | None = None) -> AgentResult:
        if not self._confirmed(meta):
            return AgentResult(
                status=NEED_CONFIRM,
                speech="请先核对门店、餐品、取餐方式和金额，再点击确认创建未支付订单。")
        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return AgentResult(speech="当前会话身份不完整，不能确认真实订单。")
        if _leased_draft is not None:
            draft = _leased_draft
        elif token:
            draft = await self.drafts.consume(
                token, user_id=user_id, session_id=session_id,
                merchant=self.merchant, expected_action="create")
        else:
            draft = await self.drafts.consume_current(
                user_id=user_id, session_id=session_id,
                merchant=self.merchant, expected_action="create")
        if draft is None:
            return AgentResult(speech="订单预览已失效，请重新选择商品并确认。")
        if _leased_draft is None:
            async with self.drafts.operation_hold(
                    draft.token, user_id=draft.user_id) as held:
                if not held:
                    return self._lease_expired_result()
                return await self.confirm(
                    intent, ctx, meta, token=token, _leased_draft=draft)
        if draft.schema_digest != self._schema_digest():
            return AgentResult(speech="商家接口已更新，这份预览已失效，请重新预览后再确认。")

        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_expired_result()

        # 确认轮必须用原 typed items 重新向商户计价。价格、商品展示或取餐方式
        # 任一变化都只能生成新快照并再次确认，绝不能沿用旧确认直接 create。
        try:
            items = copy.deepcopy(draft.upstream_args.get("items"))
            if not isinstance(items, list) or len(items) != 1:
                raise _BusinessError("draft typed items missing")
            calculate_args = self._arguments("calculate-price", {
                key: copy.deepcopy(value)
                for key, value in draft.upstream_args.items()
                if key != "takeWayCode"
            })
            current = await self._read("calculate-price", calculate_args)
            calculated = self._calculation(
                current, request_item=items[0],
                requested_takeway=draft.fulfillment,
                selected_takeway_code=str(
                    draft.upstream_args.get("takeWayCode") or ""))
        except _SelectionRequired as exc:
            return AgentResult(
                status=NEED_SLOT, speech=str(exc),
                follow_up="请重新选择取餐方式并预览订单。",
                missing_slots=["pickup_mode"])
        except Exception as exc:
            logger.warning("麦当劳确认前重新计价失败：%s", type(exc).__name__)
            return AgentResult(
                speech="确认前无法重新核对商品和价格，没有创建订单。"
                       "请重新选择餐品后再试。")

        refreshed = self._refresh_draft(draft, calculated)
        if self._draft_changed(draft, refreshed):
            if not await self.drafts.put(
                    refreshed, lease_token=draft.token):
                return AgentResult(
                    speech="商家信息有变化，但新预览无法安全保存，没有创建订单。"
                           "请稍后重新下单。")
            speech = self.preview_speech(refreshed, unpaid_expiry=True)
            speech += f"取餐方式：{refreshed.fulfillment}。"
            speech = "商家价格、商品信息或取餐方式有变化，请重新确认。" + speech
            return AgentResult(
                status=NEED_CONFIRM, speech=speech,
                ui_card=self.preview_card(refreshed),
                data={"checkout_token": refreshed.token, "summary": speech})

        task = await self._open_ledger(draft, ctx)
        if isinstance(task, Duplicate):
            return AgentResult(speech="这份订单已经在受理中，请不要重复确认。")
        if task is None:
            return AgentResult(
                speech="暂时无法安全受理真实订单，没有向麦当劳提交，请重新预览后再试。")

        try:
            if not await self.drafts.authorize(
                    draft.token, user_id=draft.user_id):
                return self._lease_expired_result()
            response = await self.call_tool(
                "create-order", copy.deepcopy(draft.upstream_args), write=True)
            payload = self._business_data(response, write=True)
            order_id = str(payload.get("orderId") or "").strip()
            if not order_id:
                raise _IncompleteResponse("create-order missing orderId")
        except (_BusinessRejected, DeclaredBusinessRejected):
            await self._record_outcome(
                task, draft, status="failed", progress="麦当劳拒绝创建订单")
            return AgentResult(
                speech="麦当劳明确拒绝了这次下单，没有创建订单。"
                       "请检查餐品或门店后重新预览。",
                data={"merchant": self.merchant, "status": "failed"})
        except McpTimeout as exc:
            if not exc.sent:
                await self._record_outcome(
                    task, draft, status="failed", progress="麦当劳订单未提交")
                return AgentResult(
                    speech="连接麦当劳时超时，订单没有提交。"
                           "请重新预览后再试。",
                    data={"merchant": self.merchant, "status": "failed"})
            await self._record_outcome(
                task, draft, status="uncertain", progress="麦当劳下单结果不确定")
            return self._uncertain_result()
        except asyncio.CancelledError:
            await self._record_outcome(
                task, draft, status="uncertain", progress="麦当劳下单结果不确定")
            raise
        except Exception as exc:
            logger.warning("麦当劳写调用结局不确定：%s", type(exc).__name__)
            await self._record_outcome(
                task, draft, status="uncertain", progress="麦当劳下单结果不确定")
            return self._uncertain_result()

        amount_cents = draft.amount_cents
        merchant_total = self._create_amount(payload)
        amount_mismatch = (merchant_total is None or
                           merchant_total != draft.amount_cents)
        result = MerchantResult(
            server=str(self.server.id),
            merchant=self.merchant,
            order_id=order_id,
            status="created",
            amount_cents=amount_cents,
            store_name=str((draft.store or {}).get("name") or ""),
            items=[{
                "name": item.name,
                "quantity": item.quantity,
                "specifications": list(item.specifications),
            } for item in draft.items],
        )
        ref = result.ledger_ref()
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_lost_after_write_result()
        synced = await self._close_verified_ledger(
            task, DONE, ref, progress="麦当劳订单已创建")
        speech = f"麦当劳未支付订单已创建，订单号 {order_id}。"
        if amount_mismatch:
            speech += "商家返回的金额与预览不一致，请先到官方应用核对，不要支付。"
        if not synced:
            speech += "本地记录同步异常，请勿重复下单。"
        buttons = [
            {"label": "查订单", "send_text": f"查询麦当劳订单 {order_id}"},
            {"label": "放弃支付", "send_text": "放弃支付这笔麦当劳订单"},
        ]
        card = {
            "type": "mcp_order",
            **ref,
            "items": result.items,
            "buttons": copy.deepcopy(buttons),
        }
        if amount_mismatch:
            return AgentResult(speech=speech, ui_card=card, data=ref)

        payment_card = None
        if await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            payment_card = await self._payment_card(
                ctx, intent, draft, ref, payload=payload, buttons=buttons)
        if payment_card is not None:
            if payment_card.get("qr_svg"):
                speech += "尚未付款，请扫码完成支付。"
            else:
                speech += "尚未付款，请打开安全支付链接完成支付。"
            card = payment_card
        else:
            speech += "支付入口暂不可用，请到麦当劳官方应用支付。"
        return AgentResult(speech=speech, ui_card=card, data=ref)

    async def _open_ledger(self, draft: MerchantDraft, ctx):
        if self.ledger is None:
            return None
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return None
        item_summary = ",".join(f"{item.product_id}x{item.quantity}"
                                for item in draft.items)
        goal = f"{draft.store.get('id', '')}|{item_summary}|{draft.amount_cents}"
        try:
            return await self.ledger.open(
                draft.user_id, draft.session_id, str(self.server.id),
                _LEDGER_KIND, goal,
                origin_trace_id=str(getattr(ctx, "trace_id", "") or ""),
                idempotency_goal=f"mcdonalds:{draft.token}")
        except Exception as exc:
            logger.warning("麦当劳 Ledger.open 失败：%s", type(exc).__name__)
            return None

    async def _record_outcome(self, task, draft: MerchantDraft, *,
                              status: str, progress: str) -> None:
        # 失败/不确定也沿用 MerchantResult 的持久化 allowlist；不把远端
        # message、支付 URL、地址、手机号或异常文本写进账本。
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return
        ref = MerchantResult(
            server=str(self.server.id), merchant=self.merchant,
            status=status, amount_cents=draft.amount_cents,
            store_name=str((draft.store or {}).get("name") or ""),
        ).ledger_ref()
        await self._close_ledger(task, FAILED, ref, progress=progress)

    def _uncertain_result(self) -> AgentResult:
        return AgentResult(
            speech="麦当劳下单没有拿到可靠结果，可能已经受理。"
                   "请不要重复下单，先到麦当劳官方应用核对订单。",
            data={"merchant": self.merchant, "status": "uncertain"})

    def _lease_expired_result(self) -> AgentResult:
        return AgentResult(
            speech="订单确认已失效，没有继续计价、记账或向麦当劳提交。"
                   "请重新预览后再试。")

    def _lease_lost_after_write_result(self) -> AgentResult:
        return AgentResult(
            speech="麦当劳下单已提交，但本地授权在结果落账前过期，"
                   "这笔订单可能已经受理。请不要重复下单，"
                   "先到麦当劳官方应用核对。",
            data={"merchant": self.merchant, "status": "uncertain"})

    async def _close_ledger(self, task, status: str, result_ref: dict, *,
                            progress: str) -> bool:
        try:
            return bool(await self.ledger.close(
                task.task_id, status, result_ref=result_ref, progress=progress))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("麦当劳 Ledger.close 失败：%s", type(exc).__name__)
            return False

    async def _close_verified_ledger(self, task, status: str, result_ref: dict,
                                     *, progress: str) -> bool:
        """Finish this known-success close, then preserve caller cancellation."""
        close_task = asyncio.create_task(self.ledger.close(
            task.task_id, status, result_ref=result_ref, progress=progress))
        try:
            return bool(await asyncio.shield(close_task))
        except asyncio.CancelledError:
            try:
                # This task is already a side effect.  Never detach it from the
                # owner lease: deletion must stay pending until it settles.
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await close_task
                raise
            except Exception as exc:
                logger.warning("麦当劳已成功订单落账失败：%s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.warning("麦当劳已成功订单落账失败：%s", type(exc).__name__)
            return False

    async def _read(self, name: str, arguments: dict) -> dict:
        if not self._required_present(name, arguments):
            raise _BusinessError(f"{name} required arguments missing")
        response = await self.call_tool(name, arguments, write=False)
        return self._business_data(response, write=False)

    @staticmethod
    def _business_data(response: dict, *, write: bool = False) -> dict | list:
        incomplete = _IncompleteResponse if write else _BusinessError
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise incomplete("MCP protocol result not ok")
        envelope = response.get("data")
        if not isinstance(envelope, dict):
            raise incomplete("business envelope missing")
        if envelope.get("success") is not True:
            raise _BusinessRejected("business success is not true")
        code = envelope.get("code")
        if isinstance(code, bool) or code != 200:
            raise _BusinessRejected("business code is not 200")
        payload = envelope.get("data")
        if not isinstance(payload, (dict, list)):
            raise incomplete("business data missing")
        return payload

    def _arguments(self, name: str, dynamic: dict) -> dict:
        binding = self.tools[name]
        result = copy.deepcopy(getattr(binding.tool, "const_args", {}) or {})
        result.update({key: copy.deepcopy(value) for key, value in dynamic.items()
                       if value not in (None, "")})
        properties = (binding.input_schema or {}).get("properties") or {}
        if properties:
            result = {key: value for key, value in result.items()
                      if key in properties}
        return result

    def _required_present(self, name: str, arguments: dict) -> bool:
        required = (self.tools[name].input_schema or {}).get("required") or []
        return all(key in arguments and arguments[key] not in (None, "")
                   for key in required)

    def _schema_digest(self) -> str:
        payload = {}
        for name in _ALL_TOOLS:
            binding = self.tools[name]
            payload[name] = {
                "schema": binding.input_schema,
                "const_args": getattr(binding.tool, "const_args", {}) or {},
            }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _owner(ctx) -> tuple[str, str]:
        return (str(getattr(ctx, "user_id", "") or ""),
                str(getattr(ctx, "session_id", "") or ""))

    @staticmethod
    def _choice_value(value, kind: str) -> str:
        """Unwrap only the fixed HMI button sentence generated by this codec."""
        text = str(value or "").strip()
        prefix = f"选择麦当劳{kind}："
        return text[len(prefix):].strip() if text.startswith(prefix) else text

    @staticmethod
    def _quantity(value) -> int | None:
        text = str(value).strip()
        if not re.fullmatch(r"[0-9]+", text):
            return None
        quantity = int(text)
        return quantity if 1 <= quantity <= 20 else None

    @staticmethod
    def _stores(data) -> list[dict]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("stores", "storeList", "list"):
                values = data.get(key)
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, dict)]
        return []

    @staticmethod
    def _store_name(store: dict) -> str:
        return str(store.get("storeName") or store.get("name") or "")

    @classmethod
    def _matching_stores(cls, stores: list[dict], hint: str) -> list[dict]:
        if not hint:
            return list(stores)
        needle = cls._normalized(hint)
        return [store for store in stores
                if needle in cls._normalized(cls._store_name(store))]

    @staticmethod
    def _store_status(store: dict) -> bool | None:
        value = store.get("businessStatus")
        if isinstance(value, bool):
            return value
        # 兼容旧字段，但只接受显式、可审计的开/关枚举；缺失或陌生值
        # 一律 unknown，不能把“非 CLOSED”误当成营业中。
        raw = str(store.get("workStatus") or "").strip().lower()
        if raw in {"open", "opened", "1", "营业", "营业中"}:
            return True
        if raw in {"closed", "close", "closing", "0", "已打烊", "打烊", "停业"}:
            return False
        return None

    @classmethod
    def _store_orderable(cls, store: dict, slots: dict, *,
                         now: datetime | None = None) -> bool:
        status = cls._store_status(store)
        if status is not None:
            return status
        explicit = str(slots.get("reservation_date") or "").strip()
        if explicit:
            return bool(
                store.get("reservation") is True and
                re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", explicit) and
                cls._reservation_allowed(
                    explicit, store.get("reservationTimeOptions"), now=now))
        return bool(cls._auto_reservation(
            store.get("reservationTimeOptions"), now=now))

    @classmethod
    def _store_context(cls, store: dict, slots: dict, *,
                       now: datetime | None = None) -> dict:
        context = {"storeCode": str(store.get("storeCode") or "")}
        be_code = str(store.get("beCode") or "").strip()
        if be_code:
            context["beCode"] = be_code
        reservation_date = str(slots.get("reservation_date") or "").strip()
        if not reservation_date and cls._store_status(store) is None:
            reservation_date = cls._auto_reservation(
                store.get("reservationTimeOptions"), now=now)
            if not reservation_date:
                raise _SelectionRequired(
                    "商家没有返回可靠的营业状态或可用预约窗口，无法安全下单。")
        if not reservation_date:
            return context
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", reservation_date):
            raise _SelectionRequired("预约时间格式不正确，请选择商家提供的预约时段。")
        if store.get("reservation") is not True:
            raise _SelectionRequired("这家门店不支持预约，请改为立即取餐。")
        if not cls._reservation_allowed(
                reservation_date, store.get("reservationTimeOptions"), now=now):
            raise _SelectionRequired("该预约时间不在这家门店当前可选窗口内。")
        context["reservationDate"] = reservation_date
        return context

    @staticmethod
    def _auto_reservation(options, *, now: datetime | None = None) -> str:
        """从官方公开窗口选下一安全时刻；不猜门店营业状态。

        商户实测只返回 ``reservationTimeOptions``，格式如
        ``早餐(07:14至10:15)，午餐(...)``。选择至少提前 15 分钟、向上取整
        到 15 分钟的最早时刻，既避免卡在窗口起点，也保持可复现。
        """
        now = now or _now_shanghai()
        not_before = now + timedelta(minutes=15)
        candidates: list[datetime] = []
        for option in options or []:
            if not isinstance(option, dict):
                continue
            date = str(option.get("date") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            text = str(option.get("reservationOptionText") or "")
            for sh, sm, eh, em in re.findall(
                    r"(\d{2}):(\d{2})\s*(?:至|-)\s*(\d{2}):(\d{2})", text):
                try:
                    start = datetime.fromisoformat(f"{date} {sh}:{sm}")
                    end = datetime.fromisoformat(f"{date} {eh}:{em}")
                    if now.tzinfo is not None:
                        start = start.replace(tzinfo=now.tzinfo)
                        end = end.replace(tzinfo=now.tzinfo)
                except ValueError:
                    continue
                if end < start:
                    end += timedelta(days=1)
                candidate = max(start + timedelta(minutes=15), not_before)
                # 向上取整到 15 分钟，秒/微秒也算进下一档。
                if candidate.second or candidate.microsecond or candidate.minute % 15:
                    had_partial_minute = bool(
                        candidate.second or candidate.microsecond)
                    candidate = candidate.replace(second=0, microsecond=0)
                    if had_partial_minute:
                        candidate += timedelta(minutes=1)
                    candidate += timedelta(minutes=(-candidate.minute) % 15)
                if candidate <= end:
                    candidates.append(candidate)
        if not candidates:
            return ""
        return min(candidates).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _reservation_allowed(value: str, options, *,
                             now: datetime | None = None) -> bool:
        date, wanted = value.split(" ", 1)
        current = now or _now_shanghai()
        try:
            wanted_at = datetime.fromisoformat(value)
        except ValueError:
            return False
        if current.tzinfo is not None:
            wanted_at = wanted_at.replace(tzinfo=current.tzinfo)
        if wanted_at < current + timedelta(minutes=15):
            return False
        wanted_minutes = int(wanted[:2]) * 60 + int(wanted[3:])
        for option in options or []:
            if not isinstance(option, dict) or str(option.get("date") or "") != date:
                continue
            text = str(option.get("reservationOptionText") or "")
            ranges = re.findall(r"(\d{2}):(\d{2})\s*(?:至|-)\s*(\d{2}):(\d{2})", text)
            for sh, sm, eh, em in ranges:
                start = int(sh) * 60 + int(sm)
                end = int(eh) * 60 + int(em)
                if start <= wanted_minutes <= end:
                    return True
        return False

    def _store_choices(self, stores: list[dict]) -> AgentResult:
        choices = [MerchantChoice(
            id=str(store.get("storeCode") or ""),
            name=self._store_name(store) or "麦当劳门店",
            subtitle=("营业中" if self._store_status(store) is True
                      else "支持预约"),
            send_text=f"选择麦当劳门店：{self._store_name(store)}",
            data={"store_code": str(store.get("storeCode") or "")},
        ) for store in stores[:3]]
        return AgentResult(
            status=NEED_SLOT,
            speech="找到多家可用的麦当劳门店，请选择一家。",
            follow_up="请选择具体门店。", missing_slots=["store_hint"],
            ui_card=self.choice_card("store", choices))

    @staticmethod
    def _menu_products(data) -> list[dict]:
        categories = data.get("categories") if isinstance(data, dict) else None
        details = data.get("meals") if isinstance(data, dict) else None
        if not isinstance(categories, list) or not isinstance(details, dict):
            return []
        products = []
        seen_codes: set[str] = set()
        for category in categories:
            meals = category.get("meals") if isinstance(category, dict) else None
            if isinstance(meals, list):
                for category_item in meals:
                    if not isinstance(category_item, dict):
                        continue
                    code = str(category_item.get("code") or "").strip()
                    detail = details.get(code)
                    if (not code or code in seen_codes or
                            not isinstance(detail, dict)):
                        continue
                    seen_codes.add(code)
                    products.append({
                        "code": code,
                        "tags": copy.deepcopy(category_item.get("tags") or []),
                        **copy.deepcopy(detail),
                    })
        return products

    @staticmethod
    def _product_name(product: dict) -> str:
        return str(product.get("name") or product.get("mealName") or
                   product.get("title") or "")

    @classmethod
    def _matching_products(cls, products: list[dict], query: str) -> list[dict]:
        needle = cls._normalized(query)
        exact = [item for item in products
                 if cls._normalized(cls._product_name(item)) == needle]
        if exact:
            return exact
        return [item for item in products
                if needle in cls._normalized(cls._product_name(item))]

    def _product_choices(self, products: list[dict]) -> AgentResult:
        choices = []
        for product in products[:3]:
            try:
                price = yuan_to_cents(product.get("currentPrice"))
            except ValueError:
                price = None
            subtitle = (f"{price // 100}.{price % 100:02d} 元"
                        if price is not None else "到店价格")
            name = self._product_name(product) or "麦当劳餐品"
            choices.append(MerchantChoice(
                id=str(product.get("code") or ""), name=name,
                subtitle=subtitle, send_text=f"选择麦当劳餐品：{name}"))
        return AgentResult(
            status=NEED_SLOT,
            speech="找到多款相近的麦当劳餐品，请选择一款。",
            follow_up="请选择具体餐品。", missing_slots=["item_query"],
            ui_card=self.choice_card("product", choices))

    @staticmethod
    def _meal_detail(data: dict, code: str) -> dict:
        detail = data if isinstance(data, dict) else None
        if (not isinstance(detail, dict) or
                str(detail.get("code") or "") != str(code)):
            raise _BusinessError("meal detail missing")
        return detail

    @staticmethod
    def _build_item(detail: dict, *, product_code: str,
                    quantity: int) -> tuple[dict, list[str]]:
        item = {"productCode": product_code, "quantity": quantity}
        selected_rounds = []
        specifications = []
        for round_value in detail.get("rounds") or []:
            if not isinstance(round_value, dict):
                continue
            round_id = round_value.get("id")
            if isinstance(round_id, bool) or round_id in (None, ""):
                raise _SelectionRequired("round id missing")
            minimum = McDonaldsWorkflow._nonnegative_int(
                round_value.get("minQuantity"), default=0)
            maximum = McDonaldsWorkflow._nonnegative_int(
                round_value.get("maxQuantity"), default=minimum)
            if minimum is None or maximum is None or maximum < minimum:
                raise _SelectionRequired("round quantity contract invalid")
            choices = [value for value in (round_value.get("choices") or [])
                       if isinstance(value, dict)]
            defaults = [value for value in choices
                        if value.get("isDefault") in (1, "1", True) or
                        (McDonaldsWorkflow._nonnegative_int(
                            value.get("quantity"), default=0) or 0) > 0]
            combo_items = []
            selected_total = 0
            for choice in defaults:
                code = str(choice.get("code") or "")
                selected_quantity = McDonaldsWorkflow._nonnegative_int(
                    choice.get("quantity"), default=1)
                choice_maximum = McDonaldsWorkflow._maximum_int(
                    choice.get("maxQuantity"), default=selected_quantity or 1)
                if (not code or selected_quantity is None or selected_quantity <= 0 or
                        choice_maximum is None or
                        (choice_maximum >= 0 and
                         selected_quantity > choice_maximum)):
                    raise _SelectionRequired("default combo choice invalid")
                combo = {"code": code, "quantity": selected_quantity}
                modification, names = McDonaldsWorkflow._build_modification(
                    choice.get("modification"))
                if modification is not None:
                    combo["modification"] = modification
                combo_items.append(combo)
                selected_total += selected_quantity
                name = str(choice.get("name") or "")
                if name:
                    specifications.append(name)
                specifications.extend(names)
            if selected_total < minimum or selected_total > maximum:
                raise _SelectionRequired("round has no valid official default")
            if combo_items:
                selected_rounds.append({
                    "round": str(round_id),
                    "comboItemList": combo_items,
                })
        if selected_rounds:
            item["roundList"] = selected_rounds

        modification, names = McDonaldsWorkflow._build_modification(
            detail.get("modification"))
        if modification is not None:
            item["modification"] = modification
        specifications.extend(names)
        # 同一官方默认项可能同时出现在套餐层与整餐层；预览去重但保持顺序。
        specifications = list(dict.fromkeys(specifications))
        return item, specifications

    @staticmethod
    def _build_modification(value) -> tuple[dict | None, list[str]]:
        if value in (None, {}):
            return None, []
        if not isinstance(value, dict):
            raise _SelectionRequired("modification is not an object")
        groups = value.get("items")
        if groups in (None, []):
            return None, []
        if not isinstance(groups, list):
            raise _SelectionRequired("modification.items is not a list")
        request_values = []
        selected_names = []
        for group in groups:
            if not isinstance(group, dict):
                raise _SelectionRequired("modification group invalid")
            minimum = McDonaldsWorkflow._nonnegative_int(
                group.get("minValues"), default=0)
            maximum = McDonaldsWorkflow._nonnegative_int(
                group.get("maxValues"), default=minimum)
            if minimum is None or maximum is None or maximum < minimum:
                raise _SelectionRequired("modification quantity contract invalid")
            selected_total = 0
            values = group.get("values")
            if not isinstance(values, list):
                raise _SelectionRequired("modification values missing")
            for option in values:
                if not isinstance(option, dict):
                    raise _SelectionRequired("modification value invalid")
                code = str(option.get("code") or "")
                selected_quantity = McDonaldsWorkflow._nonnegative_int(
                    option.get("selectedQuantity"), default=0)
                maximum_quantity = McDonaldsWorkflow._nonnegative_int(
                    option.get("maxQuantity"), default=selected_quantity or 0)
                if (not code or selected_quantity is None or maximum_quantity is None or
                        selected_quantity > maximum_quantity):
                    raise _SelectionRequired("modification selection invalid")
                if selected_quantity > 0:
                    key = str(option.get("selectedKey") or "")
                    if not key:
                        raise _SelectionRequired("selected modification key missing")
                    request_values.append({
                        "key": key, "code": code,
                        "quantity": selected_quantity,
                    })
                    selected_total += selected_quantity
                    name = str(option.get("name") or "")
                    if name:
                        selected_names.append(name)
                else:
                    # 官方描述要求用 unselectedKey 明确未选状态；没有该 key 时省略，
                    # 绝不拿 selectedKey 或本地造值代替。
                    key = str(option.get("unselectedKey") or "")
                    if key:
                        request_values.append({
                            "key": key, "code": code, "quantity": 0,
                        })
            if selected_total < minimum or selected_total > maximum:
                raise _SelectionRequired("modification has no valid official default")
        return ({"values": request_values} if request_values else None,
                selected_names)

    @staticmethod
    def _nonnegative_int(value, *, default: int | None = None) -> int | None:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return None
        try:
            number = int(str(value))
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @staticmethod
    def _maximum_int(value, *, default: int | None = None) -> int | None:
        """官方 choice.maxQuantity=-1 表示不设上限；其余负数拒绝。"""
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return None
        try:
            number = int(str(value))
        except (TypeError, ValueError):
            return None
        return number if number >= -1 else None

    @classmethod
    def _take_way(cls, values, requested: str) -> dict | None:
        choices = [value for value in (values or []) if isinstance(value, dict)
                   and value.get("code") not in (None, "")]
        if not choices:
            return None
        needle = cls._normalized(requested)
        aliases = {
            "到店自取": (
                "到店自取", "自取", "自提", "店内自提", "外带", "取餐"),
            "堂食": ("堂食", "店内"),
        }
        for choice in choices:
            title = cls._normalized(" ".join(str(value or "") for value in (
                choice.get("title"), choice.get("subtitle"),
                choice.get("name"))))
            if needle and (needle in title or title in needle):
                return choice
            for canonical, words in aliases.items():
                if needle in {cls._normalized(word) for word in words} and any(
                        cls._normalized(word) in title for word in words):
                    return choice
        return choices[0] if not requested else None

    @classmethod
    def _calculation(cls, data: dict, *, request_item: dict,
                     requested_takeway: str,
                     selected_takeway_code: str = "") -> dict:
        if not isinstance(data, dict):
            raise _BusinessError("calculation data missing")
        amount = cls._cents(data.get("price"))
        original = cls._cents(data.get("originalPrice"))
        if amount is None or original is None or original < amount:
            raise _BusinessError("calculation amount invalid")
        products = data.get("productList")
        if not isinstance(products, list) or len(products) != 1:
            raise _BusinessError("product unavailable")
        calculated_item = products[0]
        if not isinstance(calculated_item, dict):
            raise _BusinessError("calculated product invalid")
        expected_code = str(request_item.get("productCode") or "")
        expected_quantity = cls._positive_int(request_item.get("quantity"))
        actual_code = str(calculated_item.get("productCode") or "")
        actual_quantity = cls._positive_int(calculated_item.get("quantity"))
        if (not expected_code or expected_quantity is None or
                actual_code != expected_code or
                actual_quantity != expected_quantity):
            raise _BusinessError("calculated product identity mismatch")
        subtotal = cls._cents(calculated_item.get("subtotal"))
        original_subtotal = cls._cents(
            calculated_item.get("originalSubtotal"))
        if (subtotal is None or original_subtotal is None or
                original_subtotal < subtotal):
            raise _BusinessError("calculated product subtotal invalid")
        product_name = str(calculated_item.get("productName") or "").strip()
        if not product_name:
            raise _BusinessError("calculated product name missing")

        if selected_takeway_code:
            choices = [value for value in (data.get("takeWayList") or [])
                       if isinstance(value, dict)]
            take_way = next((value for value in choices
                             if str(value.get("code") or "") ==
                             selected_takeway_code), None)
            if take_way is None:
                raise _SelectionRequired(
                    "原先选择的取餐方式已不可用，请重新选择。")
        else:
            take_way = cls._take_way(
                data.get("takeWayList"), requested_takeway)
            if take_way is None:
                raise _SelectionRequired(
                    "这家门店不支持你选择的取餐方式。")

        return {
            "amount_cents": amount,
            "original_cents": original,
            "discount_cents": cls._discount_cents(data, original, amount),
            "product_name": product_name,
            "product_amount_cents": subtotal,
            "product_original_cents": original_subtotal,
            "take_way": copy.deepcopy(take_way),
        }

    @staticmethod
    def _positive_int(value) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(str(value))
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _refresh_draft(draft: MerchantDraft, calculated: dict) -> MerchantDraft:
        current = draft.items[0]
        refreshed_item = replace(
            current,
            name=str(calculated.get("product_name") or current.name),
            amount_cents=int(calculated["product_amount_cents"]),
        )
        args = copy.deepcopy(draft.upstream_args)
        take_way = calculated["take_way"]
        args["takeWayCode"] = str(take_way.get("code") or "")
        fulfillment = str(take_way.get("title") or
                          take_way.get("name") or "")
        reservation_date = str(
            (draft.store or {}).get("reservation_date") or "")
        if reservation_date:
            fulfillment += f"，预约 {reservation_date}"
        return replace(
            draft,
            token=secrets.token_urlsafe(24),
            items=[refreshed_item],
            amount_cents=int(calculated["amount_cents"]),
            original_amount_cents=int(calculated["original_cents"]),
            discount_cents=int(calculated["discount_cents"]),
            fulfillment=fulfillment,
            upstream_args=args,
            created_at=time.time(),
        )

    @staticmethod
    def _draft_changed(before: MerchantDraft, after: MerchantDraft) -> bool:
        return any((
            before.amount_cents != after.amount_cents,
            before.original_amount_cents != after.original_amount_cents,
            before.discount_cents != after.discount_cents,
            before.fulfillment != after.fulfillment,
            before.items != after.items,
            before.upstream_args != after.upstream_args,
        ))

    async def _payment_card(self, ctx, intent, draft: MerchantDraft, ref: dict,
                            *, payload: dict,
                            buttons: list[dict]) -> dict | None:
        if self.payment is None:
            return None
        binding = self.tools["create-order"]
        if str(getattr(binding.tool, "pay_url_locator", "") or "") != (
                "data.payH5Url"):
            return None
        pay_url = payload.get("payH5Url")
        if not isinstance(pay_url, str) or not pay_url:
            return None
        try:
            parsed = urlparse(pay_url)
            host = normalize_hostname(parsed.hostname or "")
            scheme_ok = (
                parsed.scheme.lower() == "https" and
                parsed.username is None and parsed.password is None and
                parsed.port in (None, 443) and pay_url == pay_url.strip() and
                not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127
                        for ch in pay_url)
            )
        except ValueError:
            host, scheme_ok = "", False
        allowed = {
            normalize_hostname(value)
            for value in (getattr(self.server, "pay_url_hosts", []) or [])
        }
        allowed.discard("")
        if not scheme_ok or not host or host not in allowed:
            logger.warning("[麦当劳] 支付链接不在白名单，拒绝登记：host=%s", host)
            return None
        try:
            response = await self.payment.authorize(
                agent_id="mcp-bridge",
                user_id=str(getattr(ctx, "user_id", "") or ""),
                vehicle_id=str(getattr(ctx, "vehicle_id", "") or ""),
                scene=str(getattr(intent, "name", "") or "mcd.order"),
                amount_cents=draft.amount_cents,
                description="麦当劳订单",
                idempotency_key=idem_key(
                    draft.user_id, "mcp_pay",
                    str(ref.get("order_id") or "")),
                channel=3,
                external_pay_url=pay_url,
                external_order_ref=str(ref.get("order_id") or ""),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # create 已有明确 orderId 后，支付登记失败（含调用被取消）也不能
            # 把已知成功降级成“订单可能创建”。
            logger.warning("麦当劳已成功订单的支付登记异常：%s", type(exc).__name__)
            return None
        try:
            payment_id = str(
                getattr(response, "payment_id", "") or "").strip()
            if not payment_id:
                return None
            card = {
                "type": "payment_qr",
                **ref,
                "payment_id": payment_id,
                "amount": (
                    f"{draft.amount_cents // 100}."
                    f"{draft.amount_cents % 100:02d}元"),
                "scene": str(
                    getattr(intent, "name", "") or "mcd.order"),
                "qr_content": pay_url,
                "pay_url": pay_url,
                "merchant_note": "订单状态以麦当劳为准",
                "buttons": copy.deepcopy(buttons),
            }
            qr_svg = str(getattr(response, "qr_svg", "") or "")
            if qr_svg:
                card["qr_svg"] = qr_svg
            return card
        except Exception as exc:
            logger.warning("麦当劳已成功订单的支付卡构造异常：%s", type(exc).__name__)
            return None

    @staticmethod
    def _confirmed(meta) -> bool:
        value = (meta or {}).get("confirmed")
        return value is True or str(value or "").strip().lower() == "true"

    @staticmethod
    def _cents(value) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            amount = int(str(value))
        except (TypeError, ValueError):
            return None
        return amount if amount >= 0 else None

    @classmethod
    def _discount_cents(cls, data: dict, original: int, payable: int) -> int:
        for key in ("discountAmount", "discount", "discountPrice"):
            value = cls._cents(data.get(key))
            if value is not None and value <= original:
                return value
        return max(0, original - payable)

    @staticmethod
    def _create_amount(payload: dict) -> int | None:
        detail = payload.get("orderDetail")
        if not isinstance(detail, dict):
            return None
        value = detail.get("realTotalAmount")
        if value in (None, ""):
            return None
        try:
            return yuan_to_cents(value)
        except ValueError:
            return None

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"[\s，,。.！!？?、；;：:()（）\-—_]", "", str(value or "")).lower()

    @staticmethod
    def _read_failure(action: str, exc: Exception) -> AgentResult:
        logger.warning("麦当劳%s失败：%s", action, type(exc).__name__)
        return AgentResult(speech=f"暂时无法{action}，没有创建订单，请稍后再试。")

"""瑞幸官方 MCP 的确定性选店、选品、规格、预览、下单与取消工作流。"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import re
import secrets
import time
from dataclasses import replace
from urllib.parse import urlparse

from agents._sdk import AgentResult, NEED_CONFIRM, NEED_SLOT
from agents._sdk.ledger import DONE, FAILED, Duplicate, idem_key

from ..admission import normalize_hostname
from .base import DeclaredBusinessRejected, MerchantWorkflow
from .models import (
    MerchantChoice,
    MerchantDraft,
    MerchantItem,
    MerchantResult,
    yuan_to_cents,
)
from ..mcp_client import McpTimeout


logger = logging.getLogger("agent.mcp_bridge.merchant.luckin")

_LEDGER_KIND = "mcp_order"
_ORDER_TOOLS = (
    "queryShopList",
    "searchProductForMcp",
    "switchProduct",
    "queryProductDetailInfo",
    "previewOrder",
    "createOrder",
    # createOrder's admitted compensation dependency is part of the order
    # workflow contract and therefore part of its schema snapshot.
    "cancelOrder",
)
_CANCEL_TOOLS = (
    "queryOrderDetailInfo",
    "cancelOrder",
)
# 只读看菜单：门店解析 + 商品检索，**一个写工具都不需要**。
_MENU_TOOLS = (
    "queryShopList",
    "searchProductForMcp",
)
# 用户没说想喝什么时给商户检索的种子词。空串会被商户判「非法参数」（真机取证），
# 所以必须给一个实词——挑最泛的品类词，不预设口味。
_MENU_SEED_QUERY = "咖啡"
# 逐 intent 的工具契约。用表不用 if 链：新增 workflow intent 时漏配这里，
# 桥在启动期就会以具名 ValueError 拒载——2026-08-13 luckin.menu 首次上真栈时
# 正是这样被抓到的（单测给的是全量工具，只有容器里才走到这条校验）。
_WORKFLOW_TOOL_CONTRACTS = {
    "luckin.order": _ORDER_TOOLS,
    "luckin.order_cancel": _CANCEL_TOOLS,
    "luckin.menu": _MENU_TOOLS,
}
_OPEN_STATUSES = {"营业中", "open", "opened", "1", "true"}
_CLOSED_STATUSES = {
    "已打烊", "打烊", "停业", "休息中", "closed", "close", "closing", "0",
    "false",
}
_SPEC_GROUPS = {
    "temperature": {"温度", "冷热", "热冷"},
    "ice": {"冰量", "冰度", "加冰"},
    "sweetness": {"糖度", "甜度"},
    "milk": {"奶底", "奶类", "乳基底", "奶制品"},
}
_ORDER_ID_PATHS = (
    "orderIdStr",
    "orderId",
    "order.id",
    "order.orderIdStr",
    "order.orderId",
    "orderInfo.orderIdStr",
    "orderInfo.orderId",
    "orderNo",
    "order.orderNo",
    "orderInfo.orderNo",
)
_ORDER_STATUS_PATHS = (
    "status",
    "orderStatusName",
    "orderStatus",
    "orderState",
    "state",
    "cancelStatus",
    "order.status",
    "order.orderStatusName",
    "order.orderStatus",
    "order.orderState",
    "order.state",
    "order.cancelStatus",
    "orderInfo.status",
    "orderInfo.orderStatusName",
    "orderInfo.orderStatus",
    "orderInfo.orderState",
    "orderInfo.state",
    "orderInfo.cancelStatus",
)
_CANCELLABLE_STATUSES = {
    "created", "unpaid", "pending", "pendingpayment", "waitingpayment",
    "waitpay", "createdunpaid", "待支付", "待付款", "未支付", "已创建",
}
_CANCELLED_STATUSES = {
    "cancelled", "canceled", "cancelsuccess", "cancelledsuccess",
    "已取消", "取消成功",
}


class _BusinessError(RuntimeError):
    """协议层成功，但官方业务 envelope 未确认成功。"""


class _SelectionRequired(_BusinessError):
    def __init__(self, slot: str, value: str):
        super().__init__(f"unsupported selection {slot}")
        self.slot = slot
        self.value = value


class _BusinessReject(_BusinessError):
    """The merchant explicitly rejected the write; no uncertain outcome."""


class _IncompleteSuccess(_BusinessError):
    """The write returned success but omitted/mismatched its terminal facts."""


class LuckinWorkflow(MerchantWorkflow):
    merchant = "luckin"
    intents = ("luckin.order", "luckin.order_cancel", "luckin.menu")

    def __init__(self, server, workflow_spec, tools_by_name, draft_store,
                 ledger, payment):
        self.server = server
        self.workflow_spec = workflow_spec
        self.drafts = draft_store
        self.ledger = ledger
        self.payment = payment
        available = dict(tools_by_name or {})
        declared = tuple(str(name) for name in (
            getattr(workflow_spec, "required_tools", None) or ()))
        if not declared:
            raise ValueError("luckin workflow required_tools must not be empty")
        intent_name = str(getattr(workflow_spec, "intent", "") or "")
        expected = _WORKFLOW_TOOL_CONTRACTS.get(intent_name)
        if expected is None:
            raise ValueError(
                f"luckin workflow has no tool contract for intent {intent_name!r}")
        self.required_tools = declared
        missing = [name for name in self.required_tools if name not in available]
        if missing:
            raise ValueError(f"luckin workflow missing tools: {','.join(missing)}")
        absent_contract = [name for name in expected if name not in self.required_tools]
        if absent_contract:
            raise ValueError(
                "luckin workflow declaration incomplete: " +
                ",".join(absent_contract))
        # Keep the codec's authority exactly equal to the workflow declaration,
        # even if a future bootstrap caller accidentally passes server-wide
        # bindings instead of the per-workflow subset.
        self.tools = {name: available[name] for name in self.required_tools}

    async def prepare(self, intent, ctx, meta) -> AgentResult:
        slots = dict(getattr(intent, "slots", {}) or {})
        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return self.refused("当前会话身份不完整，暂时不能创建真实订单。")
        store = None
        trusted = self._trusted_store(slots, meta)
        if trusted is None:
            resumed = await self._resume_store_choice(
                slots, user_id=user_id, session_id=session_id)
            if isinstance(resumed, AgentResult):
                return resumed
            if resumed is not None:
                slots, store = resumed

        item_query = str(slots.get("item_query") or "").strip()
        if not item_query:
            return AgentResult(
                status=NEED_SLOT, speech="想点哪一款瑞幸饮品？",
                follow_up="请说具体饮品，例如“生椰拿铁”。",
                missing_slots=["item_query"])
        quantity = self._quantity(slots.get("quantity", "1"))
        if quantity is None:
            return AgentResult(
                status=NEED_SLOT, speech="数量需要是 1 到 20 之间的整数。",
                follow_up="请告诉我要几杯。", missing_slots=["quantity"])

        store, dept_id, refusal = await self._resolve_store(
            store, trusted, slots=slots,
            user_id=user_id, session_id=session_id)
        if refusal is not None:
            return refusal

        try:
            products_data = await self._read(
                "searchProductForMcp",
                self._arguments("searchProductForMcp", {
                    "deptId": dept_id, "query": item_query,
                }))
        except Exception as exc:
            return self._read_failure("查询该门店菜单", exc)
        products = [product for product in self._products(products_data)
                    if self._is_available_product(product)]
        matches = self._matching_products(products, item_query)
        if not matches:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"这家门店没有找到可售的“{item_query}”。",
                follow_up="请换一款饮品。", missing_slots=["item_query"])
        if len(matches) != 1:
            return self._product_choices(matches)
        product = matches[0]
        product_id = self._positive_int(product.get("productId"))
        if product_id is None:
            return AgentResult(
                status=NEED_SLOT, speech="官方商品信息不完整，请换一款饮品。",
                follow_up="请换一款饮品。", missing_slots=["item_query"])

        try:
            detail = await self._read(
                "queryProductDetailInfo",
                self._arguments("queryProductDetailInfo", {
                    "deptId": dept_id, "productId": product_id,
                }))
            if not isinstance(detail, dict):
                raise _BusinessError("product detail is not object")
            detail_id = self._positive_int(detail.get("productId"))
            sku = str(detail.get("skuCode") or "").strip()
            if detail_id != product_id or not sku:
                raise _BusinessError("product detail identity mismatch")
            detail = await self._apply_specs(
                detail, dept_id=dept_id, product_id=product_id,
                quantity=quantity, slots=slots)
            final_sku = str(detail.get("skuCode") or "").strip()
            if not final_sku:
                raise _BusinessError("final sku missing")
            product_list = [{
                "amount": quantity,
                "productId": product_id,
                "skuCode": final_sku,
            }]
            preview = await self._read(
                "previewOrder",
                self._arguments("previewOrder", {
                    "deptId": dept_id, "productList": product_list,
                }))
            draft = self._draft_from_preview(
                preview, user_id=user_id, session_id=session_id,
                dept_id=dept_id, product_id=product_id, sku=final_sku,
                quantity=quantity, expected_store=store,
                fallback_name=str(product.get("productName") or item_query))
        except _SelectionRequired as exc:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"这款饮品不支持“{exc.value}”，我没有替你猜其他规格。",
                follow_up="请选择商家当前可用的规格。",
                missing_slots=[exc.slot])
        except Exception as exc:
            return self._read_failure("核对饮品规格和预览订单", exc)

        if not await self.drafts.put(draft):
            return self.refused("订单预览暂时无法安全保存，请稍后重新下单。")
        speech = self.preview_speech(draft)
        return AgentResult(
            status=NEED_CONFIRM, speech=speech,
            ui_card=self.preview_card(draft),
            data={"checkout_token": draft.token, "summary": speech})

    async def confirm(self, intent, ctx, meta, token: str = "") -> AgentResult:
        if str(getattr(intent, "name", "") or "") == "luckin.order_cancel":
            return await self._confirm_cancel(intent, ctx, token=token)
        return await self._confirm_create(intent, ctx, token=token)

    async def cancel(self, intent, ctx, meta) -> AgentResult:
        # McpBridgeAgent deliberately routes every ``*_cancel`` turn here.
        # Dispatch the resumed global confirmation before doing another status
        # lookup, otherwise the user is trapped in an endless confirm loop.
        confirmed = str((meta or {}).get("confirmed", "")).lower() == "true"
        if confirmed:
            token = str((getattr(intent, "slots", {}) or {}).get(
                "checkout_token") or "")
            return await self._confirm_cancel(intent, ctx, token=token)
        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return AgentResult(speech="当前会话身份不完整，不能取消真实订单。")
        slots = dict(getattr(intent, "slots", {}) or {})
        requested_order_id = str(slots.get("order_id") or "").strip()
        previous_ref = await self._owned_order(
            user_id, session_id=session_id, order_id=requested_order_id)
        order_id = str(previous_ref.get("order_id") or "").strip()
        if not order_id:
            return AgentResult(
                status=NEED_SLOT,
                speech="没有找到可确定归属于你的瑞幸订单。",
                follow_up="请提供瑞幸订单号。", missing_slots=["order_id"])

        try:
            order = await self._read(
                "queryOrderDetailInfo",
                self._arguments("queryOrderDetailInfo", {"orderId": order_id}))
            if (not isinstance(order, dict) or
                    self._extract_order_id(order) != order_id or
                    self._order_status(order) not in _CANCELLABLE_STATUSES):
                raise _BusinessError(
                    "query order identity/status is not cancellable")
        except Exception as exc:
            logger.warning("瑞幸取消前查单失败：%s", type(exc).__name__)
            return AgentResult(
                speech="暂时无法从瑞幸核对这笔订单，没有发起取消。")

        draft = MerchantDraft(
            token=secrets.token_urlsafe(24), merchant=self.merchant,
            operation="cancel",
            user_id=user_id, session_id=session_id,
            store={
                "operation": "cancel", "order_id": order_id,
                "name": str(previous_ref.get("store_name") or ""),
            },
            items=[],
            amount_cents=self._nonnegative_int(
                previous_ref.get("amount_cents")) or 0,
            upstream_args=self._arguments(
                "cancelOrder", {"orderId": order_id}),
            schema_digest=self._schema_digest(), created_at=time.time())
        if not self._required_present("cancelOrder", draft.upstream_args):
            return AgentResult(speech="取消参数不完整，没有向瑞幸发起取消。")
        if not await self.drafts.put(draft):
            return AgentResult(speech="取消确认暂时无法安全保存，请稍后再试。")
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"请确认：取消瑞幸订单 {order_id}？确认后会立即提交。",
            ui_card={
                "type": "mcp_order", "server": str(self.server.id),
                "merchant": self.merchant, "order_id": order_id,
                "status": "cancel_pending",
                "confirmation_context": "merchant_cancel", "buttons": [],
            },
            data={"checkout_token": draft.token, "order_id": order_id})

    async def _confirm_create(
            self, intent, ctx, *, token: str,
            _leased_draft: MerchantDraft | None = None) -> AgentResult:
        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return AgentResult(speech="当前会话身份不完整，不能确认真实订单。")
        if _leased_draft is not None:
            draft = _leased_draft
        else:
            draft = await self._consume_draft(
                token, user_id=user_id, session_id=session_id,
                expected_action="create")
        if draft is None or draft.operation != "create":
            return AgentResult(speech="订单预览已失效，请重新选择饮品并确认。")
        if _leased_draft is None:
            async with self.drafts.operation_hold(
                    draft.token, user_id=draft.user_id) as held:
                if not held:
                    return self._lease_expired_result(operation="create")
                return await self._confirm_create(
                    intent, ctx, token=token, _leased_draft=draft)
        if draft.schema_digest != self._schema_digest():
            return AgentResult(speech="商家接口已更新，这份预览已失效，请重新预览后再确认。")
        if not self._required_present("createOrder", draft.upstream_args):
            return AgentResult(speech="订单参数已失效，没有向瑞幸提交，请重新预览。")
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_expired_result(operation="create")

        # Confirmation is not permission to use a stale quote.  Re-preview
        # immediately before opening the write ledger; any price, discount,
        # coupon, item, SKU, store or fulfillment change requires a fresh
        # visible confirmation and guarantees createOrder=0 on this turn.
        try:
            if len(draft.items) != 1:
                raise _BusinessError("draft item count mismatch")
            product_id = self._positive_int(draft.items[0].product_id)
            quantity = self._positive_int(draft.items[0].quantity)
            dept_id = self._positive_int((draft.store or {}).get("id"))
            sku = str(draft.items[0].sku or "").strip()
            if product_id is None or quantity is None or dept_id is None or not sku:
                raise _BusinessError("draft product identity invalid")
            preview_args = self._arguments("previewOrder", {
                "deptId": dept_id,
                "productList": copy.deepcopy(
                    draft.upstream_args.get("productList")),
            })
            preview = await self._read("previewOrder", preview_args)
            fresh = self._draft_from_preview(
                preview, user_id=user_id, session_id=session_id,
                dept_id=dept_id, product_id=product_id, sku=sku,
                quantity=quantity, expected_store=draft.store,
                fallback_name=draft.items[0].name)
        except Exception as exc:
            logger.warning("瑞幸确认前重新核价失败：%s", type(exc).__name__)
            return AgentResult(
                speech="暂时无法重新核对瑞幸价格，没有提交订单，"
                       "请重新预览后再试。")
        if self._draft_signature(fresh) != self._draft_signature(draft):
            if not await self.drafts.put(fresh, lease_token=draft.token):
                return AgentResult(
                    speech="价格或优惠已变化，但新预览无法安全保存，"
                           "没有提交订单。")
            speech = "价格或优惠已变化，" + self.preview_speech(fresh)
            return AgentResult(
                status=NEED_CONFIRM, speech=speech,
                ui_card=self.preview_card(fresh),
                data={"checkout_token": fresh.token, "summary": speech})
        # ``_draft_from_preview`` generates a token for a possible fresh
        # confirmation.  When nothing changed, keep the consumed token because
        # it is also the active operation lease identity.
        draft = replace(fresh, token=draft.token)

        task = await self._open_ledger(draft, ctx, operation="create")
        if isinstance(task, Duplicate):
            return AgentResult(speech="这份订单已经在受理中，请不要重复确认。")
        if task is None:
            return AgentResult(
                speech="暂时无法安全受理真实订单，没有向瑞幸提交，"
                       "请重新预览后再试。")

        try:
            if not await self.drafts.authorize(
                    draft.token, user_id=draft.user_id):
                return self._lease_expired_result(operation="create")
            response = await self.call_tool(
                "createOrder", copy.deepcopy(draft.upstream_args), write=True)
            envelope, payload = self._business_envelope(response)
            if not isinstance(payload, dict):
                raise _IncompleteSuccess("create data is not object")
            order_id = self._extract_order_id(payload)
            if not order_id:
                raise _IncompleteSuccess("create order id locator unresolved")
        except asyncio.CancelledError:
            await self._record_uncertain(
                task, operation="create", draft=draft)
            raise
        except McpTimeout as exc:
            if exc.sent:
                return await self._uncertain_write_result(
                    task, operation="create", order_id="", draft=draft)
            return await self._failed_write_result(
                task, operation="create", order_id="", draft=draft)
        except (_BusinessReject, DeclaredBusinessRejected):
            return await self._failed_write_result(
                task, operation="create", order_id="", draft=draft)
        except _IncompleteSuccess:
            return await self._uncertain_write_result(
                task, operation="create", order_id="", draft=draft)
        except Exception as exc:
            logger.warning("瑞幸下单结局不确定：%s", type(exc).__name__)
            return await self._uncertain_write_result(
                task, operation="create", order_id="", draft=draft)

        result = MerchantResult(
            server=str(self.server.id), merchant=self.merchant,
            order_id=order_id, status="created",
            amount_cents=draft.amount_cents,
            store_name=str((draft.store or {}).get("name") or ""),
            items=[{
                "name": item.name, "quantity": item.quantity,
                "specifications": list(item.specifications),
            } for item in draft.items])
        ref = result.ledger_ref()
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_lost_after_write_result(operation="create")
        synced = await self._close_verified_ledger(
            task, DONE, ref, progress="瑞幸未支付订单已创建")
        buttons = self._order_buttons(order_id, include_cancel=True)
        card = {
            "type": "mcp_order", **ref, "items": result.items,
            "buttons": buttons,
        }
        speech = f"瑞幸未支付订单已创建，订单号 {order_id}。"
        pay_card = None
        if await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            pay_card = await self._payment_card(
                ctx, intent, draft, ref, envelope=envelope, buttons=buttons)
        if pay_card is not None:
            card = pay_card
            if pay_card.get("qr_svg"):
                speech += "可以扫码支付；本次没有替你完成最终付款。"
            else:
                speech += "可以打开安全支付链接；本次没有替你完成最终付款。"
        else:
            speech += "尚未付款，请到瑞幸官方应用核对后支付。"
        if not synced:
            speech += "本地记录同步异常，请勿重复下单。"
        return AgentResult(speech=speech, ui_card=card, data=ref)

    async def _confirm_cancel(
            self, intent, ctx, *, token: str,
            _leased_draft: MerchantDraft | None = None) -> AgentResult:
        user_id, session_id = self._owner(ctx)
        if not user_id or not session_id:
            return AgentResult(speech="当前会话身份不完整，不能确认取消。")
        if _leased_draft is not None:
            draft = _leased_draft
        else:
            draft = await self._consume_draft(
                token, user_id=user_id, session_id=session_id,
                expected_action="cancel")
        if draft is None or draft.operation != "cancel":
            return AgentResult(speech="取消确认已失效，请重新发起取消。")
        if _leased_draft is None:
            async with self.drafts.operation_hold(
                    draft.token, user_id=draft.user_id) as held:
                if not held:
                    return self._lease_expired_result(operation="cancel")
                return await self._confirm_cancel(
                    intent, ctx, token=token, _leased_draft=draft)
        if draft.schema_digest != self._schema_digest():
            return AgentResult(speech="商家接口已更新，这次取消确认已失效，请重新操作。")
        order_id = str((draft.store or {}).get("order_id") or "").strip()
        if not order_id or not self._required_present(
                "cancelOrder", draft.upstream_args):
            return AgentResult(speech="取消参数已失效，没有向瑞幸提交。")
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_expired_result(operation="cancel")

        task = await self._open_ledger(draft, ctx, operation="cancel")
        if isinstance(task, Duplicate):
            return AgentResult(speech="这笔取消已经在受理中，请不要重复确认。")
        if task is None:
            return AgentResult(
                speech="暂时无法安全受理取消，没有向瑞幸提交，请重新操作。")
        try:
            if not await self.drafts.authorize(
                    draft.token, user_id=draft.user_id):
                return self._lease_expired_result(operation="cancel")
            response = await self.call_tool(
                "cancelOrder", copy.deepcopy(draft.upstream_args), write=True)
            _, payload = self._business_envelope(
                response, allow_scalar=True)
        except asyncio.CancelledError:
            await self._record_uncertain(
                task, operation="cancel", draft=draft)
            raise
        except McpTimeout as exc:
            if exc.sent:
                return await self._uncertain_write_result(
                    task, operation="cancel", order_id=order_id, draft=draft)
            return await self._failed_write_result(
                task, operation="cancel", order_id=order_id, draft=draft)
        except (_BusinessReject, DeclaredBusinessRejected):
            return await self._failed_write_result(
                task, operation="cancel", order_id=order_id, draft=draft)
        except _IncompleteSuccess:
            return await self._uncertain_write_result(
                task, operation="cancel", order_id=order_id, draft=draft)
        except Exception as exc:
            logger.warning("瑞幸取消结局不确定：%s", type(exc).__name__)
            return await self._uncertain_write_result(
                task, operation="cancel", order_id=order_id, draft=draft)

        if payload is True:
            # The live Luckin contract acknowledges cancellation with the
            # scalar ``data: true``.  Once that write acknowledgement exists,
            # every failure in the read-only terminal verification is an
            # uncertain outcome; it can no longer prove that cancellation was
            # not accepted.
            try:
                if not await self.drafts.authorize(
                        draft.token, user_id=draft.user_id):
                    return self._lease_lost_after_write_result(
                        operation="cancel")
                payload = await self._read(
                    "queryOrderDetailInfo",
                    self._arguments(
                        "queryOrderDetailInfo", {"orderId": order_id}))
            except asyncio.CancelledError:
                await self._record_uncertain(
                    task, operation="cancel", draft=draft)
                raise
            except Exception as exc:
                logger.warning(
                    "瑞幸取消已受理但终态核验失败：%s", type(exc).__name__)
                return await self._uncertain_write_result(
                    task, operation="cancel", order_id=order_id, draft=draft)
        if (not isinstance(payload, dict) or
                self._extract_order_id(payload) != order_id or
                self._order_status(payload) not in _CANCELLED_STATUSES):
            return await self._uncertain_write_result(
                task, operation="cancel", order_id=order_id, draft=draft)

        result = MerchantResult(
            server=str(self.server.id), merchant=self.merchant,
            order_id=order_id, status="cancelled",
            amount_cents=draft.amount_cents,
            store_name=str((draft.store or {}).get("name") or ""),
            cancelled=True)
        ref = result.ledger_ref()
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return self._lease_lost_after_write_result(operation="cancel")
        synced = await self._close_verified_ledger(
            task, DONE, ref, progress="瑞幸订单已取消")
        speech = f"瑞幸订单 {order_id} 已取消。"
        if not synced:
            speech += "本地记录同步异常，请以瑞幸官方订单状态为准。"
        return AgentResult(
            speech=speech,
            ui_card={
                "type": "mcp_order", **ref,
                "buttons": self._order_buttons(order_id, include_cancel=False),
            },
            data=ref)

    async def _apply_specs(self, detail: dict, *, dept_id: int,
                           product_id: int, quantity: int,
                           slots: dict) -> dict:
        current = copy.deepcopy(detail)
        for slot_name in ("temperature", "ice", "sweetness", "milk"):
            desired = str(slots.get(slot_name) or "").strip()
            if not desired:
                continue
            selection = self._official_selection(current, slot_name, desired)
            if selection is None:
                raise _SelectionRequired(slot_name, desired)
            parent_id, sub_id, selected = selection
            if selected:
                continue
            sku = str(current.get("skuCode") or "").strip()
            if not sku:
                raise _BusinessError("switch input sku missing")
            switched = await self._read(
                "switchProduct",
                self._arguments("switchProduct", {
                    "deptId": dept_id,
                    "productId": product_id,
                    "skuCode": sku,
                    "attrOperationParam": {
                        "attributeId": parent_id,
                        "subAttr": {"attributeId": sub_id, "operation": 1},
                    },
                    "amount": quantity,
                }))
            if not isinstance(switched, dict):
                raise _BusinessError("switch result is not object")
            if self._positive_int(switched.get("productId")) != product_id:
                raise _BusinessError("switch product identity mismatch")
            if not str(switched.get("skuCode") or "").strip():
                raise _BusinessError("switch result sku missing")
            current = switched
        return current

    def _draft_from_preview(self, preview, *, user_id: str, session_id: str,
                            dept_id: int, product_id: int, sku: str,
                            quantity: int, expected_store: dict,
                            fallback_name: str) -> MerchantDraft:
        if not isinstance(preview, dict):
            raise _BusinessError("preview data is not object")
        shop = preview.get("shopInfo")
        if not isinstance(shop, dict) or self._positive_int(
                shop.get("deptId")) != dept_id or not self._is_open(shop):
            raise _BusinessError("preview shop mismatch or closed")
        products = preview.get("productInfoList")
        if not isinstance(products, list) or len(products) != 1:
            raise _BusinessError("preview product list mismatch")
        item = products[0]
        if (not isinstance(item, dict) or
                self._positive_int(item.get("productId")) != product_id or
                str(item.get("skuCode") or "").strip() != sku or
                self._positive_int(item.get("amount")) != quantity):
            raise _BusinessError("preview product identity mismatch")

        amount_cents = yuan_to_cents(preview.get("discountPrice"))
        original_cents = yuan_to_cents(preview.get("totalInitialPrice"))
        discount_cents = yuan_to_cents(preview.get("privilegeMoney"))
        if original_cents < amount_cents or discount_cents != (
                original_cents - amount_cents):
            raise _BusinessError("preview amount relationship mismatch")
        item_amount = item.get("estimateTotalPrice")
        if item_amount is None:
            each_cents = yuan_to_cents(item.get("estimatePrice"))
            item_amount_cents = each_cents * quantity
        else:
            item_amount_cents = yuan_to_cents(item_amount)
        if item_amount_cents != amount_cents:
            raise _BusinessError("single item total mismatch")

        official_longitude = self._coordinate(
            shop.get("longitude"), longitude=True)
        official_latitude = self._coordinate(
            shop.get("latitude"), longitude=False)
        expected_longitude = self._coordinate(
            expected_store.get("longitude"), longitude=True)
        expected_latitude = self._coordinate(
            expected_store.get("latitude"), longitude=False)
        if (official_longitude is None or official_latitude is None or
                expected_longitude is None or expected_latitude is None or
                abs(official_longitude - expected_longitude) > 0.001 or
                abs(official_latitude - expected_latitude) > 0.001):
            raise _BusinessError("preview shop coordinate mismatch")

        raw_coupons = preview.get("couponCodeList") or []
        if (not isinstance(raw_coupons, list) or len(raw_coupons) > 50 or
                any(not isinstance(code, str) for code in raw_coupons)):
            raise _BusinessError("preview coupons malformed")
        coupons = [code for code in raw_coupons if code]
        specs = self._split_specifications(item.get("additionDesc"))
        product_name = str(
            item.get("name") or item.get("productName") or fallback_name).strip()
        if not product_name:
            raise _BusinessError("preview product name missing")
        product_list = [{
            "amount": quantity, "productId": product_id, "skuCode": sku,
        }]
        create_args = self._arguments("createOrder", {
            "deptId": dept_id,
            "productList": product_list,
            "longitude": official_longitude,
            "latitude": official_latitude,
            "couponCodeList": coupons,
        })
        if not self._required_present("createOrder", create_args):
            raise _BusinessError("create required arguments missing")
        return MerchantDraft(
            token=secrets.token_urlsafe(24), merchant=self.merchant,
            operation="create",
            user_id=user_id, session_id=session_id,
            store={
                "operation": "create", "id": dept_id,
                "name": str(shop.get("deptName") or "").strip(),
                "longitude": official_longitude, "latitude": official_latitude,
            },
            items=[MerchantItem(
                name=product_name, quantity=quantity, sku=sku,
                product_id=str(product_id), specifications=specs,
                amount_cents=item_amount_cents)],
            amount_cents=amount_cents,
            original_amount_cents=original_cents,
            discount_cents=discount_cents,
            fulfillment="到店自取", upstream_args=create_args,
            schema_digest=self._schema_digest(), created_at=time.time())

    @staticmethod
    def _draft_signature(draft: MerchantDraft) -> str:
        payload = {
            "merchant": draft.merchant,
            "store": draft.store,
            "items": [{
                "name": item.name, "quantity": item.quantity,
                "sku": item.sku, "product_id": item.product_id,
                "specifications": item.specifications,
                "amount_cents": item.amount_cents,
            } for item in draft.items],
            "amount_cents": draft.amount_cents,
            "original_amount_cents": draft.original_amount_cents,
            "discount_cents": draft.discount_cents,
            "fulfillment": draft.fulfillment,
            "currency": draft.currency,
            "upstream_args": draft.upstream_args,
            "schema_digest": draft.schema_digest,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    async def _payment_card(self, ctx, intent, draft: MerchantDraft,
                            ref: dict, *, envelope: dict,
                            buttons: list[dict]) -> dict | None:
        binding = self.tools["createOrder"]
        locator = str(getattr(binding.tool, "pay_url_locator", "") or "").strip()
        amount_locator = str(
            getattr(binding.tool, "amount_locator", "") or "").strip()
        # A hosted-payment URL is not enough: the gateway amount must be bound
        # to a merchant-confirmed create response.  Luckin's amount locator is
        # intentionally absent until live discovery, so registration remains
        # fail-closed even if somebody guesses a pay URL path.
        if not locator or not amount_locator or self.payment is None:
            return None
        pay_url = self._dig_any(envelope, locator)
        if not isinstance(pay_url, str) or not pay_url:
            return None
        merchant_amount = self._dig_any(envelope, amount_locator)
        amount_unit = str(
            getattr(binding.tool, "amount_unit", "yuan") or "yuan")
        if amount_unit not in {"yuan", "cents"}:
            logger.warning("瑞幸创单金额单位未经支持，拒绝登记支付")
            return None
        try:
            if amount_unit == "cents":
                merchant_cents = self._nonnegative_int(merchant_amount)
            else:
                merchant_cents = yuan_to_cents(merchant_amount)
        except ValueError:
            merchant_cents = None
        if merchant_cents != draft.amount_cents:
            logger.warning("瑞幸创单金额无法与预览绑定，拒绝登记支付")
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
            logger.warning("[瑞幸] 支付链接不在白名单，拒绝登记：host=%s", host)
            return None
        try:
            response = await self.payment.authorize(
                agent_id="mcp-bridge",
                user_id=str(getattr(ctx, "user_id", "") or ""),
                vehicle_id=str(getattr(ctx, "vehicle_id", "") or ""),
                scene=str(getattr(intent, "name", "") or "luckin.order"),
                amount_cents=draft.amount_cents,
                description="瑞幸咖啡订单",
                idempotency_key=idem_key(
                    draft.user_id, "mcp_pay", str(ref.get("order_id") or "")),
                channel=3,
                external_pay_url=pay_url,
                external_order_ref=str(ref.get("order_id") or ""))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("瑞幸已成功订单的支付登记异常：%s", type(exc).__name__)
            return None
        if response is None or not str(
                getattr(response, "payment_id", "") or "").strip():
            return None
        card = {
            "type": "payment_qr", **ref,
            "payment_id": str(getattr(response, "payment_id", "") or ""),
            "amount": f"{draft.amount_cents // 100}.{draft.amount_cents % 100:02d}元",
            "scene": str(getattr(intent, "name", "") or "luckin.order"),
            "qr_content": pay_url,
            "pay_url": pay_url,
            "merchant_note": "订单状态以瑞幸为准",
            "buttons": copy.deepcopy(buttons),
        }
        qr_svg = str(getattr(response, "qr_svg", "") or "")
        if qr_svg:
            card["qr_svg"] = qr_svg
        return card

    async def _consume_draft(self, token: str, *, user_id: str,
                             session_id: str,
                             expected_action: str) -> MerchantDraft | None:
        if token:
            return await self.drafts.consume(
                token, user_id=user_id, session_id=session_id,
                merchant=self.merchant, expected_action=expected_action)
        return await self.drafts.consume_current(
            user_id=user_id, session_id=session_id, merchant=self.merchant,
            expected_action=expected_action)

    async def _open_ledger(self, draft: MerchantDraft, ctx, *, operation: str):
        if self.ledger is None:
            return None
        if not await self.drafts.authorize(
                draft.token, user_id=draft.user_id):
            return None
        if operation == "cancel":
            # Contract §9.9 has one merchant order lifecycle namespace.
            # Distinguish cancel through goal/idempotency metadata, not a new
            # unregistered Task Ledger kind.
            kind = _LEDGER_KIND
            goal = str((draft.store or {}).get("order_id") or "")
        else:
            kind = _LEDGER_KIND
            items = ",".join(
                f"{item.product_id}x{item.quantity}" for item in draft.items)
            goal = f"{draft.store.get('id', '')}|{items}|{draft.amount_cents}"
        try:
            return await self.ledger.open(
                draft.user_id, draft.session_id, str(self.server.id), kind, goal,
                origin_trace_id=str(getattr(ctx, "trace_id", "") or ""),
                idempotency_goal=f"luckin:{operation}:{draft.token}")
        except Exception as exc:
            logger.warning("瑞幸 Ledger.open 失败：%s", type(exc).__name__)
            return None

    async def _record_uncertain(
            self, task, *, operation: str,
            draft: MerchantDraft | None = None) -> None:
        if (draft is not None and not await self.drafts.authorize(
                draft.token, user_id=draft.user_id)):
            return
        ref = {
            "server": str(self.server.id), "merchant": self.merchant,
            "error": "mcp_uncertain", "outcome": "uncertain",
        }
        progress = ("瑞幸取消结果不确定" if operation == "cancel"
                    else "瑞幸下单结果不确定")
        await self._close_ledger(task, FAILED, ref, progress=progress)

    async def _record_failed(
            self, task, *, operation: str,
            draft: MerchantDraft | None = None) -> None:
        if (draft is not None and not await self.drafts.authorize(
                draft.token, user_id=draft.user_id)):
            return
        ref = {
            "server": str(self.server.id), "merchant": self.merchant,
            "error": "mcp_rejected", "outcome": "failed",
        }
        progress = ("瑞幸取消未受理" if operation == "cancel"
                    else "瑞幸下单未受理")
        await self._close_ledger(task, FAILED, ref, progress=progress)

    async def _uncertain_write_result(self, task, *, operation: str,
                                      order_id: str,
                                      draft: MerchantDraft | None = None) -> AgentResult:
        await self._record_uncertain(
            task, operation=operation, draft=draft)
        if operation == "cancel":
            speech = (
                f"瑞幸订单 {order_id} 的取消没有拿到可靠结果，"
                "可能已经受理。请不要重复取消，先到官方应用核对。")
        else:
            speech = (
                "瑞幸下单没有拿到可靠结果，可能已经受理。"
                "请不要重复下单，先到瑞幸官方应用核对。")
        return AgentResult(
            speech=speech,
            data={"merchant": self.merchant, "status": "uncertain"})

    async def _failed_write_result(self, task, *, operation: str,
                                   order_id: str,
                                   draft: MerchantDraft | None = None) -> AgentResult:
        await self._record_failed(task, operation=operation, draft=draft)
        if operation == "cancel":
            speech = (f"瑞幸没有取消订单 {order_id}："
                      "商家明确未受理，请稍后再试。")
        else:
            speech = "瑞幸没有受理这次下单，请重新预览后再试。"
        return AgentResult(
            speech=speech,
            data={"merchant": self.merchant, "status": "failed"})

    def _lease_expired_result(self, *, operation: str) -> AgentResult:
        action = "取消" if operation == "cancel" else "下单"
        return AgentResult(
            speech=f"瑞幸{action}确认已失效，没有继续记账或向商家提交。"
                   "请重新操作。")

    def _lease_lost_after_write_result(self, *, operation: str) -> AgentResult:
        action = "取消" if operation == "cancel" else "下单"
        return AgentResult(
            speech=f"瑞幸{action}已提交但本地授权过期，结果不确定。"
                   "请不要重复操作，先到瑞幸官方应用核对。",
            data={"merchant": self.merchant, "status": "uncertain"})

    async def _close_ledger(self, task, status: str, result_ref: dict, *,
                            progress: str) -> bool:
        try:
            return bool(await self.ledger.close(
                task.task_id, status, result_ref=result_ref, progress=progress))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("瑞幸 Ledger.close 失败：%s", type(exc).__name__)
            return False

    async def _close_verified_ledger(self, task, status: str,
                                     result_ref: dict, *,
                                     progress: str) -> bool:
        """Persist a known merchant terminal result before propagating cancel."""
        close_task = asyncio.create_task(self.ledger.close(
            task.task_id, status, result_ref=result_ref, progress=progress))
        try:
            return bool(await asyncio.shield(close_task))
        except asyncio.CancelledError:
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await close_task
                raise
            except Exception as exc:
                logger.warning("瑞幸已确认终态落账失败：%s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.warning("瑞幸已确认终态落账失败：%s", type(exc).__name__)
            return False

    async def _owned_order(self, user_id: str, *, session_id: str = "",
                           order_id: str = "") -> dict:
        if self.ledger is None:
            return {}
        try:
            tasks = await self.ledger.recent(
                user_id, kind=_LEDGER_KIND, limit=20)
        except Exception as exc:
            logger.warning("瑞幸 Ledger.recent 失败：%s", type(exc).__name__)
            return {}
        requested = self._explicit_order_id(order_id)
        seen: set[str] = set()
        fallback: dict = {}
        for task in tasks or []:
            if str(getattr(task, "user_id", "") or "") != user_id:
                continue
            if str(getattr(task, "kind", "") or "") != _LEDGER_KIND:
                continue
            ref = getattr(task, "result_ref", {}) or {}
            if not isinstance(ref, dict) or str(
                    ref.get("merchant") or "") != self.merchant:
                continue
            candidate_id = str(ref.get("order_id") or "").strip()
            candidate_from_ref = bool(candidate_id)
            if not candidate_id:
                # Cancel attempts store the exact order id in ``goal`` even
                # when their result is uncertain and result_ref deliberately
                # omits it.  Recover it only as a tombstone key; create goals
                # contain pipes and can never pass this conservative shape.
                goal = str(getattr(task, "goal", "") or "").strip()
                if (len(goal) <= 128 and re.fullmatch(
                        r"[A-Za-z0-9_-]+", goal) and
                        re.search(r"[0-9]", goal)):
                    candidate_id = goal
            if not candidate_id:
                if (not requested and (
                        str(getattr(task, "status", "") or "") != DONE or
                        self._normalize_status(ref.get("outcome")) in {
                            "failed", "uncertain"})):
                    # A newer owned write with no recoverable identity makes
                    # "the recent order" ambiguous.  Do not fall through to
                    # an older order and cancel the wrong one.
                    return {}
                continue
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            if requested and candidate_id != requested:
                continue
            if not candidate_from_ref:
                continue
            if str(getattr(task, "status", "") or "") != DONE:
                continue
            if self._normalize_status(ref.get("outcome")) in {
                    "failed", "uncertain"}:
                continue
            status = self._normalize_status(ref.get("status"))
            if status not in _CANCELLABLE_STATUSES:
                # recent() is newest-first.  A terminal record is a tombstone
                # for any older "created" record carrying the same order id.
                if requested:
                    return {}
                continue
            owned = {
                "order_id": candidate_id,
                "amount_cents": self._nonnegative_int(
                    ref.get("amount_cents")) or 0,
                "store_name": str(ref.get("store_name") or ""),
            }
            if session_id and str(
                    getattr(task, "session_id", "") or "") == session_id:
                return owned
            if not fallback:
                fallback = owned
        return fallback

    @staticmethod
    def _explicit_order_id(value) -> str:
        """Return a real explicit id, not a deictic planner placeholder."""
        text = str(value or "").strip()
        compact = re.sub(r"\s+", "", text)
        deictic = (
            "刚才", "刚刚", "最近", "上一", "上次", "上个", "那单",
            "那笔", "这单", "这笔", "我的订单", "我的瑞幸订单",
        )
        if (not re.search(r"[0-9]", compact) and
                any(marker in compact for marker in deictic)):
            return ""
        return text

    async def _read(self, name: str, arguments: dict):
        if not self._required_present(name, arguments):
            raise _BusinessError(f"{name} required arguments missing")
        response = await self.call_tool(name, arguments, write=False)
        _, payload = self._business_envelope(response)
        return payload

    @staticmethod
    def _business_envelope(response: dict, *, allow_scalar: bool = False):
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise _BusinessError("MCP protocol result not ok")
        envelope = response.get("data")
        if not isinstance(envelope, dict):
            raise _IncompleteSuccess("business envelope missing")
        success = envelope.get("success")
        if success is False:
            raise _BusinessReject("business success is false")
        if success is not True:
            raise _IncompleteSuccess("business success is missing")
        code = envelope.get("code")
        if code is None:
            raise _IncompleteSuccess("business code is missing")
        if isinstance(code, bool) or str(code) != "0":
            raise _BusinessReject("business code is not zero")
        payload = envelope.get("data")
        if (not allow_scalar and not isinstance(payload, (dict, list))) or (
                allow_scalar and payload is None):
            raise _IncompleteSuccess("business data missing")
        return envelope, payload

    def _arguments(self, name: str, dynamic: dict) -> dict:
        binding = self.tools[name]
        result = copy.deepcopy(getattr(binding.tool, "const_args", {}) or {})
        result.update({
            key: copy.deepcopy(value) for key, value in dynamic.items()
            if value not in (None, "")
        })
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
        for name in self.required_tools:
            binding = self.tools[name]
            payload[name] = {
                "schema": binding.input_schema,
                "const_args": getattr(binding.tool, "const_args", {}) or {},
                "pay_url_locator": str(
                    getattr(binding.tool, "pay_url_locator", "") or ""),
                "amount_locator": str(
                    getattr(binding.tool, "amount_locator", "") or ""),
                "amount_unit": str(
                    getattr(binding.tool, "amount_unit", "yuan") or "yuan"),
            }
        if "createOrder" in self.tools:
            payload["pay_url_hosts"] = list(
                getattr(self.server, "pay_url_hosts", []) or [])
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _owner(ctx) -> tuple[str, str]:
        return (str(getattr(ctx, "user_id", "") or "").strip(),
                str(getattr(ctx, "session_id", "") or "").strip())

    @classmethod
    def _trusted_store(cls, slots: dict, meta) -> tuple[str, float, float] | None:
        name = str(slots.get("store_name") or "").strip()
        longitude = cls._coordinate(
            slots.get("store_longitude"), longitude=True)
        latitude = cls._coordinate(
            slots.get("store_latitude"), longitude=False)
        if not name or longitude is None or latitude is None:
            return None
        raw = (meta or {}).get("_trusted_slot_refs", "")
        try:
            refs = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(refs, dict):
            return None
        required = {
            "store_name": "name",
            "store_longitude": "lng",
            "store_latitude": "lat",
        }
        prefixes = set()
        for slot_name, leaf in required.items():
            item = refs.get(slot_name)
            if (not isinstance(item, dict) or
                    item.get("producer_intent") != "nearby.search"):
                return None
            ref = str(item.get("ref") or "")
            # 两种合法来源，**同前缀同下标的约束对两者一字不改**：
            #   ① 本轮 plan 内的 nearby.search 步骤   `<step>.data.items.<N>.<leaf>`
            #   ② 跨轮焦点（2026-08-13）            `focus.last_places.<N>.<leaf>`
            # ② 的值同样只可能由**执行器**从服务端持有的上一轮结果写入（PlanContext，
            # LLM/客户端写不到）；放行的是来源，不是校验强度——name/lng/lat 仍必须
            # 全部来自同一条 POI，混拼一律拒。
            match = re.fullmatch(
                rf"([A-Za-z0-9_-]+\.data\.items\.\d+)\.{leaf}", ref
            ) or re.fullmatch(rf"(focus\.last_places\.\d+)\.{leaf}", ref)
            if match is None:
                return None
            prefixes.add(match.group(1))
        if len(prefixes) != 1:
            return None
        return name, longitude, latitude

    @staticmethod
    def _coordinate(value, *, longitude: bool) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        limit = 180 if longitude else 90
        if not math.isfinite(number) or not -limit <= number <= limit:
            return None
        return number

    @staticmethod
    def _quantity(value) -> int | None:
        text = str(value).strip()
        if not re.fullmatch(r"[0-9]+", text):
            return None
        quantity = int(text)
        return quantity if 1 <= quantity <= 20 else None

    @staticmethod
    def _positive_int(value) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 and str(value).strip() == str(number) else None

    @staticmethod
    def _nonnegative_int(value) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @staticmethod
    def _stores(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("shops", "shopList", "list", "data"):
                values = payload.get(key)
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, dict)]
        return []

    @staticmethod
    def _products(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("products", "productList", "list", "data"):
                values = payload.get(key)
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, dict)]
        return []

    @staticmethod
    def _is_open(store: dict) -> bool:
        status = str(store.get("workStatus") or "").strip().lower()
        if status in _CLOSED_STATUSES:
            return False
        return status in _OPEN_STATUSES

    @staticmethod
    def _is_available_product(product: dict) -> bool:
        if product.get("soldOut") is True:
            return False
        for key in ("saleStatus", "sellStatus", "workStatus"):
            if key not in product:
                continue
            value = str(product.get(key) or "").strip().lower()
            if value in {"0", "false", "sold_out", "soldout", "售罄", "已售罄"}:
                return False
        return True

    @classmethod
    def _matching_stores(cls, stores: list[dict], hint: str) -> list[dict]:
        needle = cls._normalized_store(hint)
        if not needle:
            return []
        return [store for store in stores
                if cls._overlaps(needle, cls._normalized_store(
                    store.get("deptName")))]

    @classmethod
    def _matching_products(cls, products: list[dict], query: str) -> list[dict]:
        needle = cls._normalized(query)
        if not needle:
            return []
        exact = [product for product in products
                 if cls._normalized(product.get("productName")) == needle]
        if exact:
            return exact
        return [product for product in products
                if cls._overlaps(
                    needle, cls._normalized(product.get("productName")))]

    @classmethod
    def _official_selection(cls, detail: dict, slot_name: str,
                            desired: str) -> tuple[int, int, bool] | None:
        groups = detail.get("productAttrs")
        if not isinstance(groups, list):
            return None
        desired_norm = cls._normalized_spec(desired)
        allowed_groups = {_ for _ in _SPEC_GROUPS[slot_name]}
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = cls._normalized_spec(group.get("attributeName"))
            if group_name not in allowed_groups:
                continue
            parent_id = cls._positive_int(group.get("attributeId"))
            subs = group.get("productSubAttrs")
            if parent_id is None or not isinstance(subs, list):
                return None
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                name = cls._normalized_spec(sub.get("attributeName"))
                if name != desired_norm:
                    continue
                if sub.get("canSelected") not in (1, True, "1"):
                    return None
                sub_id = cls._positive_int(sub.get("attributeId"))
                if sub_id is None:
                    return None
                selected = sub.get("selected") in (1, True, "1", "true")
                return parent_id, sub_id, selected
            return None
        return None

    @staticmethod
    def _normalized(value) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "",
                      str(value or "")).lower()

    @classmethod
    def _normalized_store(cls, value) -> str:
        text = cls._normalized(value)
        for token in ("瑞幸咖啡", "luckincoffee", "luckin", "瑞幸", "上海市", "上海"):
            text = text.replace(token, "")
        if text.endswith("店"):
            text = text[:-1]
        return text

    @classmethod
    def _normalized_spec(cls, value) -> str:
        text = cls._normalized(value)
        if text.endswith("的"):
            text = text[:-1]
        return text

    @staticmethod
    def _overlaps(left: str, right: str) -> bool:
        return bool(left and right and (left in right or right in left))

    async def _resolve_store(self, store, trusted, *, slots: dict,
                             user_id: str, session_id: str):
        """把可信 POI 三元组解析成官方门店 + deptId。

        返回 `(store, dept_id, refusal)`——`refusal` 非 None 时调用方原样返回。
        下单与看菜单**共用这一份**判定：门店可信链（同一轮 nearby.search 的同一条
        item）、打烊、坐标一致性、同名多店消歧，抄成两份就会漂移（B1 的成因）。
        """
        if store is None:
            if trusted is None:
                # 同 `_reselect_store`：这三个槽用户填不了，声明成 missing_slots
                # 会让会话挂起并吞掉后续每一句（真栈实证见那里的注释）。
                # 这条是**改动前就存在**的，用户会话里循环的正是它。
                return None, None, self.refused(
                    "请先查询附近的瑞幸门店并选择一家，"
                    "我只会使用该公开门店 POI 的坐标。",
                    "可以说“查询附近的瑞幸咖啡”，我再按您选的那家继续。")
            store_name, longitude, latitude = trusted
            try:
                shops = await self._read(
                    "queryShopList",
                    self._arguments("queryShopList", {
                        "deptName": store_name,
                        "longitude": longitude,
                        "latitude": latitude,
                    }))
                stores = self._stores(shops)
                if not stores:
                    # 高德的 POI 名与瑞幸官方 deptName 并不总是同一套写法
                    # （2026-08-13 真机取证：「瑞幸咖啡(前海华强金融大厦店)」→1 家，
                    # 「luckin coffee 瑞幸咖啡(华润前海23F内部店)」→0 家，
                    # 空名 + 同一坐标 →8 家）。按名查不到就**只用坐标**再查一次。
                    # 这不放松可信链：坐标仍然只来自那条公开 POI，且下面的
                    # 距离一致性 + 同名匹配两道闸原样生效，匹配不到就出候选卡让用户选，
                    # 比「没找到，请重新选择门店」这个死胡同诚实也有用。
                    shops = await self._read(
                        "queryShopList",
                        self._arguments("queryShopList", {
                            "deptName": "",
                            "longitude": longitude,
                            "latitude": latitude,
                        }))
                    stores = self._stores(shops)
            except Exception as exc:
                return None, None, self._read_failure("查询瑞幸门店", exc)

            if not stores:
                return None, None, self._reselect_store(
                    "没有找到与所选 POI 对应的瑞幸官方门店，请重新选择门店。")
            open_stores = [candidate for candidate in stores
                           if self._is_open(candidate)]
            if not open_stores:
                return None, None, self._reselect_store(
                    "找到的瑞幸门店已打烊，请换一家或稍后再试。")
            nearby_stores = [
                candidate for candidate in open_stores
                if self._near_trusted_poi(candidate, longitude, latitude)]
            if not nearby_stores:
                return None, None, self._reselect_store(
                    "官方门店的坐标或距离与所选 POI 不一致，没有继续下单，请重新选择门店。")
            matched = self._matching_stores(nearby_stores, store_name)
            if len(matched) != 1:
                return None, None, await self._store_choices(
                    matched or nearby_stores, slots=slots, user_id=user_id,
                    session_id=session_id, source={
                        "name": store_name, "longitude": longitude,
                        "latitude": latitude,
                    })
            store = matched[0]
        dept_id = self._positive_int(store.get("deptId"))
        official_longitude = self._coordinate(store.get("longitude"), longitude=True)
        official_latitude = self._coordinate(store.get("latitude"), longitude=False)
        if dept_id is None or official_longitude is None or official_latitude is None:
            return None, None, self._reselect_store(
                "官方门店信息不完整，不能继续下单，请换一家。")
        return store, dept_id, None

    async def menu(self, intent, ctx, meta) -> AgentResult:
        """只读：看这家瑞幸门店有什么可点。**不建草稿、不碰任何写工具。**

        存在的理由是「这家店的菜单」以前只能落到演示商户的 shop.menu
        （2026-08-12 trace 9899486a8773d577 答的是「（演示商户）拿铁 22 元起」）——
        目录里没有真实商户的只读菜单能力，模型没有选错，是**目录里没有更好的选项**。
        """
        slots = dict(getattr(intent, "slots", {}) or {})
        user_id, session_id = self._owner(ctx)
        store = None
        trusted = self._trusted_store(slots, meta)
        if trusted is None:
            resumed = await self._resume_store_choice(
                slots, user_id=user_id, session_id=session_id)
            if isinstance(resumed, AgentResult):
                return resumed
            if resumed is not None:
                slots, store = resumed
        store, dept_id, refusal = await self._resolve_store(
            store, trusted, slots=slots,
            user_id=user_id, session_id=session_id)
        if refusal is not None:
            return refusal

        # 2026-08-13 真机取证：`query=""` 被商户判 `code=1000 非法参数`，`query=" "`
        # 返回 0 条，任意实词返回**最多 3 条**——这个工具是「搜商品」不是「列全菜单」。
        # 所以①用户没说要什么时用种子词而不是空串；②话术不许说成「全部菜单」。
        asked = str(slots.get("item_query") or "").strip()
        query = asked or _MENU_SEED_QUERY
        try:
            products_data = await self._read(
                "searchProductForMcp",
                self._arguments("searchProductForMcp", {
                    "deptId": dept_id, "query": query,
                }))
        except Exception as exc:
            return self._read_failure("查询该门店菜单", exc)
        products = [product for product in self._products(products_data)
                    if self._is_available_product(product)]
        if not products:
            return AgentResult(
                status=NEED_SLOT,
                speech=(f"这家门店没查到可售的“{asked}”。" if asked
                        else "这家门店暂时没查到在售商品。"),
                follow_up="换个说法或换一家门店试试？",
                missing_slots=["item_query"])

        store_name = str(store.get("deptName") or "这家瑞幸")
        items = [self._menu_item(product) for product in products[:20]]
        listed = "、".join(
            item["name"] + (f"（{item['price']}）" if item.get("price") else "")
            for item in items[:5])
        # 用户问了什么就说什么；没问时说清这是「几款」而不是「全部」——
        # 商户接口一次最多给 3 条，把它播成菜单就是替商户编了个完整性承诺。
        speech = (f"{store_name}的“{asked}”有：{listed}。" if asked
                  else f"{store_name}可以点这几款：{listed}。")
        speech += "要哪一款？想喝别的直接说名字，我再帮你找。"
        return AgentResult(
            speech=speech,
            ui_card={
                "type": "merchant_choices",
                "stage": "choices",
                "choice_kind": "product",
                "merchant": "瑞幸",
                "store_name": store_name,
                "title": f"{store_name} 可点商品",
                "items": items,
                # options 与 items 同序但**各自自带 image_url**：渲染端按 options 取，
                # 靠下标去另一个数组捞图会在任一端裁剪时错位。
                "options": [
                    {"label": item["name"], "subtitle": item.get("price", ""),
                     "send_text": f"在{store_name}点一杯{item['name']}",
                     **({"image_url": item["image_url"]}
                        if item.get("image_url") else {})}
                    for item in items[:6]
                ],
            })

    def _menu_item(self, product: dict) -> dict:
        price = self._menu_price(product)
        item = {
            "id": str(product.get("productId") or ""),
            "name": str(product.get("productName") or "").strip(),
            "price": price,
            "subtitle": price,
        }
        image = self.image_url(product.get("pictureUrl"))
        if image:
            item["image_url"] = image
        return item

    @staticmethod
    def _menu_price(product: dict) -> str:
        """价格只在**商家确实给了**的时候展示——猜价格是最不该犯的错。

        字段名取自真机 searchProductForMcp 响应（`estimatePrice` 到手价优先、
        `initialPrice` 原价兜底），不是照常见命名猜的：第一版写了
        price/salePrice/discountPrice 四个键，真实响应里一个都不存在。
        """
        for key in ("estimatePrice", "initialPrice"):
            try:
                value = float(product.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return f"{value:.2f} 元"
        return ""

    @staticmethod
    def _reselect_store(speech: str) -> AgentResult:
        """门店类拒绝**不能是 NEED_SLOT**——它要的槽用户填不了。

        门店三元组按设计只能来自同一轮 `nearby.search` 的可信公开 POI；
        把它声明成 `missing_slots` 等于向用户要一个他永远给不出的东西，而引擎会据此
        **挂起会话**：此后每一句话都被当成补槽答案吞掉、再次失败、再次挂起。
        2026-08-13 真栈实证（demo-f1hkwr / demo-r6qjf4）：「看麦当劳(科苑南路餐厅)的详情」
        被答成「请先查询附近的瑞幸门店…」，日志 `Resuming plan ... (slot fill step s1)`
        ——问麦当劳答瑞幸，「第一个」「选择瑞幸门店：X」也全被吞掉。

        所以这里回到普通 OK 结果 + 可执行的 follow_up：用户下一句照常走规划。
        代价是 `require_confirm=true` 的下单步仍会被中央确认闸追加一句
        「确定继续吗？」（只读的 *.menu 不受影响，它 require_confirm=false）——
        那是**话术不好看**，而挂起黑洞是**功能不可用**，两害相权。
        """
        return MerchantWorkflow.refused(
            speech, "可以说“查询附近的瑞幸咖啡”，我再按您选的那家继续。")

    async def _store_choices(self, stores: list[dict], *, slots: dict,
                             user_id: str, session_id: str,
                             source: dict) -> AgentResult:
        ordered = sorted(
            stores,
            key=lambda store: self._distance(store.get("distance")))[:3]
        candidates = [{
            "deptId": self._positive_int(store.get("deptId")),
            "deptName": str(store.get("deptName") or "").strip(),
            "longitude": self._coordinate(
                store.get("longitude"), longitude=True),
            "latitude": self._coordinate(
                store.get("latitude"), longitude=False),
            "distance": self._distance(store.get("distance")),
            "workStatus": str(store.get("workStatus") or ""),
        } for store in ordered]
        candidates = [candidate for candidate in candidates
                      if candidate["deptId"] is not None and
                      candidate["deptName"] and
                      candidate["longitude"] is not None and
                      candidate["latitude"] is not None and
                      math.isfinite(candidate["distance"])]
        if len(candidates) < 2:
            return AgentResult(
                speech="门店候选信息不完整，请重新查询附近的瑞幸。")
        request_slots = {
            key: str(slots.get(key) or "").strip()
            for key in ("item_query", "quantity", "temperature", "ice",
                        "sweetness", "milk")
            if str(slots.get(key) or "").strip()
        }
        continuation = MerchantDraft(
            token=secrets.token_urlsafe(24), merchant=self.merchant,
            operation="select_store",
            user_id=user_id, session_id=session_id,
            store={
                "operation": "select_store",
                "source": {
                    "name": str(source.get("name") or "").strip(),
                    "longitude": self._coordinate(
                        source.get("longitude"), longitude=True),
                    "latitude": self._coordinate(
                        source.get("latitude"), longitude=False),
                },
                "candidates": candidates,
            },
            items=[], amount_cents=0,
            upstream_args={"request": request_slots},
            schema_digest=self._schema_digest(), created_at=time.time())
        if not await self.drafts.put(continuation):
            return AgentResult(
                speech="门店选择暂时无法安全保存，请重新查询。")
        choices = [MerchantChoice(
            id=str(candidate["deptId"]),
            name=candidate["deptName"],
            subtitle=self._store_subtitle(candidate),
            send_text=f"选择瑞幸门店：{candidate['deptName']}",
            data={"dept_id": str(candidate["deptId"])},
        ) for candidate in candidates]
        return AgentResult(
            status=NEED_SLOT,
            speech="找到多家可能的瑞幸门店，请选择其中一家。",
            follow_up="请选择门店。", missing_slots=["store_name"],
            ui_card=self.choice_card("store", choices),
            data={"checkout_token": continuation.token})

    async def _resume_store_choice(self, slots: dict, *, user_id: str,
                                   session_id: str):
        selected_name = str(slots.get("store_name") or "").strip()
        if not selected_name:
            return None
        choice_prefix = "选择瑞幸门店："
        if selected_name.startswith(choice_prefix):
            selected_name = selected_name[len(choice_prefix):].strip()
        draft = await self.drafts.consume_current(
            user_id=user_id, session_id=session_id, merchant=self.merchant,
            expected_action="select_store")
        if draft is None:
            return None
        operation = draft.operation
        if operation != "select_store":
            # A create/cancel confirmation snapshot is a different capability
            # state.  Put it back; button-like text cannot consume it.
            await self.drafts.put(draft)
            return None
        if draft.schema_digest != self._schema_digest():
            return AgentResult(
                status=NEED_SLOT,
                speech="商家接口已更新，请重新查询附近门店。",
                missing_slots=[
                    "store_name", "store_longitude", "store_latitude"])
        candidates = (draft.store or {}).get("candidates")
        source = (draft.store or {}).get("source")
        if not isinstance(candidates, list) or not isinstance(source, dict):
            return AgentResult(
                status=NEED_SLOT, speech="门店选择已失效，请重新查询。",
                missing_slots=[
                    "store_name", "store_longitude", "store_latitude"])
        selected_norm = self._normalized_store(selected_name)
        matches = [candidate for candidate in candidates
                   if isinstance(candidate, dict) and
                   self._normalized_store(candidate.get("deptName")) ==
                   selected_norm]
        source_lng = self._coordinate(source.get("longitude"), longitude=True)
        source_lat = self._coordinate(source.get("latitude"), longitude=False)
        if (len(matches) != 1 or source_lng is None or source_lat is None or
                not self._is_open(matches[0]) or
                not self._near_trusted_poi(matches[0], source_lng, source_lat)):
            return AgentResult(
                status=NEED_SLOT,
                speech="这家门店不在刚才的官方候选中，请重新查询后选择。",
                missing_slots=[
                    "store_name", "store_longitude", "store_latitude"])
        saved = (draft.upstream_args or {}).get("request")
        if not isinstance(saved, dict):
            return AgentResult(
                status=NEED_SLOT, speech="门店选择已失效，请重新查询。",
                missing_slots=[
                    "store_name", "store_longitude", "store_latitude"])
        restored = {str(key): str(value) for key, value in saved.items()}
        restored["store_name"] = str(matches[0].get("deptName") or "")
        return restored, copy.deepcopy(matches[0])

    def _product_choices(self, products: list[dict]) -> AgentResult:
        choices = [MerchantChoice(
            id=str(product.get("productId") or ""),
            name=str(product.get("productName") or "瑞幸饮品"),
            subtitle=self._product_subtitle(product),
            send_text=(f"选择瑞幸商品："
                       f"{str(product.get('productName') or '')}"),
            data={"product_id": str(product.get("productId") or "")},
        ) for product in products]
        return AgentResult(
            status=NEED_SLOT,
            speech="找到多款相近的瑞幸饮品，请选择具体商品。",
            follow_up="请选择饮品。", missing_slots=["item_query"],
            ui_card=self.choice_card("product", choices))

    @staticmethod
    def _distance(value) -> float:
        try:
            number = float(value)
            return number if math.isfinite(number) and number >= 0 else math.inf
        except (TypeError, ValueError):
            return math.inf

    @classmethod
    def _near_trusted_poi(cls, store: dict, longitude: float,
                          latitude: float) -> bool:
        official_lng = cls._coordinate(
            store.get("longitude"), longitude=True)
        official_lat = cls._coordinate(
            store.get("latitude"), longitude=False)
        declared = cls._distance(store.get("distance"))
        if official_lng is None or official_lat is None or not math.isfinite(
                declared):
            return False
        calculated = cls._haversine_km(
            longitude, latitude, official_lng, official_lat)
        # Public POI coordinates and merchant entrances can differ slightly,
        # but an identically named store several kilometres away is not the
        # selected POI.  The merchant's own distance claim must corroborate it.
        if calculated > 3.0 or declared > 3.0:
            return False
        return abs(declared - calculated) <= max(0.75, calculated * 0.5)

    @staticmethod
    def _haversine_km(lng1: float, lat1: float,
                      lng2: float, lat2: float) -> float:
        radius = 6371.0088
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        a = (math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) *
             math.sin(dl / 2) ** 2)
        a = min(1.0, max(0.0, a))
        return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def _store_subtitle(cls, store: dict) -> str:
        distance = cls._distance(store.get("distance"))
        distance_text = f"{distance:g} km" if distance != math.inf else ""
        return " · ".join(value for value in ("营业中", distance_text) if value)

    @staticmethod
    def _product_subtitle(product: dict) -> str:
        for key in ("estimatePrice", "initialPrice"):
            if product.get(key) is not None:
                try:
                    cents = yuan_to_cents(product.get(key))
                except ValueError:
                    continue
                return f"{cents // 100}.{cents % 100:02d} 元起"
        return ""

    @classmethod
    def _split_specifications(cls, value) -> list[str]:
        return [part.strip() for part in re.split(r"[/／、|]", str(value or ""))
                if part.strip()]

    @classmethod
    def _extract_order_id(cls, payload: dict) -> str:
        for path in _ORDER_ID_PATHS:
            value = cls._dig(payload, path)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                text = str(value).strip()
                if text and len(text) <= 128 and not re.search(r"\s", text):
                    return text
        return ""

    @classmethod
    def _order_status(cls, payload: dict) -> str:
        for path in _ORDER_STATUS_PATHS:
            normalized = cls._normalize_status(cls._dig(payload, path))
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _normalize_status(value) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "",
                      str(value or "")).lower()

    @classmethod
    def _dig_any(cls, value, locators: str):
        for locator in re.split(r"[|,]", str(locators or "")):
            path = locator.strip()
            if not path:
                continue
            found = cls._dig(value, path)
            if found not in (None, ""):
                return found
        return ""

    @staticmethod
    def _dig(value, path: str):
        current = value
        for part in str(path or "").split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return None
                current = current[index]
            else:
                return None
        return current

    @staticmethod
    def _order_buttons(order_id: str, *, include_cancel: bool) -> list[dict]:
        buttons = [{
            "label": "查订单", "send_text": f"查询瑞幸订单 {order_id}",
        }]
        if include_cancel:
            buttons.append({
                "label": "取消订单", "send_text": f"取消瑞幸订单 {order_id}",
            })
        return buttons

    @staticmethod
    def _read_failure(action: str, exc: Exception) -> AgentResult:
        logger.warning("瑞幸读链路失败（%s）：%s", action, type(exc).__name__)
        return MerchantWorkflow.refused(
            f"暂时无法{action}，没有创建订单，请稍后再试。")

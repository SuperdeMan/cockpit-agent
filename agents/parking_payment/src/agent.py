"""停车缴费 Agent —— 交易类生态 Agent。查找停车场 + 缴费(二次确认)。

Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult, NEED_CONFIRM, FAILED
from .providers import build_parking_provider

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class ParkingPaymentAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.parking = build_parking_provider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "parking.find":
            return await self._find(ctx)
        if intent.name == "parking.pay":
            return await self._pay(intent, meta)
        return AgentResult(status=FAILED, speech="停车助手暂不支持该请求。")

    async def _find(self, ctx) -> AgentResult:
        await ctx.fetch("vehicle.location")
        lots = await self.parking.find()
        items = [{"name": l.name, "available": l.available,
                  "price": f"{l.price_per_hour}元/小时", "distance_m": l.distance_m}
                 for l in lots]
        names = "、".join(f"{l.name}(余{l.available})" for l in lots[:3])
        return AgentResult(
            speech=f"附近找到 {len(lots)} 个停车场：{names}。需要导航过去吗？",
            ui_card={"type": "parking_list", "items": items},
        )

    async def _pay(self, intent, meta: dict) -> AgentResult:
        amount = intent.slots.get("amount", "")
        plate = intent.slots.get("plate", "")
        order_id = intent.slots.get("order_id", "current")
        fee_cents = 0
        if not amount and plate:
            fee_cents, err = await self.parking.get_fee("current", plate)
            if err:
                return AgentResult(status=FAILED, speech=f"查询费用失败：{err}")
            amount = f"{fee_cents / 100:.0f}元"
        detail = f"停车费{amount}" if amount else "当前停车订单费用"

        # 用户已二次确认（编排器只对挂起那一步注入 confirmed）→ 真正支付
        if meta.get("confirmed") == "true":
            ok, receipt = await self.parking.pay(order_id, plate, fee_cents)
            if not ok:
                return AgentResult(status=FAILED, speech=f"支付没有成功：{receipt}")
            return AgentResult(
                speech=f"已为您支付{detail}，祝您一路顺利。",
                ui_card={"type": "payment_receipt", "receipt_id": receipt,
                         "order_id": order_id, "amount": amount},
            )

        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"确认支付 {detail} 吗？",
            follow_up="说『确认』完成支付",
        ).action("parking.pay", {"order_id": order_id, "amount": amount},
                 require_confirm=True)

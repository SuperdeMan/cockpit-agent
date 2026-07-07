"""停车缴费 Agent —— 交易类生态 Agent。只做「缴费(二次确认)」。

停车场**发现**（找停车场/附近有没有停车场）归 nearby（真高德 POI）；原 parking.find 是重复的
mock（假空位、无 AMAP 源）已停用。

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
        # 停车场「发现」已归 nearby（真高德 POI）——本 Agent 只做缴费。
        if intent.name == "parking.pay":
            return await self._pay(intent, meta)
        return AgentResult(status=FAILED, speech="停车助手只负责缴费；找停车场请说『附近有没有停车场』。")

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

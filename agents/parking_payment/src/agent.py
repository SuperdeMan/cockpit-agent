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
        if intent.name == "parking.query_fee":
            return await self._query_fee(intent)
        if intent.name == "parking.pay":
            return await self._pay(intent, meta)
        return AgentResult(status=FAILED, speech="停车助手只负责查费与缴费；找停车场请说『附近有没有停车场』。")

    async def _query_fee(self, intent) -> AgentResult:
        """只查金额，**一分钱都不动**。

        出处：意图落域对抗测试 findings §6.1（`nq.parking-negate`「停车费先别交，
        我想先知道多少钱」定性为 `capability_gap`）。此前本 Agent 只有 `parking.pay`
        一个能力，**用户问的那件事系统答不了**，模型被迫从目录里选，只能选到唯一那个。
        `require_confirm` 兜住了钱不会自己出去，但那只说明严重度是「答非所问」而不是
        「误付款」——**描述治不了缺能力，补能力才治**。

        provider 侧 `get_fee` 早就存在（`ParkingProvider.get_fee`），缺的一直只是
        能力面上的声明与这一条分支。
        """
        plate = intent.slots.get("plate", "")
        order_id = intent.slots.get("order_id", "current")
        fee_cents, err = await self.parking.get_fee(order_id, plate)
        if err:
            return AgentResult(status=FAILED, speech=f"查询停车费用失败：{err}")
        amount = f"{fee_cents / 100:.0f}元"
        return AgentResult(
            speech=f"当前停车费是{amount}。需要现在缴费吗？",
            follow_up="说『交停车费』我就去付",
            ui_card={"type": "parking_fee", "order_id": order_id,
                     "plate": plate, "amount": amount},
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

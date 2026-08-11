"""停车缴费 Agent —— 交易类生态 Agent。只做「缴费(二次确认)」。

停车场**发现**（找停车场/附近有没有停车场）归 nearby（真高德 POI）；原 parking.find 是重复的
mock（假空位、无 AMAP 源）已停用。

支付经 payment-gateway（§9.17，2026-08-11 批 2 接线）：本 Agent 不持任何支付凭证、
不生成收据——第一趟 Authorize 建单出确认话术，用户确认后第二趟**同幂等键重取**
（confirm_token 的官方传递通道：token 只活在单次 handle() 栈内，不进
payload/ui_card/data）→ Capture 亮码 → HMI 出 payment_qr 卡，支付完成的回执由
网关轮询 worker 经统一主动引擎推送。金额单一真相=网关订单快照（confirmed 分支
刻意不重查费，防「确认 5 元扣 6 元」的漂移）。
"""
from __future__ import annotations
import hashlib
import os

from agents._sdk import BaseAgent, AgentResult, NEED_CONFIRM, FAILED
from agents._sdk.payment_client import PaymentClient
from .providers import build_parking_provider

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SCENE = "parking.pay"
# proto GetStatusResponse.Status 数值（避免在 Agent 侧硬依赖生成枚举对象）
_ST_CAPTURED = 2
_ST_REPAYABLE = {3, 4, 6}     # CANCELLED / FAILED / EXPIRED：单已关，需用户重新发起


def _idem_key(user_id: str, order_id: str, plate: str) -> str:
    """请求指纹（§9.17：**刻意不含金额**——金额漂移时幂等命中返回用户确认过的快照单）。"""
    raw = f"{user_id}|{_SCENE}|{order_id}|{plate}"
    return "pk_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


class ParkingPaymentAgent(BaseAgent):
    def __init__(self, payment: PaymentClient | None = None):
        super().__init__(_MANIFEST)
        self.parking = build_parking_provider()
        self.payment = payment or PaymentClient()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        # 停车场「发现」已归 nearby（真高德 POI）——本 Agent 只做缴费。
        if intent.name == "parking.query_fee":
            return await self._query_fee(intent)
        if intent.name == "parking.pay":
            return await self._pay(intent, ctx, meta)
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

    async def _pay(self, intent, ctx, meta: dict) -> AgentResult:
        plate = intent.slots.get("plate", "")
        order_id = intent.slots.get("order_id", "current")
        idem = _idem_key(ctx.user_id, order_id, plate)

        # ── 第二趟：用户已确认（编排器只对挂起那一步注入 confirmed）──
        if meta.get("confirmed") == "true":
            # 幂等重取：同键命中快照单（含同一 confirm_token）。amount 传占位值
            # ——命中路径参数被快照覆盖；刻意不重查费（金额漂移防线，§9.17）。
            auth = await self.payment.authorize(
                agent_id="parking-payment", user_id=ctx.user_id,
                vehicle_id=ctx.vehicle_id, scene=_SCENE, amount_cents=1,
                description="", idempotency_key=idem)
            if auth is None:
                return AgentResult(speech="支付服务暂时不可用，请稍后再试。")
            if auth.status == _ST_CAPTURED:
                return AgentResult(speech="这笔停车费已经支付过了，不用再付。")
            if auth.status in _ST_REPAYABLE:
                return AgentResult(
                    speech="之前的支付订单已关闭。要重新缴费的话，请再说一次『交停车费』。")
            cap = await self.payment.capture(auth.payment_id, auth.confirm_token)
            if cap is None or not cap.ok:
                err = (cap.error if cap else "") or "支付服务暂时不可用"
                return AgentResult(speech=f"支付没能发起：{err}。请稍后再试。")
            amount = f"{auth.amount_cents / 100:.0f}元"
            card = {
                "type": "payment_qr",
                "payment_id": auth.payment_id,
                "amount": amount,
                "scene": _SCENE,
                "qr_content": cap.qr_content,
                "qr_svg": getattr(cap, "qr_svg", ""),
                "pay_url": cap.pay_url,
                "expires_at_ms": cap.expires_at_ms,
            }
            if cap.provider_mode == "mock":
                card["_prov"] = {"mode": "mock", "vendor": "mock",
                                 "note": "模拟支付渠道"}
            return AgentResult(
                speech=f"好的，{amount}停车费的付款码已经出来了，"
                       f"请用手机扫码完成支付，支付成功后我会告诉您。",
                ui_card=card,
            )

        # ── 第一趟：查费 → 建单（渠道零动作）→ NEED_CONFIRM ──
        fee_cents, err = await self.parking.get_fee(order_id, plate)
        if err:
            return AgentResult(speech=f"查询停车费用失败：{err}")
        desc = f"停车费（{plate}）" if plate else "停车费"
        auth = await self.payment.authorize(
            agent_id="parking-payment", user_id=ctx.user_id,
            vehicle_id=ctx.vehicle_id, scene=_SCENE, amount_cents=fee_cents,
            description=desc, idempotency_key=idem)
        if auth is None:
            return AgentResult(speech="支付服务暂时不可用，请稍后再试。")
        if auth.status == _ST_CAPTURED:
            return AgentResult(speech="这笔停车费已经支付过了，不用再付。")
        # confirm_token 是栈变量，到此为止——不进 speech/ui_card/data/action payload
        return AgentResult(
            status=NEED_CONFIRM,
            speech=auth.confirm_prompt,     # 网关按订单快照生成——用户点头的金额=扣的金额
            follow_up="说『确认』我就展示付款码",
        ).action("parking.pay", {"order_id": order_id, "plate": plate},
                 require_confirm=True)

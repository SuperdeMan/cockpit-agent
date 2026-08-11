"""支付轮询 worker——pending_pay → captured 的唯一推进者（§9.17）。

- 网关**进程内** asyncio task：查单要签名、签名必须在凭证域内，独立 poller 服务
  =渠道凭证第二注入点，不做（设计 2.4）。
- 轮询集持久在 Redis zset `payment:poll`：启动即接续存量（`due_polls` 直接读 zset），
  停机不丢钱——渠道侧照收，最坏回执迟到，上限=二维码有效期。
- 步进按单龄：<30s 每 3s、<60s 每 5s、之后 8s（elapsed 从 created_at 算，零额外状态）。
- merchant_hosted 只做过期收口（schedule 在 expires_at 一次到位，中间不空轮）；
  **永不**推进它到 captured——商户是真相源。
- 终态推送经统一主动引擎（`user_contract` 档：支付回执是用户明确期待的事；
  dedup_key=payment|{payment_id}——每单终态只发一次，天然幂等）。
- 单实例假设：worker 是 pending_pay→captured 的唯一写者；多副本需 per-payment
  SETNX 租约，v1 不做。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

try:
    from providers import CLOSED, PAID, PaymentChannelError
    from store import PaymentOrder, PaymentStore
except ImportError:      # 包形态（tests），同 server.py 的双形态导入
    from .providers import CLOSED, PAID, PaymentChannelError
    from .store import PaymentOrder, PaymentStore

logger = logging.getLogger("payment.worker")

_TICK_S = 3.0


def _next_interval(order: PaymentOrder, now: float) -> float:
    age = now - order.created_at
    if age < 30:
        return 3.0
    if age < 60:
        return 5.0
    return 8.0


class PollWorker:
    def __init__(self, store: PaymentStore, providers: dict, *,
                 audit=None, nc=None):
        self.store = store
        self.providers = providers
        self._audit = audit
        self._nc = nc                 # NATS（可为 None：proactive 客户端自会跳过）
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        logger.info("PollWorker 启动（tick=%ss；轮询集接续 Redis zset 存量）", _TICK_S)
        while not self._stopped.is_set():
            try:
                await self.tick()
            except Exception as e:    # worker 循环永不因单次异常退出
                logger.warning("PollWorker tick 异常（继续）：%s", e)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_TICK_S)
            except asyncio.TimeoutError:
                pass

    async def tick(self, now: float | None = None) -> int:
        """处理一批到期单。返回处理数（测试口径）。"""
        now = now or time.time()
        due = await self.store.due_polls(now)
        for order in due:
            await self._handle(order, now)
        return len(due)

    async def _handle(self, order: PaymentOrder, now: float) -> None:
        if order.status != "pending_pay":
            await self.store.unschedule(order.payment_id)
            return

        # merchant_hosted：只有过期收口，不查渠道（商户是真相源）
        if order.channel == "merchant_hosted":
            if order.expires_at and now >= order.expires_at:
                await self.store.mark_expired(order.payment_id)
                logger.info("merchant_hosted 会话过期收口：%s", order.payment_id)
            else:
                await self.store.schedule_poll(order.payment_id,
                                               order.expires_at or now + 60)
            return

        provider = self.providers.get(order.provider_key)
        if provider is None:
            logger.warning("订单 %s 的渠道 %s 未配置——退避重轮",
                           order.payment_id, order.provider_key)
            await self.store.schedule_poll(order.payment_id, now + 30)
            return

        try:
            st = await provider.query(order.payment_id)
        except PaymentChannelError as e:
            logger.warning("查单失败 %s（退避重轮）：%s", order.payment_id, e)
            await self.store.schedule_poll(order.payment_id, now + 8)
            return

        if st.state == PAID:
            updated = await self.store.mark_captured(order.payment_id,
                                                     trade_no=st.trade_no)
            if updated:
                logger.info("支付完成：%s trade_no=%s", order.payment_id, st.trade_no)
                if self._audit:
                    self._audit.payment_captured(order.agent_id, order.payment_id,
                                                 order.amount_cents,
                                                 trade_no=st.trade_no)
                await self._emit_span(updated, "captured")
                await self._notify_paid(updated)
            return
        if st.state == CLOSED:
            await self.store.mark_cancelled(order.payment_id)
            await self._emit_span(order, "cancelled")
            return
        # WAITING：过期收口或排下一轮
        if order.expires_at and now >= order.expires_at:
            try:
                await provider.close(order.payment_id)
            except PaymentChannelError as e:
                logger.warning("过期关单失败 %s（本地照常过期）：%s",
                               order.payment_id, e)
            await self.store.mark_expired(order.payment_id)
            await self._emit_span(order, "expired")
            return
        await self.store.schedule_poll(order.payment_id,
                                       now + _next_interval(order, now))

    async def _emit_span(self, order: PaymentOrder, outcome: str) -> None:
        try:
            from observability import events as obs_events
            await obs_events.get_emitter("payment").emit_span(
                "payment-worker", "payment.poll", attrs={
                    "payment_id": order.payment_id, "outcome": outcome,
                    "provider": order.provider_key,
                })
        except Exception:
            pass

    async def _notify_paid(self, order: PaymentOrder) -> None:
        """支付回执经统一主动引擎（§9.8 信封；fail-open 由客户端保证）。"""
        amount = f"{order.amount_cents // 100}.{order.amount_cents % 100:02d}"
        card = {
            "type": "payment_receipt",
            "receipt_id": order.trade_no or order.payment_id,
            "order_id": order.external_order_ref or order.payment_id,
            "amount": f"{amount}元",
            "scene": order.scene,
        }
        if order.provider_mode == "mock":
            card["_prov"] = {"mode": "mock", "vendor": "mock",
                             "note": "模拟支付渠道"}
        payload = {
            "type": "payment_result",
            "agent_id": "payment-gateway",
            "user_id": order.user_id,
            "speech": f"您的{order.description or order.scene}已支付成功，共 {amount} 元。",
            "ui_card": card,
            "priority": "user_contract",
            "dedup_key": f"payment|{order.payment_id}",
        }
        try:
            from runtime.proactive import publish_proactive
            outcome = await publish_proactive(self._nc, payload)
            logger.info("支付回执推送：%s（%s）", order.payment_id, outcome)
        except Exception as e:
            logger.warning("支付回执推送失败（不影响订单终态）：%s", e)

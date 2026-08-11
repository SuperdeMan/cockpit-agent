"""Mock 支付渠道——e2e/CI 无凭证走通全状态机的确定性替身。

`PAYMENT_MOCK_AUTOPAY_S` 模拟「用户扫码支付完成」的延迟：
- N>0：create_qr 后 N 秒起 query 返回 PAID（e2e 默认 8s，够卡片渲染与轮询各跑一轮）
- 0：立即 PAID（单测快路径）
- -1：永不 PAID（测过期收口路径）

进程重启后（worker 从 zset 续轮）对没见过的单：AUTOPAY_S>=0 直接 PAID——
mock 的语义是「用户总会付」，续轮单立即完成保证 e2e 稳定；-1 仍恒 WAITING。
"""
from __future__ import annotations

import os
import time

from .base import (CLOSED, PAID, WAITING, ChannelStatus, PaymentProvider,
                   QrResult)


class MockPaymentProvider(PaymentProvider):
    channel = "mock"
    mode = "mock"

    def __init__(self):
        self._created: dict[str, float] = {}
        self._closed: set[str] = set()
        self._refunded: set[str] = set()

    @staticmethod
    def _autopay_s() -> float:
        try:
            return float(os.getenv("PAYMENT_MOCK_AUTOPAY_S", "8"))
        except ValueError:
            return 8.0

    async def create_qr(self, payment_id: str, amount_cents: int,
                        description: str) -> QrResult:
        self._created[payment_id] = time.time()
        return QrResult(qr_content=f"mockpay://{payment_id}")

    async def query(self, payment_id: str) -> ChannelStatus:
        if payment_id in self._closed:
            return ChannelStatus(state=CLOSED)
        autopay = self._autopay_s()
        if autopay < 0:
            return ChannelStatus(state=WAITING)
        created = self._created.get(payment_id)
        if created is None or time.time() - created >= autopay:
            return ChannelStatus(state=PAID, trade_no=f"mocktrade_{payment_id}")
        return ChannelStatus(state=WAITING)

    async def close(self, payment_id: str) -> bool:
        self._closed.add(payment_id)
        return True

    async def refund(self, payment_id: str, amount_cents: int,
                     reason: str) -> tuple[bool, str]:
        if payment_id in self._refunded:
            return True, f"{payment_id}_r1"   # 幂等：重复退款返回同一凭证
        self._refunded.add(payment_id)
        return True, f"{payment_id}_r1"

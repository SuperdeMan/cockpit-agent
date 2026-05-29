"""支付订单存储（Redis 优先，内存兜底）。"""
from __future__ import annotations
import os
import time
import uuid
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("payment.store")

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


@dataclass
class PaymentOrder:
    payment_id: str = ""
    agent_id: str = ""
    user_id: str = ""
    vehicle_id: str = ""
    scene: str = ""
    amount_cents: int = 0
    currency: str = "CNY"
    description: str = ""
    status: str = "authorized"  # authorized | captured | cancelled | failed
    idempotency_key: str = ""
    confirm_token: str = ""
    created_at: float = field(default_factory=time.time)


class PaymentStore:
    def __init__(self):
        self._url = os.getenv("REDIS_URL", "")
        self._r = None
        self._mem: dict[str, PaymentOrder] = {}
        self._idem: dict[str, str] = {}  # idempotency_key -> payment_id

    async def _redis(self):
        if aioredis and self._url and self._r is None:
            try:
                self._r = aioredis.from_url(self._url, decode_responses=True)
                await self._r.ping()
            except Exception:
                self._r = None
        return self._r

    async def authorize(self, agent_id: str, user_id: str, vehicle_id: str,
                        scene: str, amount_cents: int, currency: str,
                        description: str, idempotency_key: str) -> PaymentOrder:
        """创建预授权订单。幂等：同 key 不重复创建。"""
        # 幂等检查
        if idempotency_key in self._idem:
            existing_id = self._idem[idempotency_key]
            existing = await self.get(existing_id)
            if existing:
                return existing

        order = PaymentOrder(
            payment_id=f"pay_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id, user_id=user_id, vehicle_id=vehicle_id,
            scene=scene, amount_cents=amount_cents, currency=currency,
            description=description, status="authorized",
            idempotency_key=idempotency_key,
            confirm_token=uuid.uuid4().hex,
        )

        self._mem[order.payment_id] = order
        self._idem[idempotency_key] = order.payment_id
        logger.info("Authorized: %s (%s, %d %s)", order.payment_id, scene, amount_cents, currency)
        return order

    async def capture(self, payment_id: str, confirm_token: str) -> tuple[bool, str]:
        """确认扣款。返回 (ok, receipt_id_or_error)。"""
        order = self._mem.get(payment_id)
        if not order:
            return False, "订单不存在"
        if order.status != "authorized":
            return False, f"订单状态异常: {order.status}"
        if order.confirm_token != confirm_token:
            return False, "确认 token 不匹配"

        order.status = "captured"
        receipt_id = f"rcpt_{uuid.uuid4().hex[:8]}"
        logger.info("Captured: %s -> %s", payment_id, receipt_id)
        return True, receipt_id

    async def cancel(self, payment_id: str) -> bool:
        order = self._mem.get(payment_id)
        if not order or order.status != "authorized":
            return False
        order.status = "cancelled"
        logger.info("Cancelled: %s", payment_id)
        return True

    async def get(self, payment_id: str) -> PaymentOrder | None:
        return self._mem.get(payment_id)

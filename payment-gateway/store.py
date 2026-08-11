"""支付订单存储（Redis 真读写，内存兜底）。契约 docs/conventions.md §9.17。

- 键形态：hash `payment:order:{payment_id}`（全字段）、string `payment:idem:{key}`
  （幂等键 → payment_id，TTL 24h——幂等窗口，防键膨胀）、zset `payment:poll`
  （member=payment_id，score=下次轮询时刻 epoch 秒；worker 重启续轮的持久轮询集）。
- 订单键**不设 TTL**：隐私登记 lifecycle=retained_audit（金融审计与拒付窗口），
  删除走 `payment_redact_owner`（脱敏 owner 保留金额/状态）而非整删。
- backend **启动时定命**：首次连接成败决定走 Redis 还是内存，之后不切换——
  运行中掉线的操作如实抛错，不静默掉回内存造成状态分裂（两边各有半份订单）。
- 单实例假设（compose 单副本）：幂等用 SET NX 防 async 交错已够；多副本需
  per-payment 租约（SETNX lease），v1 不做。
"""
from __future__ import annotations

import os
import time
import uuid
import logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("payment.store")

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

# 隐私目标登记镜像（权威在 runtime/privacy_registry.py，形态改动必须两处同步 §9.17）
PERSONAL_DATA_TARGETS = (
    {
        # Redis 为主形态；进程内存是 Redis 不可达时的兜底形态——兜底存在就必须登记。
        "id": "payment_order",
        "storage_variants": ("payment:order:*", "payment:idem:*",
                            "PaymentStore._mem", "PaymentStore._idem"),
    },
)

# 终态集合（不可再迁移）
TERMINAL = frozenset({"captured", "cancelled", "failed", "expired", "refunded"})
# 合法迁移表：mark_* 只认这张表，表外迁移拒绝并 warning
_TRANSITIONS = {
    "pending_pay": {"authorized"},
    "captured": {"pending_pay"},
    "cancelled": {"authorized", "pending_pay"},
    "expired": {"pending_pay"},
    "failed": {"authorized", "pending_pay"},
    "refunding": {"captured"},
    "refunded": {"refunding"},
}

_IDEM_TTL_S = 24 * 3600
_ORDER_PREFIX = "payment:order:"
_IDEM_PREFIX = "payment:idem:"
_POLL_ZSET = "payment:poll"


def max_amount_fen() -> int:
    try:
        return int(os.getenv("PAYMENT_MAX_AMOUNT_FEN", "20000"))
    except ValueError:
        return 20000


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
    # authorized | pending_pay | captured | cancelled | failed | expired | refunding | refunded
    status: str = "authorized"
    idempotency_key: str = ""
    confirm_token: str = ""
    channel: str = ""            # alipay_qr | wechat_qr | merchant_hosted | ""(未定)
    provider_key: str = ""       # 实际执行渠道：alipay | wechat | mock（provider_mode 依据）
    external_pay_url: str = ""   # merchant_hosted：商户支付链接
    external_order_ref: str = "" # merchant_hosted：商户订单号
    qr_content: str = ""
    pay_url: str = ""
    channel_trade_ref: str = ""
    trade_no: str = ""
    fail_reason: str = ""
    refund_id: str = ""
    expires_at: float = 0.0      # epoch 秒（pending_pay 的过期收口时刻）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def provider_mode(self) -> str:
        return "mock" if self.provider_key == "mock" else "real"


_INT_FIELDS = {"amount_cents"}
_FLOAT_FIELDS = {"expires_at", "created_at", "updated_at"}


def _to_hash(order: PaymentOrder) -> dict[str, str]:
    return {k: str(v) for k, v in asdict(order).items()}


def _from_hash(data: dict[str, str]) -> PaymentOrder | None:
    if not data:
        return None
    kwargs = {}
    for k, v in data.items():
        if k in _INT_FIELDS:
            kwargs[k] = int(v or 0)
        elif k in _FLOAT_FIELDS:
            kwargs[k] = float(v or 0)
        else:
            kwargs[k] = v
    known = {f for f in PaymentOrder.__dataclass_fields__}
    return PaymentOrder(**{k: v for k, v in kwargs.items() if k in known})


class PaymentStore:
    def __init__(self):
        self._url = os.getenv("REDIS_URL", "")
        self._r = None
        self._backend: str | None = None      # "redis" | "memory"，首连后定命
        self._mem: dict[str, PaymentOrder] = {}
        self._idem: dict[str, str] = {}       # idempotency_key -> payment_id
        self._poll_mem: dict[str, float] = {} # payment_id -> next_poll_at（内存兜底）

    async def _redis(self):
        if self._backend == "memory":
            return None
        if self._r is None:
            if not (aioredis and self._url):
                self._backend = "memory"
                logger.warning("PaymentStore 走内存兜底（无 redis 库或 REDIS_URL）——"
                               "订单不跨重启，幂等不跨副本")
                return None
            try:
                self._r = aioredis.from_url(
                    self._url, decode_responses=True, socket_timeout=3,
                    socket_connect_timeout=3, socket_keepalive=True,
                    health_check_interval=30, retry_on_timeout=True)
                await self._r.ping()
                self._backend = "redis"
            except Exception as e:
                self._r = None
                self._backend = "memory"
                logger.warning("PaymentStore Redis 连接失败（%s）——内存兜底，"
                               "订单不跨重启", e)
                return None
        return self._r

    # ── 建单与幂等 ───────────────────────────────────────────────

    async def authorize(self, agent_id: str, user_id: str, vehicle_id: str,
                        scene: str, amount_cents: int, currency: str,
                        description: str, idempotency_key: str,
                        channel: str = "", provider_key: str = "",
                        external_pay_url: str = "",
                        external_order_ref: str = "") -> PaymentOrder:
        """创建预授权订单。幂等：同 key 返回同一订单（含同一 confirm_token，不轮换）。

        金额/币种 fail-closed：非法直接抛 ValueError，绝不建一张「回头再改」的单。

        **幂等查找先于参数校验**（批 2 接线定稿）：confirm_token 的官方传递通道是
        「第二趟同键重取」（§9.17），重取方手上没有金额（刻意不重查费，防漂移），
        传占位值也必须命中快照单。命中后按状态分流：
        - captured/refunding/refunded/authorized/pending_pay → 返回原单（防双付）；
        - cancelled/expired/failed → **落到新建、幂等键 remap**——幂等防的是双付，
          不是防用户重新尝试一次已经关掉的支付。
        """
        if not idempotency_key:
            raise ValueError("idempotency_key 必填（幂等三层链的第一层，§9.17）")

        existing = await self._idem_lookup(idempotency_key)
        if existing and existing.status not in ("cancelled", "expired", "failed"):
            return existing

        if amount_cents < 0:
            raise ValueError(f"amount_cents 不得为负（got {amount_cents}）")
        if amount_cents == 0 and channel != "merchant_hosted":
            # merchant_hosted 例外：支付发生在商户收银台，商户响应可能不回金额
            # ——登记 0 是「金额未知」的诚实表达，不造数（§9.17）。自有收单的
            # 0 元单没有意义，照旧拒绝。
            raise ValueError("amount_cents 必须为正（仅 merchant_hosted 允许 0=未知）")
        if (currency or "CNY") != "CNY":
            raise ValueError(f"currency 仅支持 CNY（got {currency!r}）")
        cap = max_amount_fen()
        if amount_cents > cap:
            raise ValueError(
                f"金额 {amount_cents} 分超单笔上限 PAYMENT_MAX_AMOUNT_FEN={cap}——"
                f"fail-closed 拒绝")

        order = PaymentOrder(
            payment_id=f"pay_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id, user_id=user_id, vehicle_id=vehicle_id,
            scene=scene, amount_cents=amount_cents, currency=currency or "CNY",
            description=description, status="authorized",
            idempotency_key=idempotency_key,
            confirm_token=uuid.uuid4().hex,
            channel=channel, provider_key=provider_key,
            external_pay_url=external_pay_url,
            external_order_ref=external_order_ref,
        )

        remap = existing is not None      # 可重付终态：幂等键改指新单（覆盖写）
        r = await self._redis()
        if r is not None:
            if remap:
                await r.set(_IDEM_PREFIX + idempotency_key, order.payment_id,
                            ex=_IDEM_TTL_S)
            else:
                # SET NX：async 交错下只有一个赢家；输家读赢家的单
                won = await r.set(_IDEM_PREFIX + idempotency_key, order.payment_id,
                                  nx=True, ex=_IDEM_TTL_S)
                if not won:
                    dup = await self._idem_lookup(idempotency_key)
                    if dup:
                        return dup
            await r.hset(_ORDER_PREFIX + order.payment_id, mapping=_to_hash(order))
        else:
            if not remap and idempotency_key in self._idem:
                dup = self._mem.get(self._idem[idempotency_key])
                if dup:
                    return dup
            self._mem[order.payment_id] = order
            self._idem[idempotency_key] = order.payment_id
        logger.info("Authorized: %s (%s, %d %s, channel=%s)",
                    order.payment_id, scene, amount_cents, order.currency,
                    channel or "unset")
        return order

    async def _idem_lookup(self, idempotency_key: str) -> PaymentOrder | None:
        r = await self._redis()
        if r is not None:
            pid = await r.get(_IDEM_PREFIX + idempotency_key)
            return await self.get(pid) if pid else None
        pid = self._idem.get(idempotency_key)
        return self._mem.get(pid) if pid else None

    # ── 读取 ─────────────────────────────────────────────────────

    async def get(self, payment_id: str) -> PaymentOrder | None:
        if not payment_id:
            return None
        r = await self._redis()
        if r is not None:
            return _from_hash(await r.hgetall(_ORDER_PREFIX + payment_id))
        return self._mem.get(payment_id)

    async def get_for_capture(self, payment_id: str,
                              confirm_token: str) -> tuple[PaymentOrder | None, str]:
        """Capture 前置校验：单在、态对、token 对。返回 (order, error)。

        pending_pay 重入是合法路径（返回订单让 server 回缓存二维码，不重打渠道）。
        """
        order = await self.get(payment_id)
        if not order:
            return None, "订单不存在"
        if order.status == "pending_pay":
            return order, ""          # 重入：回缓存码
        if order.status != "authorized":
            return None, f"订单状态异常: {order.status}"
        if not confirm_token or order.confirm_token != confirm_token:
            return None, "确认 token 不匹配"
        return order, ""

    # ── 状态迁移 ─────────────────────────────────────────────────

    async def _save(self, order: PaymentOrder) -> None:
        order.updated_at = time.time()
        r = await self._redis()
        if r is not None:
            await r.hset(_ORDER_PREFIX + order.payment_id, mapping=_to_hash(order))
        else:
            self._mem[order.payment_id] = order

    async def _transition(self, payment_id: str, to_status: str,
                          mutate=None) -> PaymentOrder | None:
        order = await self.get(payment_id)
        if not order:
            logger.warning("状态迁移失败：%s 不存在（→%s）", payment_id, to_status)
            return None
        if order.status == to_status:
            return order   # 幂等重放
        allowed = _TRANSITIONS.get(to_status, set())
        if order.status not in allowed:
            logger.warning("非法状态迁移拒绝：%s %s→%s（允许自 %s）",
                           payment_id, order.status, to_status, sorted(allowed))
            return None
        order.status = to_status
        if mutate:
            mutate(order)
        await self._save(order)
        logger.info("Payment %s: → %s", payment_id, to_status)
        return order

    async def mark_pending_pay(self, payment_id: str, *, qr_content: str = "",
                               pay_url: str = "", channel_trade_ref: str = "",
                               expires_at: float = 0.0) -> PaymentOrder | None:
        """亮码成功（或 merchant_hosted 登记）。confirm_token 就此作废（单次有效）。"""
        def _m(o: PaymentOrder):
            o.qr_content = qr_content or o.qr_content
            o.pay_url = pay_url or o.pay_url
            o.channel_trade_ref = channel_trade_ref or o.channel_trade_ref
            o.expires_at = expires_at or o.expires_at
            o.confirm_token = ""
        return await self._transition(payment_id, "pending_pay", _m)

    async def mark_captured(self, payment_id: str,
                            trade_no: str = "") -> PaymentOrder | None:
        def _m(o: PaymentOrder):
            o.trade_no = trade_no or o.trade_no
        order = await self._transition(payment_id, "captured", _m)
        if order:
            await self.unschedule(payment_id)
        return order

    async def mark_cancelled(self, payment_id: str) -> PaymentOrder | None:
        order = await self._transition(payment_id, "cancelled")
        if order:
            await self.unschedule(payment_id)
        return order

    async def mark_expired(self, payment_id: str) -> PaymentOrder | None:
        order = await self._transition(payment_id, "expired")
        if order:
            await self.unschedule(payment_id)
        return order

    async def mark_failed(self, payment_id: str,
                          reason: str = "") -> PaymentOrder | None:
        def _m(o: PaymentOrder):
            o.fail_reason = (reason or "")[:200]
        order = await self._transition(payment_id, "failed", _m)
        if order:
            await self.unschedule(payment_id)
        return order

    async def mark_refunding(self, payment_id: str) -> PaymentOrder | None:
        return await self._transition(payment_id, "refunding")

    async def mark_refunded(self, payment_id: str,
                            refund_id: str = "") -> PaymentOrder | None:
        def _m(o: PaymentOrder):
            o.refund_id = refund_id or o.refund_id
        return await self._transition(payment_id, "refunded", _m)

    # ── 轮询集（worker 的持久待办）─────────────────────────────────

    async def schedule_poll(self, payment_id: str, at: float) -> None:
        r = await self._redis()
        if r is not None:
            await r.zadd(_POLL_ZSET, {payment_id: at})
        else:
            self._poll_mem[payment_id] = at

    async def unschedule(self, payment_id: str) -> None:
        r = await self._redis()
        if r is not None:
            await r.zrem(_POLL_ZSET, payment_id)
        else:
            self._poll_mem.pop(payment_id, None)

    async def due_polls(self, now: float, limit: int = 50) -> list[PaymentOrder]:
        """到期待轮询的单。**孤儿自清**：单没了/已终态却还挂在轮询集 → 摘除。"""
        r = await self._redis()
        if r is not None:
            pids = await r.zrangebyscore(_POLL_ZSET, "-inf", now, start=0, num=limit)
        else:
            pids = [p for p, at in sorted(self._poll_mem.items(), key=lambda kv: kv[1])
                    if at <= now][:limit]
        out: list[PaymentOrder] = []
        for pid in pids:
            order = await self.get(pid)
            if order is None or order.status in TERMINAL:
                await self.unschedule(pid)
                continue
            out.append(order)
        return out

    # ── 隐私（payment_redact_owner，lifecycle=retained_audit）────────

    async def redact_owner(self, user_id: str) -> int:
        """脱敏 owner：清 user_id/vehicle_id/description，保留金额/状态/时间（审计）。

        对齐隐私登记 retain_or_redact_action=payment_redact_owner：金融审计要求
        留账，GDPR 要求断人——脱敏而非整删。
        """
        if not user_id:
            return 0
        n = 0
        r = await self._redis()
        if r is not None:
            async for key in r.scan_iter(match=_ORDER_PREFIX + "*", count=200):
                if await r.hget(key, "user_id") == user_id:
                    await r.hset(key, mapping={"user_id": "[redacted]",
                                               "vehicle_id": "[redacted]",
                                               "description": "[redacted]"})
                    n += 1
        else:
            for order in self._mem.values():
                if order.user_id == user_id:
                    order.user_id = "[redacted]"
                    order.vehicle_id = "[redacted]"
                    order.description = "[redacted]"
                    n += 1
        return n

    async def count_for_owner(self, user_id: str) -> int:
        """隐私探针（gdpr_md_payment_order_count）：该 owner 名下未脱敏订单数。"""
        if not user_id:
            return 0
        n = 0
        r = await self._redis()
        if r is not None:
            async for key in r.scan_iter(match=_ORDER_PREFIX + "*", count=200):
                if await r.hget(key, "user_id") == user_id:
                    n += 1
        else:
            n = sum(1 for o in self._mem.values() if o.user_id == user_id)
        return n

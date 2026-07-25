"""主动治理器核心（M3 P0）——「该不该现在打扰驾驶员」的唯一裁决点。

设计要点（子 RFC `docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md` §2）：

- **零 kind 字面量**：本文件不得出现任何具体 agent_id / type 值。优先级、情境断言、
  去重键、TTL 全由生产方在信封里声明，中央只实现通用策略。这条由源码断言测试钉死
  （M2 Outcome Verifier「防长成下一个 fast_intent.py」的同款铁律）。
- **单条通过 = 去掉治理键后原样转发**：网关与 HMI 看到的字节与治理器上线前逐字一致。
- **不改写事实**：合并只做确定性拼接，全程零 LLM。
- **产物只有话术 + 建议卡**：主动路径零执行权（沿用场景触发 D6 底线）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .evaluate import SAT, enrich, evaluate_all

logger = logging.getLogger("proactive.governor")

P_CRITICAL, P_USER_CONTRACT = "critical", "user_contract"
P_ADVISORY, P_AMBIENT = "advisory", "ambient"
# 排序即优先级（越小越先说）；未知档位按最保守的 ambient 处理
_RANK = {P_CRITICAL: 0, P_USER_CONTRACT: 1, P_ADVISORY: 2, P_AMBIENT: 3}
_GOVERNABLE = (P_ADVISORY, P_AMBIENT)      # 受免打扰/负荷/频控三闸约束的档位

# 信封里的治理键：转发前一律剥掉（下游契约不变）
GOVERNANCE_KEYS = ("priority", "conditions", "dedup_key", "ttl_ms")

DELIVERED, MERGED, DEFERRED, DROPPED = "delivered", "merged", "deferred", "dropped"
ACCEPTED = "accepted"          # 进了待发队列，投递决议随后经 _decide 给出


@dataclass
class Item:
    payload: dict
    priority: str
    conditions: list
    dedup_key: str
    ttl_ms: int
    accepted_at: float
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def agent_id(self) -> str:
        return str(self.payload.get("agent_id") or "")

    @property
    def type(self) -> str:
        return str(self.payload.get("type") or "")

    @property
    def rank(self) -> int:
        return _RANK.get(self.priority, _RANK[P_AMBIENT])

    def expired(self, now: float) -> bool:
        return self.ttl_ms > 0 and (now - self.accepted_at) * 1000 >= self.ttl_ms

    def forwardable(self) -> dict:
        """剥掉治理键后的原始 payload——单条路径的字节级兼容保证。"""
        return {k: v for k, v in self.payload.items() if k not in GOVERNANCE_KEYS}


def parse_quiet_hours(spec: str):
    """"23:00-07:00" → (1380, 420)（分钟）。空 / 非法 → None（该闸不启用）。"""
    spec = (spec or "").strip()
    if not spec or "-" not in spec:
        return None
    try:
        a, b = spec.split("-", 1)
        ah, am = (int(x) for x in a.strip().split(":", 1))
        bh, bm = (int(x) for x in b.strip().split(":", 1))
    except (ValueError, AttributeError):
        return None
    if not (0 <= ah <= 23 and 0 <= bh <= 23 and 0 <= am <= 59 and 0 <= bm <= 59):
        return None
    return ah * 60 + am, bh * 60 + bm


def in_quiet_hours(window, minutes_of_day: int) -> bool:
    if not window:
        return False
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= minutes_of_day < end
    return minutes_of_day >= start or minutes_of_day < end     # 跨零点


def merge_speech(items) -> str:
    """确定性拼接——**不改写、不新增事实**（改写合并是 v2，且必须只重述）。"""
    parts = []
    for it in items:
        s = str(it.payload.get("speech") or "").strip()
        if not s:
            continue
        if s[-1] not in "。！？!?.":
            s += "。"
        parts.append(s)
    if not parts:
        return ""
    return parts[0] + "".join(f"另外，{p}" for p in parts[1:])


def merge_cards(items) -> dict | None:
    """多张卡合成 card_group（HMI 已支持）；嵌套的 card_group 摊平，不套娃。"""
    cards = []
    for it in items:
        c = it.payload.get("card")
        if not c:
            continue
        if isinstance(c, dict) and c.get("type") == "card_group":
            cards.extend(c.get("items") or [])
        else:
            cards.append(c)
    if not cards:
        return None
    return cards[0] if len(cards) == 1 else {"type": "card_group", "items": cards}


class Governor:
    """六道闸 + 合并窗口 + 延后队列。纯 asyncio，无外部依赖（NATS 由 main 注入）。"""

    def __init__(self, publish, *, state_fn=None, emit=None, now_fn=time.time,
                 localtime_fn=time.localtime,
                 merge_window_ms: int = 1500, dedup_window_s: int = 600,
                 max_per_hour: int = 6, high_load_speed: float = 80.0,
                 quiet_hours: str = "", defer_tick_s: float = 5.0):
        self._publish = publish                 # async (payload: dict) -> None
        self._state_fn = state_fn or (lambda: {})
        self._emit = emit                       # async (event: dict) -> None，可空
        self._now = now_fn
        self._localtime = localtime_fn
        self._merge_window_ms = merge_window_ms
        self._dedup_window_s = dedup_window_s
        self._max_per_hour = max_per_hour
        self._high_load_speed = high_load_speed
        self._quiet = parse_quiet_hours(quiet_hours)
        self._defer_tick_s = defer_tick_s

        self._pending: list[Item] = []
        self._deferred: list[Item] = []
        self._dedup: dict[str, float] = {}
        self._delivered_at: deque[float] = deque()   # 频控滚动窗口（按**投递消息数**计）
        self._flush_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None

    # ── 生命周期 ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._tick_task = asyncio.create_task(self._tick_forever())

    async def stop(self) -> None:
        for t in (self._tick_task, self._flush_task):
            if t and not t.done():
                t.cancel()

    # ── 入口 ─────────────────────────────────────────────────────────────
    async def submit(self, payload: dict) -> str:
        """裁决一条主动请求。返回 delivered|merged|deferred|dropped（e2e/测试读）。"""
        item = self._to_item(payload)
        verdict, reason = self._gate(item, first_pass=True)
        if verdict == DROPPED:
            await self._decide(item, DROPPED, reason)
            return DROPPED
        if verdict == DEFERRED:
            self._deferred.append(item)
            self._mark_dedup(item)
            await self._decide(item, DEFERRED, reason)
            return DEFERRED
        self._accept(item)
        return ACCEPTED

    # ── 六道闸 ───────────────────────────────────────────────────────────
    def _gate(self, item: Item, *, first_pass: bool) -> tuple[str, str]:
        now = self._now()
        env = enrich(self._state_fn())

        # 闸1 情境断言复核：UNSAT / UNKNOWN 一律丢——生产方声称的前提无法证实就不替它说。
        if evaluate_all(item.conditions, env) != SAT:
            return DROPPED, "conditions_unmet"

        # 闸2 同类去重（**跨生产方**，这是各自进程内节流做不到的那一半）
        if first_pass and self._dedup_hit(item, now):
            return DROPPED, "dedup"

        if item.priority in _GOVERNABLE:
            # 闸3 免打扰时段（默认空=不启用，§9-3 拍板）
            if in_quiet_hours(self._quiet, self._minutes_of_day(now)):
                return self._suppress(item, "quiet_hours")
            # 闸4 驾驶负荷：读不到车速 → **放行**。"读不到车速"不是"用户在忙"的证据，
            # 用它定罪等于拿缺数据当事实（镜像冷启动最长一个快照周期全空）。
            speed = env.get("speed_kmh")
            if speed is not None:
                try:
                    if float(speed) >= self._high_load_speed:
                        return self._suppress(item, "driving_load")
                except (TypeError, ValueError):
                    pass
            # 闸5 全局频控：超限即丢（延后也没意义——窗口是小时级）
            if self._rate_exceeded(now):
                return DROPPED, "rate_limited"
        return "pass", ""

    @staticmethod
    def _suppress(item: Item, reason: str) -> tuple[str, str]:
        """能攒就攒（ttl>0），不能攒就丢——不做无限期堆积。"""
        return (DEFERRED, reason) if item.ttl_ms > 0 else (DROPPED, reason)

    def _dedup_hit(self, item: Item, now: float) -> bool:
        ts = self._dedup.get(item.dedup_key)
        return ts is not None and (now - ts) < self._dedup_window_s

    def _mark_dedup(self, item: Item) -> None:
        """治理器接手即打标（不是投递时）——去重语义是"同一件事窗口内只说一次"。"""
        self._dedup[item.dedup_key] = self._now()

    def _rate_exceeded(self, now: float) -> bool:
        cutoff = now - 3600
        while self._delivered_at and self._delivered_at[0] < cutoff:
            self._delivered_at.popleft()
        return len(self._delivered_at) >= self._max_per_hour

    def _minutes_of_day(self, now: float) -> int:
        lt = self._localtime(now)
        return lt.tm_hour * 60 + lt.tm_min

    # ── 合并窗口 ─────────────────────────────────────────────────────────
    def _accept(self, item: Item) -> None:
        self._mark_dedup(item)
        self._pending.append(item)
        window_ms = 0 if item.priority == P_CRITICAL else self._merge_window_ms
        if window_ms <= 0:
            # 安全播报不等人；顺带把待发队列一起冲出去，避免它刚说完 1 秒后又单独响一条。
            self._spawn(self._flush())
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = self._spawn(self._flush_after(window_ms / 1000.0))

    @staticmethod
    def _spawn(coro):
        return asyncio.ensure_future(coro)

    async def _flush_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        await self._flush()

    async def _flush(self) -> None:
        items, self._pending = self._pending, []
        if not items:
            return
        items.sort(key=lambda i: i.rank)          # 稳定排序：同档保持到达序
        top = items[0]
        if len(items) == 1:
            out = top.forwardable()
        else:
            out = dict(top.forwardable())
            out["speech"] = merge_speech(items)
            card = merge_cards(items)
            if card:
                out["card"] = card
            else:
                out.pop("card", None)
            out["merged_from"] = [{"agent_id": i.agent_id, "type": i.type} for i in items]
        try:
            await self._publish(out)
        except Exception as e:
            logger.warning("主动消息投递失败：%s", e)
            return                                # 没打扰到用户 → 不计入频控
        self._delivered_at.append(self._now())
        decision = DELIVERED if len(items) == 1 else MERGED
        for it in items:
            await self._decide(it, decision, f"merged_x{len(items)}" if len(items) > 1 else "")

    # ── 延后队列复评 ─────────────────────────────────────────────────────
    async def _tick_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._defer_tick_s)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:               # 复评炸了不能让治理器停摆
                logger.warning("proactive tick 异常：%s", e)

    async def tick(self) -> None:
        """延后项复评：可发了就进待发队列，TTL 到期即丢。顺带清理去重表。"""
        now = self._now()
        self._dedup = {k: ts for k, ts in self._dedup.items()
                       if (now - ts) < self._dedup_window_s}
        if not self._deferred:
            return
        still: list[Item] = []
        for item in self._deferred:
            if item.expired(now):
                await self._decide(item, DROPPED, "ttl_expired")
                continue
            verdict, reason = self._gate(item, first_pass=False)
            if verdict == DROPPED:
                await self._decide(item, DROPPED, reason)
            elif verdict == DEFERRED:
                still.append(item)
            else:
                self._pending.append(item)
                if self._flush_task is None or self._flush_task.done():
                    self._flush_task = self._spawn(
                        self._flush_after(self._merge_window_ms / 1000.0))
        self._deferred = still

    # ── 解析与观测 ───────────────────────────────────────────────────────
    def _to_item(self, payload: dict) -> Item:
        p = dict(payload or {})
        priority = str(p.get("priority") or P_ADVISORY)
        if priority not in _RANK:
            priority = P_ADVISORY                # 不认识的档位按建议类治理，不豁免
        conds = p.get("conditions") or []
        if not isinstance(conds, list):
            conds = []
        dedup = str(p.get("dedup_key") or "").strip()
        if not dedup:                            # 缺省去重键：生产方 + 消息类型
            dedup = f"{p.get('agent_id', '')}|{p.get('type', '')}"
        try:
            ttl = int(p.get("ttl_ms") or 0)
        except (TypeError, ValueError):
            ttl = 0
        return Item(payload=p, priority=priority, conditions=conds, dedup_key=dedup,
                    ttl_ms=max(0, ttl), accepted_at=self._now())

    async def _decide(self, item: Item, decision: str, reason: str) -> None:
        logger.info("proactive %s: %s/%s (%s) %s", decision, item.agent_id,
                    item.type, item.priority, reason)
        if not self._emit:
            return
        try:
            await self._emit({"request_id": item.request_id, "agent_id": item.agent_id,
                              "type": item.type, "priority": item.priority,
                              "decision": decision, "reason": reason,
                              "ts": int(self._now() * 1000)})
        except Exception as e:
            logger.debug("裁决事件发布失败（忽略）：%s", e)

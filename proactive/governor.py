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

from .delivery_store import EXPIRED as LEDGER_EXPIRED, is_durable
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

PERSONAL_DATA_TARGETS = (
    {
        "id": "proactive_process_queue",
        "storage_variants": ("Governor._pending", "Governor._deferred"),
    },
)


@dataclass
class Item:
    payload: dict
    priority: str
    conditions: list
    dedup_key: str
    ttl_ms: int
    accepted_at: float
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # M-C：durable 档在**落库之后**才拿到它；非 durable 档恒空。
    # 它随信封走到 HMI，HMI 按它幂等呈现并回 ACK——`publish 成功 ≠ 用户收到`，
    # 只有那条 ACK 才是通知合同完成的凭据。
    delivery_id: str = ""

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
        """剥掉治理键后的原始 payload——单条路径的字节级兼容保证。

        M-C 例外：durable 档追加 `delivery_id`。它不是治理键（不剥），是**投递凭据**
        ——没有它 HMI 就没法回 ACK，也就无从区分「发出去了」和「用户看见了」。
        非 durable 档不带该键，字节级兼容保证对它们逐字不变。
        """
        out = {k: v for k, v in self.payload.items() if k not in GOVERNANCE_KEYS}
        if self.delivery_id:
            out["delivery_id"] = self.delivery_id
        return out


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
                 quiet_hours: str = "", defer_tick_s: float = 5.0, store=None):
        self._publish = publish                 # async (payload: dict) -> None
        # M-C 投递账本（可空=退回纯内存的旧行为，advisory/ambient 本来就不落库）
        self._store = store
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
        self._dedup: dict[str, tuple[float, str]] = {}
        self._delivered_at: deque[
            tuple[float, tuple[tuple[str, str], ...]]
        ] = deque()   # timestamp + exact-owner/dedup keys per delivered message
        self._flush_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None

    # ── 生命周期 ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        await self.restore()
        self._tick_task = asyncio.create_task(self._tick_forever())

    async def restore(self) -> int:
        """重启后把账上还没送到的消息接回来。返回恢复条数。

        这是 durable 的另一半：只落库不恢复，等于把消息存进了一个没人读的表。
        恢复出来的 item **不重新过闸**——它们当初已经通过了；直接进待发队列，
        由正常的合并窗投出去。TTL 仍然生效（`accepted_at` 用原始创建时刻），
        所以停机久到消息过期的，恢复时就会被 tick 判掉，不会突然播一句陈旧内容。
        """
        if self._store is None:
            return 0
        try:
            await self._store.expire_stale()
            rows = await self._store.undelivered()
        except Exception as e:
            logger.warning("投递账本恢复失败（继续启动）：%s", e)
            return 0
        n = 0
        for row in rows:
            payload = row.get("payload") or {}
            if not isinstance(payload, dict) or not payload:
                continue
            item = self._to_item(payload)
            item.delivery_id = row.get("delivery_id") or ""
            created_ms = row.get("created_at") or 0
            if created_ms:
                item.accepted_at = created_ms / 1000.0   # TTL 按原始时刻算，不重新计时
            self._pending.append(item)
            n += 1
        if n:
            logger.info("投递账本恢复 %d 条未送达消息", n)
            self._flush_task = self._spawn(
                self._flush_after(self._merge_window_ms / 1000.0))
        return n

    async def acknowledge(self, delivery_ids) -> int:
        """HMI 回执：**唯一的通知合同完成条件**。返回销掉的条数。

        接受一个列表是因为合并组会把整组凭据一起带给 HMI，HMI 原样回传。
        凭据随消息走而不是留在治理器内存里——重启后 ACK 仍然对得上账。
        """
        if self._store is None:
            return 0
        ids = [delivery_ids] if isinstance(delivery_ids, str) else list(delivery_ids or [])
        n = 0
        for did in ids:
            try:
                if await self._store.mark_presented(did):
                    n += 1
            except Exception as e:
                logger.warning("回执处理失败 %s：%s", did, e)
        return n

    async def replay_undelivered(self, user_id: str = "") -> int:
        """HMI（重新）连上时补投还没送到的消息。返回补投条数。

        与「重启恢复」共用同一份账——**「什么算没送到」不该有两套判据**。
        走正常 publish 路径，故合并窗、dispatched 标记与观测一并复用。
        """
        if self._store is None:
            return 0
        try:
            await self._store.expire_stale()
            rows = await self._store.undelivered(user_id=user_id)
        except Exception as e:
            logger.warning("补投读取失败：%s", e)
            return 0
        n = 0
        for row in rows:
            payload = row.get("payload") or {}
            if not isinstance(payload, dict) or not payload:
                continue
            item = self._to_item(payload)
            item.delivery_id = row.get("delivery_id") or ""
            self._pending.append(item)
            n += 1
        if n:
            logger.info("HMI 上线，补投 %d 条未送达消息", n)
            await self._flush()
        return n

    async def stop(self) -> None:
        for t in (self._tick_task, self._flush_task):
            if t and not t.done():
                t.cancel()

    # ── E2E exact-owner namespace admin（默认不暴露，由 main 的签名门控接线）──
    @staticmethod
    def _require_owner(user_id: str) -> str:
        owner = str(user_id or "")
        if not owner:
            raise ValueError("owner user_id is required")
        return owner

    def count_owner(self, user_id: str) -> int:
        """Count exact-owner logical messages across every in-memory state."""

        owner = self._require_owner(user_id)
        keys = {
            item.dedup_key
            for item in (*self._pending, *self._deferred)
            if item.payload.get("user_id") == owner
        }
        keys.update(
            key for key, (_timestamp, key_owner) in self._dedup.items()
            if key_owner == owner
        )
        keys.update(
            key
            for _timestamp, owners in self._delivered_at
            for key_owner, key in owners
            if key_owner == owner
        )
        return len(keys)

    def rate_status(self) -> dict[str, int]:
        """Return anonymous global rolling-window usage and its safe cap."""

        self._prune_delivered(self._now())
        return {
            "rate_delivered": len(self._delivered_at),
            "rate_max_per_hour": self._max_per_hour,
        }

    def purge_owner(self, user_id: str) -> dict[str, int]:
        """Remove one exact owner from pending/deferred, preserving all others."""

        owner = self._require_owner(user_id)
        before = self.count_owner(owner)
        self._pending = [
            item for item in self._pending
            if item.payload.get("user_id") != owner
        ]
        self._deferred = [
            item for item in self._deferred
            if item.payload.get("user_id") != owner
        ]
        self._dedup = {
            key: value
            for key, value in self._dedup.items()
            if value[1] != owner
        }
        retained_deliveries = deque()
        for timestamp, owners in self._delivered_at:
            remaining = tuple(
                pair for pair in owners
                if pair[0] != owner
            )
            if remaining:
                retained_deliveries.append((timestamp, remaining))
        self._delivered_at = retained_deliveries
        after = self.count_owner(owner)
        return {"before": before, "deleted": before - after, "after": after}

    # ── 入口 ─────────────────────────────────────────────────────────────
    async def take_ownership(self, payload: dict) -> tuple[object, bool]:
        """接管一条请求：durable 档**先落库**，返回 (item, durable)。

        存在的理由是所有权移交的时机：治理器一旦回 accepted，生产方就不再重发。
        此前 ack 发生在落任何盘之前——ack 与真正投出去之间隔着合并窗（默认 1.5s）
        与最长 ttl 的延后队列，进程在这段里死掉，那条「到点提醒我」就永远没了。
        所以 durable 档必须先有账再认领；落库失败时**如实返回 durable=False**，
        不假装已持久接管。
        """
        item = self._to_item(payload)
        durable = False
        if self._store is not None and is_durable(item.priority):
            did = await self._store.persist(
                item.payload, priority=item.priority,
                ttl_ms=item.ttl_ms, dedup_key=item.dedup_key)
            if did:
                item.delivery_id, durable = did, True
        return item, durable

    async def submit(self, payload: dict) -> str:
        """裁决一条主动请求。返回 delivered|merged|deferred|dropped（e2e/测试读）。"""
        item, _durable = await self.take_ownership(payload)
        return await self.govern(item)

    async def govern(self, item) -> str:
        """对已接管的 item 走六道闸。与 submit 分开，是为了让调用方能在
        「落库完成」与「裁决完成」之间插入 ACK。"""
        verdict, reason = self._gate(item, first_pass=True)
        if verdict == DROPPED:
            await self._settle(item, DROPPED, reason)
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

        # 闸1 情境断言复核：生产方与治理器并行消费同一车况事件，request 可能先于
        # 治理器镜像到达。带 TTL 的首轮请求先短暂延后，复评仍 UNSAT/UNKNOWN 才丢；
        # 无 TTL 的请求仍 fail-closed，绝不把读不到当满足。
        if evaluate_all(item.conditions, env) != SAT:
            if first_pass and item.ttl_ms > 0:
                return DEFERRED, "conditions_pending"
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
            # 闸5 全局频控。**与闸3/4 对称**：声明了 ttl 的就攒着等窗口滚出去，
            # 没声明 ttl 的照旧即丢。原注释说「延后也没意义——窗口是小时级」，
            # 但窗口是**滑动**的：一小时前那条一滚出去就有名额了，而 tick 每几秒
            # 复评一次。一条带 5 分钟 ttl 的建议不该因为「此刻恰好满了」永远消失。
            if self._rate_exceeded(now):
                return self._suppress(item, "rate_limited")
        return "pass", ""

    @staticmethod
    def _suppress(item: Item, reason: str) -> tuple[str, str]:
        """能攒就攒（ttl>0），不能攒就丢——不做无限期堆积。"""
        return (DEFERRED, reason) if item.ttl_ms > 0 else (DROPPED, reason)

    def _dedup_hit(self, item: Item, now: float) -> bool:
        entry = self._dedup.get(item.dedup_key)
        return entry is not None and (now - entry[0]) < self._dedup_window_s

    def _mark_dedup(self, item: Item) -> None:
        """治理器接手即打标（不是投递时）——去重语义是"同一件事窗口内只说一次"。"""
        self._dedup[item.dedup_key] = (
            self._now(),
            str(item.payload.get("user_id") or ""),
        )

    def _prune_delivered(self, now: float) -> None:
        cutoff = now - 3600
        while self._delivered_at and self._delivered_at[0][0] < cutoff:
            self._delivered_at.popleft()

    def _rate_exceeded(self, now: float) -> bool:
        self._prune_delivered(now)
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
        # 合并只在同一乘员内发生：合并组只带 top 的 user_id，跨 owner 合并会把
        # B 的消息记在 A 名下、也只对 A 播报——reminder 侧刻意做的 owner 分组
        # 不该在治理器这一跳被抵消（2026-08-14 EVA 二轮批 A③）。
        # owner 键与频控/投递账本同源（payload.user_id，缺省空串归一组）。
        groups: dict[str, list] = {}
        for it in items:
            groups.setdefault(str(it.payload.get("user_id") or ""), []).append(it)
        for group in groups.values():
            await self._flush_owner_group(group)

    async def _flush_owner_group(self, items: list) -> None:
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
        # 合并组把整组凭据一起带走：HMI 原样回传，一次 ACK 销掉整组。
        # 让凭据随消息走而不是留在治理器内存里，重启后 ACK 仍然对得上账。
        dids = [i.delivery_id for i in items if i.delivery_id]
        if dids:
            out["delivery_ids"] = dids
        try:
            await self._publish(out)
        except Exception as e:
            logger.warning("主动消息投递失败：%s", e)
            # 验收补口：投递失败也要发裁决事件——否则这条消息在 obs 上既非 delivered
            # 也非 dropped，直接从观测面消失，dashboard 查无此案。不计频控照旧
            # （没打扰到用户）。
            # **不销账**：投递失败的 durable 消息要留在账上等 HMI 连上时重投
            # ——这正是「n==0 即丢」那个缺口的另一半。
            for it in items:
                await self._decide(it, DROPPED, "publish_error")
            return
        self._delivered_at.append((
            self._now(),
            tuple(
                (
                    str(item.payload.get("user_id") or ""),
                    item.dedup_key,
                )
                for item in items
            ),
        ))
        if self._store is not None:
            # dispatched ≠ presented：发出去只是发出去了，账要等 HMI 的 ACK 才销。
            await self._store.mark_dispatched([i.delivery_id for i in items])
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
        self._dedup = {
            key: entry for key, entry in self._dedup.items()
            if (now - entry[0]) < self._dedup_window_s
        }
        if not self._deferred:
            return
        still: list[Item] = []
        for item in self._deferred:
            if item.expired(now):
                await self._settle(item, DROPPED, "ttl_expired",
                                   ledger_state=LEDGER_EXPIRED)
                continue
            verdict, reason = self._gate(item, first_pass=False)
            if verdict == DROPPED:
                await self._settle(item, DROPPED, reason)
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

    async def _settle(self, item: Item, decision: str, reason: str,
                      *, ledger_state: str = "") -> None:
        """终态：发裁决事件 + 销账。**账本不能只进不出**——判丢的消息还留在账上，
        HMI 一连上就会被重投一堆已经作废的内容。

        `ledger_state` 只在账本内部区分终态（过期 vs 判丢）；**对外裁决词表不变**
        ——obs 与 dashboard 消费的是 decision，给它加一个新值等于改既有契约。
        """
        await self._decide(item, decision, reason)
        if self._store is not None and item.delivery_id:
            await self._store.mark_final(item.delivery_id, ledger_state or decision,
                                         reason)

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

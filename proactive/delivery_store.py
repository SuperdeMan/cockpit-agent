"""主动投递的持久落点（M-C）。

**存在的理由一句话**：`publish 成功 ≠ 用户收到`。

治理器此前把待发/延后队列全放进程内存，RFC 记的理由是「这些消息的生命周期以秒计，
落库不值当」。那句话对 advisory/ambient 成立，对 `user_contract` **不成立**——
「到点提醒我」是用户显式约定，它的生命周期不是秒，是「直到我看见」。ack 之后到
真正投出去之间有一个合并窗（默认 1.5s）加上最长 ttl 的延后队列，进程在这段里死掉，
消息就没了，而生产方早已收到 accepted、不会重发。

所以只给**用户合同档与安全档**（`user_contract` / `critical`）落库；advisory/ambient
维持内存态——它们本来就可以不说，为它们付持久化的代价不划算。

形态与 `agents/reminder/src/store.py`、`memory/pg_store.py` 一致：单类双后端，
PG 优先、无 PG 内存兜底并打 WARNING（诚实降级，不假装持久）。

**本模块零领域字面量**：不出现任何 agent_id / type / intent 值，只认信封里的
priority 与 owner——与治理器同一条铁律。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

logger = logging.getLogger("proactive.delivery")

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# 投递生命周期。**只有 PRESENTED 是通知合同完成**——网关 write 成功不算，
# HMI 渲染出来才算。SPOKEN 是独立的语音观测，不是更高一级的投递终态
# （用户看见了但没听见，合同已经履行；把它当终态会让静音场景永远「未完成」）。
PENDING, DISPATCHED, PRESENTED = "pending", "dispatched", "presented"
DROPPED, EXPIRED = "dropped", "expired"
_OPEN_STATES = (PENDING, DISPATCHED)

# 需要 durable 保证的档位。其余档位不落库——它们可以不说。
DURABLE_PRIORITIES = ("critical", "user_contract")

# 个人数据清单登记（M-A 隐私清单机制）。**payload 里存着话术与卡片摘要**——
# 那是个人数据，删记忆却留着投递账本与「删了长期记忆但对话原文还在」是同一种假删除。
# 未登记的新 SQL 表会被 `scripts/e2e_contract.py` 的清单守卫直接拦下（本批实测被拦）。
PERSONAL_DATA_TARGETS = (
    {
        "id": "proactive_delivery",
        "storage_variants": ("proactive_delivery", "DeliveryStore._mem"),
        "sql_variants": ("proactive_delivery",),
    },
)


def is_durable(priority: str) -> bool:
    return (priority or "") in DURABLE_PRIORITIES


def _now_ms() -> int:
    return int(time.time() * 1000)


class DeliveryStore:
    """未送达主动消息的账本。PG 优先，无 PG 内存兜底（重启即丢，启动时打 WARNING）。"""

    def __init__(self, dsn: str | None = None):
        self._dsn = os.getenv("POSTGRES_DSN", "") if dsn is None else dsn
        self._pool = None
        self._pg_ok = False
        self._mem: dict[str, dict] = {}

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    async def init(self) -> bool:
        if not self._dsn:
            logger.warning("DeliveryStore：无 POSTGRES_DSN，内存兜底"
                           "——**重启会丢未送达的用户合同消息**")
            return False
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                schema = f.read()
            async with self._pool.acquire() as conn:
                await conn.execute(schema)
            self._pg_ok = True
            logger.info("DeliveryStore：PG 就绪（proactive_delivery）")
        except Exception as e:
            logger.warning("DeliveryStore：PG 不可用（%s），内存兜底"
                           "——**重启会丢未送达的用户合同消息**", e)
            self._pg_ok = False
        return self._pg_ok

    # ── 写 ────────────────────────────────────────────────────────────
    async def persist(self, envelope: dict, *, priority: str, dedup_key: str = "",
                      ttl_ms: int = 0, delivery_id: str = "") -> str:
        """落一条待投递记录，返回 delivery_id；失败返回空串（调用方据此诚实降级）。

        存的是**完整信封**而不是 forwardable 产物：重启恢复要重建 Item，
        priority/conditions/ttl 一个都不能少。
        """
        did = delivery_id or uuid.uuid4().hex
        now = _now_ms()
        # ttl<=0 = 不过期。**用户合同不该被一个默认过期时间悄悄吃掉**——
        # 「到点提醒我」没说「五分钟内没看见就算了」。
        expires = now + ttl_ms if ttl_ms > 0 else 0
        row = {
            "delivery_id": did, "dedup_key": dedup_key or "",
            "user_id": str(envelope.get("user_id") or ""),
            "occupant_id": str(envelope.get("owner_occupant_id")
                               or envelope.get("occupant_id") or "primary"),
            "source": str(envelope.get("agent_id") or ""),
            "priority": priority, "payload": envelope,
            "state": PENDING, "reason": "", "attempts": 0,
            "created_at": now, "expires_at": expires,
            "dispatched_at": 0, "presented_at": 0,
        }
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO proactive_delivery (delivery_id,dedup_key,user_id,"
                        "occupant_id,source,priority,payload,state,reason,attempts,"
                        "created_at,expires_at,dispatched_at,presented_at) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14)",
                        did, row["dedup_key"], row["user_id"], row["occupant_id"],
                        row["source"], priority,
                        json.dumps(envelope, ensure_ascii=False), PENDING, "", 0,
                        now, expires, 0, 0)
                return did
            except Exception as e:
                logger.warning("投递落库失败（降级为不可靠投递）：%s", e)
                return ""
        self._mem[did] = row
        return did

    async def mark_dispatched(self, delivery_ids: list[str]) -> None:
        ids = [d for d in (delivery_ids or []) if d]
        if not ids:
            return
        now = _now_ms()
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE proactive_delivery SET state=$1, dispatched_at=$2, "
                        "attempts=attempts+1 WHERE delivery_id=ANY($3) AND state=$4",
                        DISPATCHED, now, ids, PENDING)
            except Exception as e:
                logger.warning("标记 dispatched 失败：%s", e)
            return
        for d in ids:
            r = self._mem.get(d)
            if r and r["state"] == PENDING:
                r.update(state=DISPATCHED, dispatched_at=now,
                         attempts=r["attempts"] + 1)

    async def mark_presented(self, delivery_id: str) -> bool:
        """HMI 确认已呈现——**唯一的通知合同完成条件**。迟到/重复 ACK 幂等成功。"""
        if not delivery_id:
            return False
        now = _now_ms()
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    tag = await conn.execute(
                        "UPDATE proactive_delivery SET state=$1, presented_at=$2 "
                        "WHERE delivery_id=$3 AND state=ANY($4)",
                        PRESENTED, now, delivery_id, list(_OPEN_STATES))
                if str(tag).endswith("1"):
                    return True
                # 已经是 presented 的重复 ACK 也算成功（幂等），未知 id 才是 False
                async with self._pool.acquire() as conn:
                    st = await conn.fetchval(
                        "SELECT state FROM proactive_delivery WHERE delivery_id=$1",
                        delivery_id)
                return st == PRESENTED
            except Exception as e:
                logger.warning("标记 presented 失败：%s", e)
                return False
        r = self._mem.get(delivery_id)
        if not r:
            return False
        if r["state"] in _OPEN_STATES:
            r.update(state=PRESENTED, presented_at=now)
        return r["state"] == PRESENTED

    async def mark_final(self, delivery_id: str, state: str, reason: str = "") -> None:
        """治理器判丢/过期时销账——账本不能只进不出。"""
        if not delivery_id:
            return
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE proactive_delivery SET state=$1, reason=$2 "
                        "WHERE delivery_id=$3 AND state=ANY($4)",
                        state, reason or "", delivery_id, list(_OPEN_STATES))
            except Exception as e:
                logger.warning("销账失败：%s", e)
            return
        r = self._mem.get(delivery_id)
        if r and r["state"] in _OPEN_STATES:
            r.update(state=state, reason=reason or "")

    # ── 读 ────────────────────────────────────────────────────────────
    async def undelivered(self, *, user_id: str = "", limit: int = 20) -> list[dict]:
        """未送达且未过期的消息，旧的在前。

        HMI 连上时按它重投，治理器重启时按它恢复——**同一份账**，
        两个场景不该有两套「什么算没送到」的判据。
        """
        now = _now_ms()
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT * FROM proactive_delivery WHERE state=ANY($1) "
                        "AND (expires_at=0 OR expires_at>$2) "
                        "AND ($3='' OR user_id=$3) "
                        "ORDER BY created_at ASC LIMIT $4",
                        list(_OPEN_STATES), now, user_id or "", limit)
                return [self._row(r) for r in rows]
            except Exception as e:
                logger.warning("读未送达失败：%s", e)
                return []
        out = [r for r in self._mem.values()
               if r["state"] in _OPEN_STATES
               and (r["expires_at"] == 0 or r["expires_at"] > now)
               and (not user_id or r["user_id"] == user_id)]
        out.sort(key=lambda r: r["created_at"])
        return [dict(r) for r in out[:limit]]

    async def expire_stale(self) -> int:
        """把过了 ttl 还没送到的标记过期——**不重播陈旧内容**。"""
        now = _now_ms()
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    tag = await conn.execute(
                        "UPDATE proactive_delivery SET state=$1, reason=$2 "
                        "WHERE state=ANY($3) AND expires_at>0 AND expires_at<=$4",
                        EXPIRED, "ttl_expired", list(_OPEN_STATES), now)
                try:
                    return int(str(tag).split()[-1])
                except (ValueError, IndexError):
                    return 0
            except Exception as e:
                logger.warning("过期清理失败：%s", e)
                return 0
        n = 0
        for r in self._mem.values():
            if (r["state"] in _OPEN_STATES and r["expires_at"] > 0
                    and r["expires_at"] <= now):
                r.update(state=EXPIRED, reason="ttl_expired")
                n += 1
        return n

    async def get(self, delivery_id: str) -> dict | None:
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM proactive_delivery WHERE delivery_id=$1",
                        delivery_id)
                return self._row(row) if row else None
            except Exception as e:
                logger.warning("读投递记录失败：%s", e)
                return None
        r = self._mem.get(delivery_id)
        return dict(r) if r else None

    @staticmethod
    def _row(row) -> dict:
        d = dict(row)
        payload = d.get("payload")
        if isinstance(payload, str):
            try:
                d["payload"] = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                d["payload"] = {}
        return d

    # ── 合规：owner 级删除（M-B privacy 口径的延伸）─────────────────────
    async def forget_owner(self, user_id: str, occupant_id: str = "") -> int:
        """删除该 OwnerKey 的投递记录。occupant 空=该 user 全部乘员。

        payload 里存着话术与卡片摘要，那是个人数据；删记忆却留着投递账本
        与「删了长期记忆但对话原文还在」是同一种假删除。
        """
        if not user_id:
            return 0
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    tag = await conn.execute(
                        "DELETE FROM proactive_delivery WHERE user_id=$1 "
                        "AND ($2='' OR occupant_id=$2)", user_id, occupant_id or "")
                try:
                    return int(str(tag).split()[-1])
                except (ValueError, IndexError):
                    return 0
            except Exception as e:
                logger.warning("投递账本删除失败：%s", e)
                return 0
        gone = [k for k, r in self._mem.items()
                if r["user_id"] == user_id
                and (not occupant_id or r["occupant_id"] == occupant_id)]
        for k in gone:
            self._mem.pop(k, None)
        return len(gone)

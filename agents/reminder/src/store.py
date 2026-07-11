"""提醒持久层：PG（asyncpg，同 PG 实例独立表）优先，无 PG 内存兜底（诚实降级）。

仿 memory/pg_store.py 的单类双后端形态；claim_due 用 UPDATE…RETURNING 原子领取，
重复触发/未来多实例安全。内存分支重启丢失——init 时打 WARNING。
"""
from __future__ import annotations
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo

from .timeparse import business_tz, format_display, recur_label

logger = logging.getLogger("agent.reminder.store")

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
PENDING, FIRED, DONE, CANCELLED = "pending", "fired", "done", "cancelled"
ACTIVE = (PENDING, FIRED)     # 默认过滤：用户可见/可操作态


@dataclass
class Reminder:
    user_id: str
    title: str
    kind: str = "time"                 # time | todo
    fire_at: int = 0                   # epoch 秒（UTC）；todo 恒 0
    status: str = PENDING
    id: str = ""
    vehicle_id: str = ""
    created_at: int = 0
    fired_at: int = 0
    source: str = "user"
    recur: str = ""
    extra: dict = field(default_factory=dict)

    def to_card_item(self, *, now: datetime | None = None,
                     tz: tzinfo | None = None) -> dict:
        """ReminderItem 卡片契约（设计 §9.1）。time_display 后端本地化，HMI 不做时区运算。"""
        item = {"id": self.id, "title": self.title, "kind": self.kind,
                "status": self.status,
                "time_display": format_display(self.fire_at, now=now, tz=tz)
                if self.fire_at else ""}
        if self.fire_at:
            item["fire_at_ms"] = self.fire_at * 1000
        if self.recur:
            item["recur_label"] = recur_label(self.recur)   # P1a：重复标识（每天/工作日/每周X）
        return item


class ReminderStore:
    def __init__(self, dsn: str | None = None):
        self._dsn = os.getenv("POSTGRES_DSN", "") if dsn is None else dsn
        self._pool = None
        self._pg_ok = False
        self._mem: dict[str, Reminder] = {}   # id -> Reminder（PG 不可用兜底）

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    async def init(self) -> bool:
        if not self._dsn:
            logger.warning("ReminderStore: 无 POSTGRES_DSN，内存态兜底（重启丢失提醒）")
            return False
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                schema = f.read()
            async with self._pool.acquire() as conn:
                await conn.execute(schema)
            self._pg_ok = True
            logger.info("ReminderStore: PG 就绪（reminder_item）")
        except Exception as e:
            logger.warning("ReminderStore: PG 不可用（%s），内存态兜底（重启丢失提醒）", e)
            self._pg_ok = False
        return self._pg_ok

    # ── 写入 ──
    async def add(self, r: Reminder) -> Reminder:
        r.id = r.id or uuid.uuid4().hex
        r.created_at = r.created_at or int(time.time())
        if self._pg_ok:
            import json
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO reminder_item (id,user_id,vehicle_id,title,kind,fire_at,"
                    "status,created_at,fired_at,source,recur,extra) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)",
                    r.id, r.user_id, r.vehicle_id, r.title, r.kind, r.fire_at,
                    r.status, r.created_at, r.fired_at, r.source, r.recur,
                    json.dumps(r.extra, ensure_ascii=False))
        else:
            self._mem[r.id] = r
        return r

    # ── 读取 ──
    async def get(self, user_id: str, rid: str) -> Reminder | None:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM reminder_item WHERE id=$1 AND user_id=$2", rid, user_id)
            return self._row(row) if row else None
        r = self._mem.get(rid)
        return r if r and r.user_id == user_id else None

    async def list_split(self, user_id: str, *, from_ts: int = 0, to_ts: int = 0,
                         statuses: tuple = ACTIVE,
                         limit: int = 50) -> tuple[list[Reminder], list[Reminder]]:
        """(定时项按 fire_at 升序, 待办按 created_at 升序)。to_ts=0 表示无上界。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                trs = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND kind='time' "
                    "AND status=ANY($2) AND fire_at>=$3 AND ($4=0 OR fire_at<$4) "
                    "ORDER BY fire_at ASC LIMIT $5",
                    user_id, list(statuses), from_ts, to_ts, limit)
                tds = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND kind='todo' "
                    "AND status=ANY($2) ORDER BY created_at ASC LIMIT $3",
                    user_id, list(statuses), limit)
            return [self._row(x) for x in trs], [self._row(x) for x in tds]
        rs = [r for r in self._mem.values() if r.user_id == user_id and r.status in statuses]
        times = sorted((r for r in rs if r.kind == "time"
                        and r.fire_at >= from_ts and (to_ts == 0 or r.fire_at < to_ts)),
                       key=lambda r: r.fire_at)[:limit]
        todos = sorted((r for r in rs if r.kind == "todo"),
                       key=lambda r: r.created_at)[:limit]
        return times, todos

    async def find_by_title(self, user_id: str, q: str,
                            statuses: tuple = ACTIVE) -> list[Reminder]:
        q = (q or "").strip()
        if not q:
            return []
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND status=ANY($2) "
                    "AND title LIKE $3 ORDER BY fire_at ASC", user_id, list(statuses),
                    f"%{q}%")
            return [self._row(x) for x in rows]
        return sorted((r for r in self._mem.values() if r.user_id == user_id
                       and r.status in statuses and q in r.title),
                      key=lambda r: r.fire_at)

    # ── 状态转移 ──
    async def set_status(self, user_id: str, rid: str, status: str) -> bool:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status=$1 WHERE id=$2 AND user_id=$3",
                    status, rid, user_id)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id:
            return False
        r.status = status
        return True

    async def update_fire_at(self, user_id: str, rid: str, fire_at: int) -> bool:
        """改期 / snooze：新时间并回到 pending 等下一次触发（fired 尸体由此收编）。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET fire_at=$1, status='pending' "
                    "WHERE id=$2 AND user_id=$3 AND status=ANY($4)",
                    fire_at, rid, user_id, list(ACTIVE))
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.status not in ACTIVE:
            return False
        r.fire_at, r.status = fire_at, PENDING
        return True

    async def roll_recurring(self, user_id: str, rid: str, next_fire: int) -> bool:
        """重复系列触发后滚动到下一次（fired→pending；fired_at 保留为上次触发时刻）。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET fire_at=$1, status='pending' "
                    "WHERE id=$2 AND user_id=$3 AND status='fired'",
                    next_fire, rid, user_id)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.status != FIRED:
            return False
        r.fire_at, r.status = next_fire, PENDING
        return True

    async def cancel_all(self, user_id: str) -> int:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status='cancelled' "
                    "WHERE user_id=$1 AND status=ANY($2)", user_id, list(ACTIVE))
            try:
                return int(tag.split()[-1])
            except Exception:
                return 0
        n = 0
        for r in self._mem.values():
            if r.user_id == user_id and r.status in ACTIVE:
                r.status = CANCELLED
                n += 1
        return n

    async def claim_due(self, now_ts: int) -> list[Reminder]:
        """原子领取到期项（pending→fired，跨用户）。二次调用不重复返回。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE reminder_item SET status='fired', fired_at=$1 "
                    "WHERE status='pending' AND kind='time' AND fire_at>0 "
                    "AND fire_at<=$1 RETURNING *", now_ts)
            return [self._row(x) for x in rows]
        due = []
        for r in self._mem.values():
            if r.status == PENDING and r.kind == "time" and 0 < r.fire_at <= now_ts:
                r.status, r.fired_at = FIRED, now_ts
                due.append(r)
        return sorted(due, key=lambda r: r.fire_at)

    @staticmethod
    def _row(row) -> Reminder:
        import json
        extra = row["extra"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        return Reminder(id=row["id"], user_id=row["user_id"], vehicle_id=row["vehicle_id"],
                        title=row["title"], kind=row["kind"], fire_at=row["fire_at"],
                        status=row["status"], created_at=row["created_at"],
                        fired_at=row["fired_at"], source=row["source"],
                        recur=row["recur"], extra=extra or {})

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


class InvalidReminder(ValueError):
    """写入闸拒绝：这条提醒**存进去也不会触发**（Q11/N2）。

    **抛而不是静默丢**——调用方必须知道自己刚才没建成，才谈得上诚实追问。
    静默丢弃会造出「系统说建好了、库里没有」这种更难查的形态。
    """


ACTIVE = (PENDING, FIRED)     # 默认过滤：用户可见/可操作态
PERSONAL_DATA_TARGETS = (
    {
        "id": "reminder_item",
        "storage_variants": ("reminder_item", "ReminderStore._mem"),
        "sql_variants": ("reminder_item",),
    },
    {
        # Reminder owns these key families even though Context writes them
        # through the shared profile KV.  Bind the inventory to the SDK's
        # authoritative literals so three metadata copies cannot self-certify.
        "id": "reminder_shared_state",
        "storage_variants": (
            "reminders_active",
            "reminder_pending",
        ),
        "variant_constants": (
            ("agents/_sdk/shared_state.py", "REMINDERS_ACTIVE"),
            ("agents/_sdk/shared_state.py", "REMINDER_PENDING"),
        ),
    },
)
# kind 第三态（M3 P1 位置提醒）：地点数据存 `extra`（place/lat/lon/radius_m/trigger_on）——
# 它只在按 kind 选出条目后被读，从不参与过滤/排序，JSONB 是正确的家，**不加新列**
# （M2「字段级对照后否掉建表」的同一条判据）。
LOCATION = "location"


PRIMARY = "primary"


def owner_of(user_id: str, occupant_id: str = "") -> tuple[str, str]:
    """OwnerKey=(user_id, occupant_id)。空 occupant 规范化 primary，**不表示共享**。"""
    return user_id, (occupant_id or "").strip() or PRIMARY


def group_by_owner(reminders) -> dict[tuple[str, str], list]:
    """按 OwnerKey 分组，保持组内原有顺序。

    全局 due/location 扫描可以跨 owner 原子领取（围栏与时钟由车况驱动，与会话无关），
    但**消费必须先分组**：一条 speech/card 只能属于一个人，`items[0].user_id`
    不能代表混合 owner 集合。
    """
    out: dict[tuple[str, str], list] = {}
    for r in reminders or []:
        out.setdefault(owner_of(r.user_id, r.occupant_id), []).append(r)
    return out


@dataclass
class Reminder:
    user_id: str
    title: str
    # M-B：提醒的 owner 是 (user_id, occupant_id)。此前全域零 occupant——两位乘员的
    # 提醒混在一张表、触达不区分人。vehicle_id 只是环境，不参与 owner 判定。
    occupant_id: str = PRIMARY
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
        if self.kind == LOCATION:          # 位置提醒用地点占 time_display 的位置（HMI 零改动）
            place = str(self.extra.get("place") or "")
            verb = "离开" if self.extra.get("trigger_on") == "leave" else "到"
            item["time_display"] = f"{verb}{place}时" if place else ""
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
    @staticmethod
    def _prepare_new(r: Reminder) -> Reminder:
        r.id = r.id or uuid.uuid4().hex
        r.created_at = r.created_at or int(time.time())
        r.occupant_id = (r.occupant_id or "").strip() or PRIMARY
        # Q11/N2 写入闸：**定时提醒的 `fire_at` 必须是一个未来时刻**。
        # 库里实测躺着三条 `fire_at=0`（1970-01-01）的 **pending** 提醒——
        # 时间解析失败了，创建仍然成功。它们永远不会触发，而按 fire_at 升序
        # **永远排在「进行中的任务」最前面**，于是用户每次查任务都先看到
        # 「妈妈住杭州」「停车位在B2」（I-056 逐字就是这三条）。
        # > **存储层对「什么是一条有效提醒」零校验**，是这个缺陷能落库的最后一环：
        # > 上游解析失败与「非任务陈述被建成提醒」两个缺陷叠加后，**仍然写进了库**。
        # ⚠ 只管 `kind=time`：待办（todo）与位置提醒本来就没有时刻。
        if r.kind == "time" and not (r.fire_at and r.fire_at > 0):
            raise InvalidReminder(
                f"定时提醒的 fire_at 必须是有效时刻（拿到的是 {r.fire_at!r}）"
                f"——解析失败就该诚实追问，不是存一条永远不会触发的提醒")
        return r

    async def add(self, r: Reminder) -> Reminder:
        r = self._prepare_new(r)
        if self._pg_ok:
            import json
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO reminder_item (id,user_id,occupant_id,vehicle_id,title,"
                    "kind,fire_at,status,created_at,fired_at,source,recur,extra) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)",
                    r.id, r.user_id, r.occupant_id or PRIMARY, r.vehicle_id, r.title,
                    r.kind, r.fire_at, r.status, r.created_at, r.fired_at, r.source,
                    r.recur, json.dumps(r.extra, ensure_ascii=False))
        else:
            self._mem[r.id] = r
        return r

    async def add_many(self, reminders: list[Reminder]) -> list[Reminder]:
        """同一用户句子产生的多条提醒整组写入；任一无效时一条也不落。"""
        prepared = [self._prepare_new(r) for r in reminders]
        if not prepared:
            return []
        if self._pg_ok:
            import json
            query = (
                "INSERT INTO reminder_item (id,user_id,occupant_id,vehicle_id,title,"
                "kind,fire_at,status,created_at,fired_at,source,recur,extra) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)"
            )
            rows = [(
                r.id, r.user_id, r.occupant_id or PRIMARY, r.vehicle_id, r.title,
                r.kind, r.fire_at, r.status, r.created_at, r.fired_at, r.source,
                r.recur, json.dumps(r.extra, ensure_ascii=False),
            ) for r in prepared]
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(query, rows)
        else:
            # 所有校验都已在上面完成；此处才一次性修改内存态，避免半组落地。
            self._mem.update({r.id: r for r in prepared})
        return prepared

    # ── 读取 ──
    async def get(self, user_id: str, rid: str, *,
                  occupant_id: str = "", statuses: tuple = ACTIVE) -> Reminder | None:
        """按 id 取一条。**默认只给 ACTIVE**（C10-A，2026-08-28）。

        序数指代经 `REMINDERS_ACTIVE` 缓存拿到 id 再来这里回读，而那份缓存
        可能落后于库（上一轮刚被取消、或别的会话改过）。此前不过滤 status，
        于是「取消第一条」能选中一条**已经取消掉的**条目并再报一次它的标题。
        要读终态条目（审计/回读刚落库的那条）显式传 `statuses`。
        """
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM reminder_item WHERE id=$1 AND user_id=$2 "
                    "AND occupant_id=$3 AND status=ANY($4)",
                    rid, user_id, occ, list(statuses))
            return self._row(row) if row else None
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.occupant_id != occ:
            return None
        return r if r.status in statuses else None

    async def list_split(self, user_id: str, *, from_ts: int = 0, to_ts: int = 0,
                         statuses: tuple = ACTIVE, occupant_id: str = "",
                         limit: int = 50) -> tuple[list[Reminder], list[Reminder]]:
        """(定时项按 fire_at 升序, 待办按 created_at 升序)。to_ts=0 表示无上界。

        按 OwnerKey 过滤：列表序号是「第 N 个」的语义基础，混进另一位乘员的提醒
        会让序号指向别人的条目。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                trs = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND occupant_id=$6 "
                    "AND kind='time' "
                    "AND status=ANY($2) AND fire_at>=$3 AND ($4=0 OR fire_at<$4) "
                    "ORDER BY fire_at ASC LIMIT $5",
                    user_id, list(statuses), from_ts, to_ts, limit, occ)
                tds = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND occupant_id=$4 "
                    "AND kind='todo' "
                    "AND status=ANY($2) ORDER BY created_at ASC LIMIT $3",
                    user_id, list(statuses), limit, occ)
            return [self._row(x) for x in trs], [self._row(x) for x in tds]
        rs = [r for r in self._mem.values() if r.user_id == user_id
              and r.occupant_id == occ and r.status in statuses]
        times = sorted((r for r in rs if r.kind == "time"
                        and r.fire_at >= from_ts and (to_ts == 0 or r.fire_at < to_ts)),
                       key=lambda r: r.fire_at)[:limit]
        todos = sorted((r for r in rs if r.kind == "todo"),
                       key=lambda r: r.created_at)[:limit]
        return times, todos

    async def count_time(self, user_id: str, *, from_ts: int = 0, to_ts: int = 0,
                         statuses: tuple = ACTIVE, occupant_id: str = "") -> int:
        """精确统计定时项；与 ``list_split`` 同 OwnerKey/边界但不受 LIMIT。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                value = await conn.fetchval(
                    "SELECT COUNT(*) FROM reminder_item "
                    "WHERE user_id=$1 AND occupant_id=$5 AND kind='time' "
                    "AND status=ANY($2) "
                    "AND COALESCE(fire_at, 0)>=$3 "
                    "AND ($4=0 OR COALESCE(fire_at, 0)<$4)",
                    user_id, list(statuses), from_ts, to_ts, occ)
            return int(value or 0)
        return sum(
            1 for r in self._mem.values()
            if r.user_id == user_id and r.occupant_id == occ
            and r.kind == "time" and r.status in statuses
            and r.fire_at >= from_ts and (to_ts == 0 or r.fire_at < to_ts)
        )

    async def count_todo(self, user_id: str, *, statuses: tuple = ACTIVE,
                         occupant_id: str = "") -> int:
        """精确统计待办；与 ``list_split`` 同 OwnerKey 但不受 LIMIT。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                value = await conn.fetchval(
                    "SELECT COUNT(*) FROM reminder_item "
                    "WHERE user_id=$1 AND occupant_id=$3 AND kind='todo' "
                    "AND status=ANY($2)",
                    user_id, list(statuses), occ)
            return int(value or 0)
        return sum(
            1 for r in self._mem.values()
            if r.user_id == user_id and r.occupant_id == occ
            and r.kind == "todo" and r.status in statuses
        )

    async def find_by_title(self, user_id: str, q: str,
                            statuses: tuple = ACTIVE,
                            occupant_id: str = "") -> list[Reminder]:
        q = (q or "").strip()
        if not q:
            return []
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND occupant_id=$4 "
                    "AND status=ANY($2) "
                    "AND title LIKE $3 ORDER BY fire_at ASC", user_id, list(statuses),
                    f"%{q}%", occ)
            return [self._row(x) for x in rows]
        return sorted((r for r in self._mem.values() if r.user_id == user_id
                       and r.occupant_id == occ
                       and r.status in statuses and q in r.title),
                      key=lambda r: r.fire_at)

    # ── 状态转移 ──
    async def set_status(self, user_id: str, rid: str, status: str, *,
                         occupant_id: str = "") -> bool:
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status=$1 WHERE id=$2 AND user_id=$3 "
                    "AND occupant_id=$4", status, rid, user_id, occ)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.occupant_id != occ:
            return False
        r.status = status
        return True

    async def update_fire_at(self, user_id: str, rid: str, fire_at: int, *,
                             occupant_id: str = "") -> bool:
        """改期 / snooze：新时间并回到 pending 等下一次触发（fired 尸体由此收编）。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET fire_at=$1, status='pending' "
                    "WHERE id=$2 AND user_id=$3 AND status=ANY($4) AND occupant_id=$5",
                    fire_at, rid, user_id, list(ACTIVE), occ)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.occupant_id != occ or r.status not in ACTIVE:
            return False
        r.fire_at, r.status = fire_at, PENDING
        return True

    async def roll_recurring(self, user_id: str, rid: str, next_fire: int, *,
                             occupant_id: str = "") -> bool:
        """重复系列触发后滚动到下一次（fired→pending；fired_at 保留为上次触发时刻）。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET fire_at=$1, status='pending' "
                    "WHERE id=$2 AND user_id=$3 AND status='fired' AND occupant_id=$4",
                    next_fire, rid, user_id, occ)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id or r.occupant_id != occ or r.status != FIRED:
            return False
        r.fire_at, r.status = next_fire, PENDING
        return True

    async def cancel_all(self, user_id: str, *, occupant_id: str = "") -> int:
        """「全部取消」只作用于当前 OwnerKey——一位乘员说「都取消吧」不该清掉另一位的提醒。"""
        _u, occ = owner_of(user_id, occupant_id)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status='cancelled' "
                    "WHERE user_id=$1 AND status=ANY($2) AND occupant_id=$3",
                    user_id, list(ACTIVE), occ)
            try:
                return int(tag.split()[-1])
            except Exception:
                return 0
        n = 0
        for r in self._mem.values():
            if r.user_id == user_id and r.occupant_id == occ and r.status in ACTIVE:
                r.status = CANCELLED
                n += 1
        return n

    async def list_location_pending(self, limit: int = 50) -> list[Reminder]:
        """待触发的位置提醒（M3 P1）。跨用户——围栏判定由车况驱动，与会话无关。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE kind=$1 AND status=$2 "
                    "ORDER BY created_at ASC LIMIT $3", LOCATION, PENDING, limit)
            return [self._row(x) for x in rows]
        return sorted((r for r in self._mem.values()
                       if r.kind == LOCATION and r.status == PENDING),
                      key=lambda r: r.created_at)[:limit]

    async def claim_location(self, ids: list[str], now_ts: int) -> list[Reminder]:
        """原子领取到地条目（pending→fired）。同 claim_due：重复判定不重复触达。"""
        ids = [i for i in (ids or []) if i]
        if not ids:
            return []
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE reminder_item SET status='fired', fired_at=$1 "
                    "WHERE id=ANY($2) AND status='pending' AND kind=$3 RETURNING *",
                    now_ts, ids, LOCATION)
            return [self._row(x) for x in rows]
        out = []
        for rid in ids:
            r = self._mem.get(rid)
            if r and r.status == PENDING and r.kind == LOCATION:
                r.status, r.fired_at = FIRED, now_ts
                out.append(r)
        return out

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
        return Reminder(id=row["id"], user_id=row["user_id"],
                        occupant_id=(row["occupant_id"] if "occupant_id" in row.keys()
                                     else PRIMARY) or PRIMARY,
                        vehicle_id=row["vehicle_id"],
                        title=row["title"], kind=row["kind"], fire_at=row["fire_at"],
                        status=row["status"], created_at=row["created_at"],
                        fired_at=row["fired_at"], source=row["source"],
                        recur=row["recur"], extra=extra or {})

"""场景持久层：PG（asyncpg，同 PG 实例独立表）优先，无 PG 内存兜底（诚实降级）。

形态照抄 `agents/reminder/src/store.py`（单类双后端）。内存分支重启丢失——init 打 WARNING。

**字段对齐纪律（v2.1 修正①）**：`Scene` 的字段名 = DSL 顶层键 = PG 列名，三处一一同名，
`_row`/参数列表是无脑映射，**不做改名翻译**——防 DSL/存储两侧漂移。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("agent.scene.store")

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")

ENABLED, DISABLED = "enabled", "disabled"
USER, BUILTIN, DERIVED = "user", "builtin", "derived"


@dataclass
class Scene:
    user_id: str
    name: str
    id: str = ""
    aliases: list = field(default_factory=list)
    description: str = ""
    goal: str = ""
    source: str = USER
    status: str = ENABLED
    guards: list = field(default_factory=list)      # v2（P2 起用）：激活前置检查
    actions: list = field(default_factory=list)     # 有序动作（DSL v2 条目）
    triggers: list = field(default_factory=list)    # v2（P3 起用）：触发器
    created_at: int = 0
    updated_at: int = 0
    use_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_card_item(self) -> dict:
        """scene_list 卡片条目（前端只认这几个键）。"""
        return {"id": self.id, "name": self.name, "description": self.description,
                "action_count": len(self.actions), "use_count": self.use_count}


def _jsonb(v) -> str:
    return json.dumps(v or [], ensure_ascii=False)


def _unjson(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v or []


class SceneStore:
    def __init__(self, dsn: str | None = None):
        self._dsn = os.getenv("POSTGRES_DSN", "") if dsn is None else dsn
        self._pool = None
        self._pg_ok = False
        self._mem: dict[str, Scene] = {}          # id -> Scene（PG 不可用兜底）

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    async def init(self) -> bool:
        if not self._dsn:
            logger.warning("SceneStore: 无 POSTGRES_DSN，内存态兜底（重启丢失用户场景）")
            return False
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                schema = f.read()
            async with self._pool.acquire() as conn:
                await conn.execute(schema)
            self._pg_ok = True
            logger.info("SceneStore: PG 就绪（scene_item）")
        except Exception as e:
            logger.warning("SceneStore: PG 不可用（%s），内存态兜底（重启丢失用户场景）", e)
            self._pg_ok = False
        return self._pg_ok

    # ── 写 ──
    async def save(self, s: Scene) -> Scene:
        """新建或整体覆盖（同 (user_id, name) 视为同一场景 → 覆盖，用于「改一下钓鱼模式」）。"""
        now = int(time.time())
        existing = await self.get_by_name(s.user_id, s.name)
        if existing:
            s.id = existing.id
            s.created_at = existing.created_at or now
            s.use_count = existing.use_count
        s.id = s.id or f"usr-{uuid.uuid4().hex[:8]}"
        s.created_at = s.created_at or now
        s.updated_at = now
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO scene_item (id,user_id,name,aliases,description,goal,source,"
                    "status,guards,actions,triggers,created_at,updated_at,use_count) "
                    "VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,"
                    "$12,$13,$14) "
                    # source 必须一起更新：用户在"被隐藏的预置场景"同名位上新建自己的场景时，
                    # 旧行 source=builtin 会残留，导致新场景既不算"我的"、又把预置遮蔽掉——
                    # 场景凭空消失（2026-07-14 真栈 e2e 实测命中）。
                    "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, aliases=EXCLUDED.aliases,"
                    " description=EXCLUDED.description, goal=EXCLUDED.goal,"
                    " source=EXCLUDED.source, status=EXCLUDED.status,"
                    " guards=EXCLUDED.guards, actions=EXCLUDED.actions,"
                    " triggers=EXCLUDED.triggers, updated_at=EXCLUDED.updated_at",
                    s.id, s.user_id, s.name, _jsonb(s.aliases), s.description, s.goal,
                    s.source, s.status, _jsonb(s.guards), _jsonb(s.actions),
                    _jsonb(s.triggers), s.created_at, s.updated_at, s.use_count)
        else:
            self._mem[s.id] = s
        return s

    async def set_status(self, user_id: str, sid: str, status: str) -> bool:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE scene_item SET status=$1, updated_at=$2 WHERE id=$3 AND user_id=$4",
                    status, int(time.time()), sid, user_id)
            return tag.endswith("1")
        s = self._mem.get(sid)
        if not s or s.user_id != user_id:
            return False
        s.status, s.updated_at = status, int(time.time())
        return True

    async def delete(self, user_id: str, sid: str) -> bool:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "DELETE FROM scene_item WHERE id=$1 AND user_id=$2", sid, user_id)
            return tag.endswith("1")
        s = self._mem.get(sid)
        if not s or s.user_id != user_id:
            return False
        del self._mem[sid]
        return True

    async def bump_use(self, user_id: str, sid: str) -> None:
        """激活计数（P4 习惯建议排序用）。失败静默——不该因计数写失败挡住激活。"""
        try:
            if self._pg_ok:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE scene_item SET use_count=use_count+1 "
                        "WHERE id=$1 AND user_id=$2", sid, user_id)
            else:
                s = self._mem.get(sid)
                if s and s.user_id == user_id:
                    s.use_count += 1
        except Exception as e:
            logger.debug("scene: use_count 递增失败（忽略）：%s", e)

    # ── 读 ──
    async def get(self, user_id: str, sid: str) -> Scene | None:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM scene_item WHERE id=$1 AND user_id=$2", sid, user_id)
            return self._row(row) if row else None
        s = self._mem.get(sid)
        return s if s and s.user_id == user_id else None

    async def get_by_name(self, user_id: str, name: str) -> Scene | None:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM scene_item WHERE user_id=$1 AND name=$2", user_id, name)
            return self._row(row) if row else None
        for s in self._mem.values():
            if s.user_id == user_id and s.name == name:
                return s
        return None

    async def list(self, user_id: str, statuses: tuple = (ENABLED,)) -> list[Scene]:
        """按 use_count 降序（常用在前），同频按创建序。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM scene_item WHERE user_id=$1 AND status=ANY($2) "
                    "ORDER BY use_count DESC, created_at ASC", user_id, list(statuses))
            return [self._row(r) for r in rows]
        return sorted((s for s in self._mem.values()
                       if s.user_id == user_id and s.status in statuses),
                      key=lambda s: (-s.use_count, s.created_at))

    @staticmethod
    def _row(row) -> Scene:
        return Scene(
            id=row["id"], user_id=row["user_id"], name=row["name"],
            aliases=_unjson(row["aliases"]), description=row["description"],
            goal=row["goal"], source=row["source"], status=row["status"],
            guards=_unjson(row["guards"]), actions=_unjson(row["actions"]),
            triggers=_unjson(row["triggers"]), created_at=row["created_at"],
            updated_at=row["updated_at"], use_count=row["use_count"])

"""Task Ledger：跨轮持久任务账本（M2 P0）。

一句话定位：**「谁在替用户干活、干到哪了、还让不让它干」的唯一权威记录。**

**为什么存在**：deep-research 的异步深调研今天是进程内 `asyncio.create_task`——重启即丢、
无法取消、无预算、用户问「查得怎么样了」没人答得上。SessionState（Redis）只盖确认/补槽
挂起窗（秒-分钟），Ledger 管的是任务生命周期（分钟-小时、跨会话跨重启），两者分层不重叠。

**为什么是 PG 不是 Redis**：compose 的 redis 裸跑无 volume 无 AOF，容器重启数据即丢——
而「跨重启诚实」正是本账本的核心价值。表由 SDK 单方 `CREATE IF NOT EXISTS` 持有
（`agents/reminder/src/store.py` 先例），契约登记 `docs/conventions.md` §9.6。

**为什么在 SDK 侧而不是编排器侧**（对母提案 §4.I「编排器侧新模块」的落点修正，
RFC §2.1）：执行权本来就在 Agent 侧，把登记权放同侧 = 心跳/销单/预算计数都是进程内调用，
无跨服务一致性问题；编排器今天不连 PG，为账本引入新依赖却不承担执行职责得不偿失。
v2 若 engine 要主动派发长任务，它成为本存储契约的另一个客户端，消费面平移即可。

**cancel 走拉模式（零新通道）**：用户说「别查了」→ 路由回执行方 Agent → `cancel()` 置态 →
后台任务下一次 `heartbeat()` 读到 `cancelled` 自行收尾。取消延迟上限≈心跳间隔（≤10s），
分钟级任务可接受；刻意不建 NATS 推送通道（编排器侧无 NATS 订阅，为秒级取消建通道不值）。

**全程 best-effort**：账本是增强不是执行依赖——PG 不可达时所有读写返回 None/[]，Agent
照常干活，只是 ack 话术不承诺可取消/可查询（诚实降级，见 RFC §2.3）。

**唯一例外是 CA2-08 的操作准入**（kind=operation，`operation_*` 方法）：声明了
`admission: durable` 的能力必须先落账再执行，所以这些方法失败时抛 `OperationStoreError`，
由 SDK 准入层拒绝执行——绝不静默放行。设计见
`docs/design/2026-10-01-v2-durable-operation-admission.md`。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("agent.sdk.ledger")

OPERATION_KIND = "operation"               # runtime.operation.KIND；研究/订单的读取都排除它
DEFAULT_INIT_RETRY_S = 30.0


# CA2-11: one error type for every admission store (cloud PostgreSQL, vehicle SQLite).
from runtime.operation import OperationStoreError  # noqa: E402,F401

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "ledger_schema.sql")

PERSONAL_DATA_TARGETS = (
    {
        "id": "task_ledger",
        "storage_variants": ("task_ledger",),
        "sql_variants": ("task_ledger",),
    },
)

# 状态机（单向，终态不可逆；orphaned 是**判定**不是结局，见 heartbeat 的复活分支）
ACCEPTED, RUNNING = "accepted", "running"
DONE, FAILED, CANCELLED, ORPHANED = "done", "failed", "cancelled", "orphaned"
ACTIVE = (ACCEPTED, RUNNING)                 # 用户可见的「在跑」态
TERMINAL = (DONE, FAILED, CANCELLED)         # 真终态（orphaned 可被迟到心跳复活）

# 停止原因（写进 budget.stop_reason，供 Agent 区分话术：用户取消 / 超时停了 / 预算用尽）
STOP_USER, STOP_DEADLINE, STOP_BUDGET = "user", "deadline", "budget"

# 心跳节律建议 ≤10s；ORPHAN_TTL 默认 90s ≈ 9 个心跳的余量（防抖动误判）
DEFAULT_ORPHAN_TTL_S = 90.0
_PUNCT_RE = re.compile(r"[\s，,。.！!？?、；;：:~～\-—_“”\"'‘’()（）]+")


@dataclass
class LedgerTask:
    """一条任务账目。时间统一 epoch 秒（float）——PG 侧是 TIMESTAMPTZ，读出即转。"""
    task_id: str
    user_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    kind: str = ""
    goal: str = ""
    idempotency_key: str = ""
    status: str = ACCEPTED
    progress: str = ""
    budget: dict = field(default_factory=dict)
    result_ref: dict = field(default_factory=dict)
    origin_trace_id: str = ""
    heartbeat_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def stop_reason(self) -> str:
        """SDK 主动截停时写入的原因（user|deadline|budget），无则空串。"""
        return str((self.budget or {}).get("stop_reason") or "")

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


@dataclass
class Duplicate:
    """幂等命中：同一 (user_id, kind, 归一化 goal) 已有在跑任务。

    供 Agent 出「已经在查了，大概还要 N 分钟」话术——防连说两遍/重试风暴双跑。
    """
    existing: LedgerTask


# ── 纯函数（无 IO，可离线单测；SQL 层只做搬运）─────────────────────────────

def normalize_goal(goal: str) -> str:
    """幂等键的目标归一：去标点空白 + 小写。中文小写是空操作，英文主题受益。"""
    return _PUNCT_RE.sub("", (goal or "").strip()).lower()


def idem_key(user_id: str, kind: str, goal: str) -> str:
    """sha256(user_id|kind|归一化 goal)[:16]。"""
    raw = f"{user_id}|{kind}|{normalize_goal(goal)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def budget_exhausted(budget: dict | None, now: float | None = None) -> str:
    """预算是否用尽 → 返回停止原因（""=没用尽）。

    - `deadline_ts`（epoch 秒）过期 → `deadline`（守卫①「deadline 明确终态」）
    - `llm_calls_used >= llm_calls_max` / `ext_calls_used >= ext_calls_max` → `budget`
      （守卫③「token/外部 API 预算」）
    上限缺省/非数 = 不限（弱声明不制造误截停）。
    """
    b = budget or {}
    now = time.time() if now is None else now
    try:
        deadline = float(b.get("deadline_ts") or 0)
    except (TypeError, ValueError):
        deadline = 0.0
    if deadline and now >= deadline:
        return STOP_DEADLINE
    for used_k, max_k in (("llm_calls_used", "llm_calls_max"),
                          ("ext_calls_used", "ext_calls_max")):
        try:
            cap = float(b.get(max_k) or 0)
            used = float(b.get(used_k) or 0)
        except (TypeError, ValueError):
            continue
        if cap and used >= cap:
            return STOP_BUDGET
    return ""


def merge_used(budget: dict | None, used: dict | None) -> dict:
    """把本次心跳上报的用量累加进 budget（只累加 *_used 计数键，不动上限）。"""
    b = dict(budget or {})
    for k, v in (used or {}).items():
        key = k if k.endswith("_used") else f"{k}_used"
        try:
            b[key] = float(b.get(key) or 0) + float(v or 0)
        except (TypeError, ValueError):
            continue
        if b[key].is_integer():
            b[key] = int(b[key])
    return b


def is_orphaned(status: str, heartbeat_at: float, created_at: float,
                now: float | None = None, ttl: float | None = None) -> bool:
    """惰性判定：active 态但超过 ORPHAN_TTL 没心跳 = 崩溃/重启遗留的孤儿。

    从未心跳过（accepted 刚开单就崩）时以 created_at 为参照。判定只在**读**的时候做
    （query_active/get），不额外起扫描进程。
    """
    if status not in ACTIVE:
        return False
    now = time.time() if now is None else now
    ttl = orphan_ttl() if ttl is None else ttl
    ref = heartbeat_at or created_at
    return bool(ref) and (now - ref) > ttl


def orphan_ttl() -> float:
    try:
        return float(os.getenv("LEDGER_ORPHAN_TTL_S", "") or DEFAULT_ORPHAN_TTL_S)
    except ValueError:
        return DEFAULT_ORPHAN_TTL_S


def _epoch(v) -> float:
    """PG TIMESTAMPTZ（datetime）/ 数字 / None → epoch 秒。"""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return v.timestamp()
    except Exception:
        return 0.0


def _jsonb(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def row_to_task(row) -> LedgerTask:
    return LedgerTask(
        task_id=row["task_id"], user_id=row["user_id"],
        session_id=row["session_id"], agent_id=row["agent_id"],
        kind=row["kind"], goal=row["goal"],
        idempotency_key=row["idempotency_key"], status=row["status"],
        progress=row["progress"], budget=_jsonb(row["budget"]),
        result_ref=_jsonb(row["result_ref"]),
        origin_trace_id=row["origin_trace_id"],
        heartbeat_at=_epoch(row["heartbeat_at"]),
        created_at=_epoch(row["created_at"]), updated_at=_epoch(row["updated_at"]))


# ── 存储层 ─────────────────────────────────────────────────────────────────

class TaskLedger:
    """PG 任务账本。无 `POSTGRES_DSN` / asyncpg / 连接失败 → 全部操作静默返回空
    （诚实降级：Agent 照常执行任务，只是不承诺可取消/可查询）。

    **刻意不做内存兜底**（与 reminder 不同）：账本的核心价值就是跨重启诚实，进程内
    兜底会让 ack 承诺「可查询/可取消」而重启后又答不上来——那比没有账本更不诚实。
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = os.getenv("POSTGRES_DSN", "") if dsn is None else dsn
        self._pool = None
        self._pg_ok = False
        self._init_done = False
        # CA2-08：操作准入还要求 schema 里已有 `operation` 列（发布闸授权后才加）。
        self._operations_ok = False
        self._init_failed_at = 0.0
        self._init_lock = asyncio.Lock()

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    def _retry_due(self) -> bool:
        """失败后按退避重试：一次启动期抖动不能让持久准入终身不可用。无 DSN 不重试。"""
        if self._pg_ok or not self._dsn:
            return False
        try:
            backoff = float(os.getenv("LEDGER_INIT_RETRY_S", "") or DEFAULT_INIT_RETRY_S)
        except ValueError:
            backoff = DEFAULT_INIT_RETRY_S
        return time.monotonic() - self._init_failed_at >= backoff

    async def init(self) -> bool:
        """建池 + 幂等建表。可重复调用；失败只记 warning 不抛（best-effort），退避后可重试。"""
        if self._init_done and not self._retry_due():
            return self._pg_ok
        async with self._init_lock:
            if self._init_done and not self._retry_due():
                return self._pg_ok
            self._init_done = True
            if not self._dsn:
                logger.info("TaskLedger: 无 POSTGRES_DSN，任务账本禁用（长任务不可查询/取消）")
                return False
            try:
                import asyncpg
                if self._pool is not None:
                    stale, self._pool = self._pool, None
                    try:
                        await stale.close()
                    except Exception:
                        pass
                self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
                with open(_SCHEMA_PATH, encoding="utf-8") as f:
                    schema = f.read()
                async with self._pool.acquire() as conn:
                    await conn.execute(schema)
                    self._operations_ok = await conn.fetchval(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() "
                        "AND table_name='task_ledger' AND column_name='operation'") is not None
                self._pg_ok = True
                logger.info("TaskLedger: PG 就绪（task_ledger，操作准入%s）",
                            "可用" if self._operations_ok else "未启用：缺 operation 列")
            except Exception as e:
                logger.warning("TaskLedger: PG 不可用（%s），任务账本禁用", e)
                self._pg_ok = False
                self._operations_ok = False
                self._init_failed_at = time.monotonic()
        return self._pg_ok

    async def _ready(self) -> bool:
        if not self._init_done or self._retry_due():
            await self.init()
        return self._pg_ok

    # ── 开单 ──
    async def open(self, user_id: str, session_id: str, agent_id: str, kind: str,
                   goal: str, *, budget: dict | None = None,
                   origin_trace_id: str = "",
                   idempotency_goal: str = "") -> "LedgerTask | Duplicate | None":
        """开一条任务账目。

        同 (user_id, idempotency_key) 已有 active 任务 → `Duplicate`（Agent 据此出
        「已经在查了」话术，不重复开跑）。PG 不可达 → None（诚实降级）。

        **判定权在数据库**（M-D）：此前是「先 SELECT 再 INSERT」，两个实例可以同时
        查不到、同时插入，于是同一个幂等请求有两个实例都拿到了执行权——对写操作
        就是双下单。改为 INSERT ... ON CONFLICT DO NOTHING：谁插入成功谁执行，
        另一个拿到冲突后回读并按 Duplicate 处理。
        前置的 SELECT 保留，但它只干一件事——**清理孤儿**（上次崩了没销单的尸体
        会一直占着唯一键，用户永远被它挡住重试）。
        """
        if not await self._ready():
            return None
        key = idem_key(user_id, kind, idempotency_goal or goal)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM task_ledger WHERE user_id=$1 AND idempotency_key=$2 "
                    "AND status=ANY($3) ORDER BY created_at DESC LIMIT 1",
                    user_id, key, list(ACTIVE))
                if row is not None:
                    existing = row_to_task(row)
                    # 幂等命中的可能是个孤儿（上次崩了没销单）——那不算「已经在查了」，
                    # 就地改判后放行新开单，否则用户永远被一条尸体挡住重试。
                    if is_orphaned(existing.status, existing.heartbeat_at,
                                   existing.created_at):
                        await self._mark_orphaned(conn, existing.task_id)
                    else:
                        return Duplicate(existing=existing)

                task_id = uuid.uuid4().hex     # 禁 id(obj)：内存地址 GC 复用会撞键（corr_id 老教训）
                row = await conn.fetchrow(
                    "INSERT INTO task_ledger (task_id,user_id,session_id,agent_id,kind,"
                    "goal,idempotency_key,status,budget,origin_trace_id,heartbeat_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,now()) "
                    "ON CONFLICT DO NOTHING RETURNING *",
                    task_id, user_id, session_id or "", agent_id, kind, goal, key,
                    ACCEPTED, json.dumps(budget or {}, ensure_ascii=False),
                    origin_trace_id or "")
                if row is None:
                    # 竞争输了：另一个实例已经拿到执行权。回读它的账目当 Duplicate
                    # ——**不能当失败**，那一单正在被别人执行。
                    lost = await conn.fetchrow(
                        "SELECT * FROM task_ledger WHERE user_id=$1 "
                        "AND idempotency_key=$2 AND status=ANY($3) "
                        "ORDER BY created_at DESC LIMIT 1",
                        user_id, key, list(ACTIVE))
                    if lost is not None:
                        return Duplicate(existing=row_to_task(lost))
                    # 极窄的窗：对方刚插入又立刻收尾了。既然已经没有活跃任务，
                    # 说明那件事做完了，照常返回 None（诚实降级，不假装开单成功）。
                    logger.info("TaskLedger.open 竞争后对方已收尾，本次不重复开单")
                    return None
            return row_to_task(row)
        except Exception as e:
            logger.warning("TaskLedger.open 失败（任务照常执行，不承诺可查询）：%s", e)
            return None

    # ── 心跳（cancel 拉模式 + 预算强制的载体）──
    async def heartbeat(self, task_id: str, *, progress: str = "",
                        used: dict | None = None) -> str:
        """打一次心跳，返回**当前 status**。

        - 返回 `cancelled` → 任务自行收尾退出（用户取消 / 超 deadline / 预算用尽；
          具体原因经 `get(task_id).stop_reason` 区分话术）。
        - `used` 累加进 budget 计数（如 `{"llm_calls": 1}`）；累加后若超上限，SDK 就地
          置 `cancelled` 并写 `stop_reason`——预算强制在此兑现（Background 守卫③）。
        - **迟到心跳复活**：被惰性判定成 orphaned 的任务若又打上心跳，说明它其实活着
          → 拉回 running。orphaned 是判定不是结局，误判不该变成假的中断报告。
        - PG 不可达 → 返回 `running`（不因账本故障误杀正在跑的任务）。
        """
        if not await self._ready():
            return RUNNING
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "UPDATE task_ledger SET status=$2,"
                    " progress=CASE WHEN $3='' THEN progress ELSE $3 END,"
                    " heartbeat_at=now(), updated_at=now() "
                    "WHERE task_id=$1 AND status=ANY($4) AND kind<>'operation' RETURNING *",
                    task_id, RUNNING, progress or "", list(ACTIVE) + [ORPHANED])
                if row is None:
                    # 不在可推进态（已 done/failed/cancelled，或行不存在）→ 原样回报，
                    # 拉模式的 cancelled 正是从这条路返回的。
                    cur = await conn.fetchrow(
                        "SELECT status FROM task_ledger WHERE task_id=$1", task_id)
                    return str(cur["status"]) if cur else CANCELLED

                task = row_to_task(row)
                merged = merge_used(task.budget, used)
                reason = budget_exhausted(merged)
                if reason:
                    merged["stop_reason"] = reason
                    await conn.execute(
                        "UPDATE task_ledger SET status=$2, budget=$3::jsonb, "
                        "updated_at=now() WHERE task_id=$1",
                        task_id, CANCELLED,
                        json.dumps(merged, ensure_ascii=False))
                    logger.info("TaskLedger: 任务 %s 因 %s 截停", task_id[:8], reason)
                    return CANCELLED
                if used:
                    await conn.execute(
                        "UPDATE task_ledger SET budget=$2::jsonb, updated_at=now() "
                        "WHERE task_id=$1",
                        task_id, json.dumps(merged, ensure_ascii=False))
                return task.status
        except Exception as e:
            logger.warning("TaskLedger.heartbeat 失败（按继续处理）：%s", e)
            return RUNNING

    # ── 销单 ──
    async def close(self, task_id: str, status: str, *,
                    result_ref: dict | None = None, progress: str = "") -> bool:
        """终态落账（done|failed|cancelled）。终态不可逆：已终态的行不再被覆盖。"""
        if status not in TERMINAL:
            raise ValueError(f"close 只接受终态 {TERMINAL}，收到 {status!r}")
        if not await self._ready():
            return False
        try:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE task_ledger SET status=$2, result_ref=$3::jsonb,"
                    " progress=CASE WHEN $4='' THEN progress ELSE $4 END,"
                    " updated_at=now() WHERE task_id=$1 AND status<>ALL($5) AND kind<>'operation'",
                    task_id, status,
                    json.dumps(result_ref or {}, ensure_ascii=False),
                    progress or "", list(TERMINAL))
            return str(tag).endswith("1")
        except Exception as e:
            logger.warning("TaskLedger.close 失败：%s", e)
            return False

    async def cancel(self, task_id: str, *, reason: str = STOP_USER) -> bool:
        """置取消态（拉模式：后台任务下一次心跳读到即自行收尾）。已终态返回 False。"""
        if not await self._ready():
            return False
        try:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE task_ledger SET status=$2, "
                    "budget=budget || jsonb_build_object('stop_reason', $3::text), "
                    "updated_at=now() WHERE task_id=$1 AND status<>ALL($4) AND kind<>'operation'",
                    task_id, CANCELLED, reason, list(TERMINAL))
            return str(tag).endswith("1")
        except Exception as e:
            logger.warning("TaskLedger.cancel 失败：%s", e)
            return False

    # ── 读 ──
    async def query_active(self, user_id: str, *, kind: str = "",
                           limit: int = 10) -> list[LedgerTask]:
        """该用户「还在跑」的任务（含惰性 orphaned 改判后剔除）。最近受理在前。"""
        if not await self._ready():
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM task_ledger WHERE user_id=$1 AND status=ANY($2) "
                    "AND ($3='' OR kind=$3) AND kind<>'operation' "
                    "ORDER BY created_at DESC LIMIT $4",
                    user_id, list(ACTIVE), kind or "", limit)
                out = []
                for row in rows:
                    task = row_to_task(row)
                    if is_orphaned(task.status, task.heartbeat_at, task.created_at):
                        await self._mark_orphaned(conn, task.task_id)
                        continue
                    out.append(task)
            return out
        except Exception as e:
            logger.warning("TaskLedger.query_active 失败：%s", e)
            return []

    async def recent(self, user_id: str, *, kind: str = "",
                     limit: int = 5) -> list[LedgerTask]:
        """最近任务（含终态），供「刚才那个调研怎么样了」在无 active 时回答。"""
        if not await self._ready():
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM task_ledger WHERE user_id=$1 AND ($2='' OR kind=$2) "
                    "AND kind<>'operation' ORDER BY created_at DESC LIMIT $3",
                    user_id, kind or "", limit)
                out = []
                for row in rows:
                    task = row_to_task(row)
                    if is_orphaned(task.status, task.heartbeat_at, task.created_at):
                        await self._mark_orphaned(conn, task.task_id)
                        task.status = ORPHANED
                    out.append(task)
            return out
        except Exception as e:
            logger.warning("TaskLedger.recent 失败：%s", e)
            return []

    async def get(self, task_id: str) -> LedgerTask | None:
        if not await self._ready():
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM task_ledger WHERE task_id=$1 AND kind<>'operation'",
                    task_id)
                if row is None:
                    return None
                task = row_to_task(row)
                if is_orphaned(task.status, task.heartbeat_at, task.created_at):
                    await self._mark_orphaned(conn, task.task_id)
                    task.status = ORPHANED
            return task
        except Exception as e:
            logger.warning("TaskLedger.get 失败：%s", e)
            return None

    @staticmethod
    async def _mark_orphaned(conn, task_id: str) -> None:
        """惰性改判。WHERE 里再钉一次 TTL 与状态——并发心跳刚好落在读与写之间时，
        UPDATE 不匹配即放弃改判（风险表「二次确认仍超时才改判」的落地形式）。"""
        await conn.execute(
            "UPDATE task_ledger SET status=$2, updated_at=now() "
            "WHERE task_id=$1 AND status=ANY($3) AND kind<>'operation' "
            "AND COALESCE(heartbeat_at, created_at) < now() - ($4 || ' seconds')::interval",
            task_id, ORPHANED, list(ACTIVE), str(int(orphan_ttl())))

    # ── CA2-08 操作准入（kind=operation）：失败一律抛 OperationStoreError ─────────
    #
    # 判定在 runtime.operation（纯函数）；这里只搬运，且每条写都带状态/phase/绑定条件，
    # 并发时由数据库裁决谁赢。时间一律用数据库时钟，避免各 Agent 容器墙钟不一致。

    async def operations_ready(self) -> bool:
        return await self._ready() and self._operations_ok

    async def _operation_conn(self):
        if not await self.operations_ready():
            raise OperationStoreError("operation_admission_unavailable")
        return self._pool.acquire()

    async def operation_insert(self, *, operation_id: str, user_id: str, session_id: str,
                               agent_id: str, trace_id: str, envelope: dict) -> bool:
        """首次准入。返回 False 表示该 operation_id 已有记录（由调用方重读裁决）。"""
        try:
            async with await self._operation_conn() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO task_ledger (task_id,user_id,session_id,agent_id,kind,goal,"
                    "idempotency_key,status,budget,result_ref,origin_trace_id,heartbeat_at,operation) "
                    "VALUES ($1,$2,$3,$4,'operation','',$1,'accepted','{}'::jsonb,'{}'::jsonb,$5,now(),"
                    "$6::jsonb) ON CONFLICT DO NOTHING RETURNING task_id",
                    operation_id, user_id, session_id or "", agent_id, trace_id or "",
                    json.dumps(envelope, ensure_ascii=False))
            return row is not None
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_insert_failed") from e

    async def operation_get(self, operation_id: str) -> dict | None:
        """读一条准入记录，附数据库时钟下的 now_ms / touched_ms。"""
        try:
            async with await self._operation_conn() as conn:
                row = await conn.fetchrow(
                    "SELECT status, user_id, agent_id, operation, "
                    "(extract(epoch FROM now()) * 1000)::bigint AS now_ms, "
                    "(extract(epoch FROM COALESCE(heartbeat_at, created_at)) * 1000)::bigint "
                    "AS touched_ms FROM task_ledger WHERE task_id=$1 AND kind='operation'",
                    operation_id)
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_read_failed") from e
        if row is None:
            return None
        return {"status": str(row["status"]), "user_id": str(row["user_id"]),
                "agent_id": str(row["agent_id"]), "envelope": _jsonb(row["operation"]),
                "touched_ms": int(row["touched_ms"] or 0), "now_ms": int(row["now_ms"] or 0)}

    async def operation_claim(self, operation_id: str, *, observed_binding: str,
                              envelope: dict, session_id: str, trace_id: str) -> bool:
        """awaiting → executing 的 CAS；绑定与过期在同一条 UPDATE 里复核。"""
        try:
            async with await self._operation_conn() as conn:
                row = await conn.fetchrow(
                    "UPDATE task_ledger SET operation=$3::jsonb, session_id=$4, origin_trace_id=$5, "
                    "heartbeat_at=now(), updated_at=now() "
                    "WHERE task_id=$1 AND kind='operation' AND status='accepted' "
                    "AND operation->>'phase'='awaiting' AND operation->>'binding_sha256'=$2 "
                    "AND (operation->>'expires_at_ms')::bigint > (extract(epoch FROM now()) * 1000)::bigint "
                    "RETURNING task_id",
                    operation_id, observed_binding, json.dumps(envelope, ensure_ascii=False),
                    session_id or "", trace_id or "")
            return row is not None
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_claim_failed") from e

    async def operation_settle(self, operation_id: str, *, binding: str, status: str,
                               phase: str, result_ref: dict, await_ttl_ms: int = 0) -> bool:
        """记录 handler 结局。只从 executing（或被并发判 unknown 的 orphaned）出发。"""
        try:
            async with await self._operation_conn() as conn:
                tag = await conn.execute(
                    "UPDATE task_ledger SET status=$2, "
                    "operation=jsonb_set(operation, '{phase}', to_jsonb($3::text)) || "
                    "CASE WHEN $4::bigint > 0 THEN jsonb_build_object('expires_at_ms', "
                    "(extract(epoch FROM now()) * 1000)::bigint + $4::bigint) ELSE '{}'::jsonb END, "
                    "result_ref=$5::jsonb, updated_at=now() "
                    "WHERE task_id=$1 AND kind='operation' AND operation->>'binding_sha256'=$6 "
                    "AND (status='orphaned' OR (status='accepted' AND operation->>'phase'='executing'))",
                    operation_id, status, phase, int(await_ttl_ms),
                    json.dumps(result_ref, ensure_ascii=False), binding)
            return str(tag).endswith(" 1")
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_settle_failed") from e

    async def operation_mark_stale(self, operation_id: str, *, binding: str,
                                   stale_s: float, result_ref: dict) -> bool:
        """陈旧的 executing → orphaned（结局未知）。条件在 SQL 里复核，并发心跳则放弃。"""
        try:
            async with await self._operation_conn() as conn:
                tag = await conn.execute(
                    "UPDATE task_ledger SET status='orphaned', result_ref=$3::jsonb, updated_at=now() "
                    "WHERE task_id=$1 AND kind='operation' AND status='accepted' "
                    "AND operation->>'phase'='executing' AND operation->>'binding_sha256'=$2 "
                    "AND COALESCE(heartbeat_at, created_at) <= now() - make_interval(secs => $4::double precision)",
                    operation_id, binding, json.dumps(result_ref, ensure_ascii=False), float(stale_s))
            return str(tag).endswith(" 1")
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_mark_failed") from e

    async def operation_mark_expired(self, operation_id: str, *, binding: str,
                                     result_ref: dict) -> bool:
        """过期的 awaiting → cancelled（确认窗口已过，什么都没执行）。"""
        try:
            async with await self._operation_conn() as conn:
                tag = await conn.execute(
                    "UPDATE task_ledger SET status='cancelled', result_ref=$3::jsonb, updated_at=now() "
                    "WHERE task_id=$1 AND kind='operation' AND status='accepted' "
                    "AND operation->>'phase'='awaiting' AND operation->>'binding_sha256'=$2 "
                    "AND COALESCE((operation->>'expires_at_ms')::bigint, 0) "
                    "<= (extract(epoch FROM now()) * 1000)::bigint",
                    operation_id, binding, json.dumps(result_ref, ensure_ascii=False))
            return str(tag).endswith(" 1")
        except OperationStoreError:
            raise
        except Exception as e:
            raise OperationStoreError("operation_mark_failed") from e

    async def close_pool(self) -> None:
        if self._pool is not None:
            try:
                await self._pool.close()
            finally:
                self._pool = None
                self._pg_ok = False

"""车端会话轮发件箱（CA2-11 v2 §2.8）：T0 本地快路径离线那几轮的补写。

端侧本地处理完一轮，`_record_local_turn` 把 user + assistant 两行（含动作）异步写进记忆服务。离线或记忆服务
不可用时写不进去，审计（「我刚才让你做了什么」）就念不出离线那几轮。发件箱把没送到的那一轮落在车端 SQLite，
重连后按原 turn_id / exchange_id 重放——记忆服务按 turn_id 幂等（同 id 同内容 = 重放成功，同 id 异内容拒收）。

隐私不变量（逐条硬性）：

- **删除之前发生的一轮，绝不在删除之后写进去。** 每行带入箱时观测到的 owner 代际（`memory_epoch`），重放原样
  带回、照常过记忆服务的代际栅栏：之后删过记忆，栅栏判旧代际拒收，这一轮的行就地丢弃。入箱只认**观测到的**
  代际——本轮请求到达时取到的，或到达那一刻进程里缓存的最近一次成功值（只会更旧，失败方向是少写）；两者都
  没有就不入箱，与修前一样丢掉。表结构上 `memory_epoch` 也不许为空：空代际在记忆服务那边等于「旧调用方」、
  不过栅栏。
- `memory_enabled=false` 的轮次根本到不了这里（调用方入口即返回）。
- 保留 24 h（与车端操作日志一致），到期整轮删除；容量封顶，满了不再收新轮。
- 原话只进库、不进日志：日志只记条数与异常类型。

顺序：同一 exchange 先 user 后 assistant，跨 exchange 按发生时刻。同一会话还有待补写的轮时，新的本地轮也排进
发件箱（不直写），由重放按先后补齐——否则重连后的第一句先落库，离线那几轮反而排到它后面。重放带上原始
发生时刻（`AppendTurnRequest.occurred_at_ms`），会话历史里记的仍是当时的时刻。

文件：`EDGE_TURN_OUTBOX` 指定；没指定时放在车端操作日志（`EDGE_OPERATION_LOG`）同一目录、同一个持久卷里的
独立文件——操作日志按设计不存个人数据，两份数据不混在一个库里。两者都没有时只活在进程内存里（启动时告警）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from orchestrator.edge.operation_log import PATH_ENV as OPERATION_LOG_ENV

logger = logging.getLogger("edge.turn_outbox")

PATH_ENV = "EDGE_TURN_OUTBOX"
#: 没单独配置路径时，在车端操作日志同目录下用的文件名。
SIBLING_FILE = "edge-turn-outbox.sqlite3"
RETENTION_MS = 24 * 3600 * 1000
#: 容量上限（行）。一轮至多两行；离线一整天、每分钟都有本地快指令也够用。满了不收新轮（少写，不挤掉旧的）。
MAX_ROWS = 2000
#: 一次重放最多处理几个 exchange。
BATCH_EXCHANGES = 50
#: 没送到之后再试的间隔（按已试次数），封顶后一直按最后一档；记忆服务又够得着时（取到代际 / 直写成功）立即重试。
BACKOFF_S = (5, 15, 30, 60, 120)
#: 箱子空着时多久醒一次（只为清掉过期行）。
IDLE_S = 300.0

#: 一次写入的结局（端侧记忆客户端 `append` 的返回值）。
APPENDED = "appended"        # 记忆服务收下了（含同 id 同内容的重放）
REFUSED = "refused"          # 记忆服务拒收：删过记忆（旧代际）或同 id 异内容——重试也不会成，丢弃
UNAVAILABLE = "unavailable"  # 没送到：离线、超时、记忆服务不可用——留着重放

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS edge_turn_outbox (
    turn_id            TEXT PRIMARY KEY,
    exchange_id        TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    session_id         TEXT NOT NULL,
    user_id            TEXT NOT NULL CHECK (user_id <> ''),
    occupant_id        TEXT NOT NULL DEFAULT 'primary',
    vehicle_id         TEXT NOT NULL DEFAULT '',
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text               TEXT NOT NULL,
    actions            TEXT NOT NULL DEFAULT '[]',
    memory_epoch       TEXT NOT NULL CHECK (memory_epoch <> ''),
    speaker_unverified INTEGER NOT NULL DEFAULT 0,
    created_ms         INTEGER NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_ms            INTEGER NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS edge_turn_outbox_order ON edge_turn_outbox (created_ms, exchange_id, seq)",
)
_COLUMNS = ("turn_id", "exchange_id", "seq", "session_id", "user_id", "occupant_id", "vehicle_id", "role",
            "text", "actions", "memory_epoch", "speaker_unverified", "created_ms", "attempts", "next_ms")


@dataclass(frozen=True)
class OutboxTurn:
    """发件箱里的一行：一条待补写的会话轮（user 或 assistant）。"""

    turn_id: str
    exchange_id: str
    seq: int
    session_id: str
    user_id: str
    occupant_id: str
    vehicle_id: str
    role: str
    text: str
    actions: tuple[str, ...]
    memory_epoch: str
    speaker_unverified: bool
    created_ms: int
    attempts: int = 0
    next_ms: int = 0


def _row_to_turn(row) -> OutboxTurn:
    data = dict(zip(_COLUMNS, row))
    try:
        actions = json.loads(data["actions"] or "[]")
    except (TypeError, ValueError):
        actions = []
    data["actions"] = tuple(a for a in actions if isinstance(a, str)) if isinstance(actions, list) else ()
    data["speaker_unverified"] = bool(data["speaker_unverified"])
    return OutboxTurn(**data)


class EdgeTurnOutbox:
    """车端 SQLite 发件箱。所有方法同步、不抛（存储出错只记异常类型、按「没收下」处理）。"""

    def __init__(self, path: str, *, clock_ms=None, retention_ms: int = RETENTION_MS,
                 max_rows: int = MAX_ROWS):
        self.path = path
        self.persistent = path != ":memory:"
        self._now = clock_ms or (lambda: int(time.time() * 1000))
        self._retention_ms = int(retention_ms)
        self._max_rows = int(max_rows)
        self._lock = threading.Lock()
        if self.persistent:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if self.persistent:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        for statement in _SCHEMA:
            self._db.execute(statement)

    @classmethod
    def from_env(cls, env=None) -> "EdgeTurnOutbox":
        """`EDGE_TURN_OUTBOX` 指定文件；没指定就放在车端操作日志同目录（同一个持久卷）；都没有只活在内存里。"""
        env = os.environ if env is None else env
        path = (env.get(PATH_ENV) or "").strip()
        if not path:
            log_path = (env.get(OPERATION_LOG_ENV) or "").strip()
            if log_path and log_path != ":memory:":
                path = os.path.join(os.path.dirname(log_path), SIBLING_FILE)
        if not path:
            logger.warning("%s is not set: the local turn outbox only lives in memory", PATH_ENV)
        return cls(path or ":memory:")

    def now_ms(self) -> int:
        return int(self._now())

    # ── 写 ────────────────────────────────────────────────────────────

    def put(self, turns: Sequence[OutboxTurn]) -> bool:
        """一个 exchange 的几行原子入箱；同 turn_id 已在箱里就不重复收。满了 / 写不进去返回 False。"""
        if not turns:
            return False
        if any(not t.memory_epoch or not t.user_id for t in turns):
            return False                      # 没有观测到的代际就不入箱（表上的约束是第二道）
        now = self.now_ms()
        self.purge(now)
        values = [(t.turn_id, t.exchange_id, int(t.seq), t.session_id, t.user_id, t.occupant_id or "primary",
                   t.vehicle_id or "", t.role, t.text,
                   json.dumps(list(t.actions), ensure_ascii=False, separators=(",", ":")),
                   t.memory_epoch, int(bool(t.speaker_unverified)), int(t.created_ms), 0, now)
                  for t in turns]
        with self._lock:
            try:
                count = self._db.execute("SELECT COUNT(*) FROM edge_turn_outbox").fetchone()[0]
                if count + len(values) > self._max_rows:
                    logger.warning("local turn outbox is full (%d rows): a local exchange is not kept", count)
                    return False
                self._db.execute("BEGIN IMMEDIATE")
                self._db.executemany(
                    f"INSERT OR IGNORE INTO edge_turn_outbox ({', '.join(_COLUMNS)})"
                    f" VALUES ({', '.join('?' for _ in _COLUMNS)})", values)
                self._db.execute("COMMIT")
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                logger.warning("local turn outbox write failed: %s", type(exc).__name__)
                return False
        return True

    def remove(self, exchange_id: str) -> int:
        return self._change("DELETE FROM edge_turn_outbox WHERE exchange_id = ?", (exchange_id,))

    def defer(self, exchange_id: str, *, attempts: int, next_ms: int) -> int:
        return self._change("UPDATE edge_turn_outbox SET attempts = ?, next_ms = ? WHERE exchange_id = ?",
                            (int(attempts), int(next_ms), exchange_id))

    def make_due(self, now: int | None = None) -> int:
        """退避作废：所有行马上可重放（记忆服务又够得着了）。"""
        now = self.now_ms() if now is None else int(now)
        return self._change("UPDATE edge_turn_outbox SET next_ms = ? WHERE next_ms > ?", (now, now))

    def purge(self, now: int | None = None) -> int:
        """删掉超过保留期的整轮（同一 exchange 的几行发生时刻相同，按发生时刻删不会拆开一轮）。"""
        now = self.now_ms() if now is None else int(now)
        removed = self._change("DELETE FROM edge_turn_outbox WHERE created_ms < ?", (now - self._retention_ms,))
        if removed:
            logger.info("local turn outbox: %d expired row(s) removed", removed)
        return removed

    # ── 读 ────────────────────────────────────────────────────────────

    def pending(self) -> int:
        row = self._one("SELECT COUNT(*) FROM edge_turn_outbox")
        return int(row[0]) if row else 0

    def has_session(self, session_id: str) -> bool:
        return self._one("SELECT 1 FROM edge_turn_outbox WHERE session_id = ? LIMIT 1", (session_id,)) is not None

    def next_due_ms(self) -> int | None:
        row = self._one("SELECT MIN(next_ms) FROM edge_turn_outbox")
        return int(row[0]) if row and row[0] is not None else None

    def exchanges(self, limit: int = BATCH_EXCHANGES) -> list[list[OutboxTurn]]:
        """最早发生的 `limit` 个 exchange，每个按 user → assistant 排好。"""
        rows = self._all(
            f"SELECT {', '.join(_COLUMNS)} FROM edge_turn_outbox WHERE exchange_id IN ("
            " SELECT exchange_id FROM edge_turn_outbox GROUP BY exchange_id"
            " ORDER BY MIN(created_ms), exchange_id LIMIT ?)"
            " ORDER BY created_ms, exchange_id, seq", (int(limit),))
        grouped: dict[str, list[OutboxTurn]] = {}
        for row in rows:
            turn = _row_to_turn(row)
            grouped.setdefault(turn.exchange_id, []).append(turn)
        return list(grouped.values())

    # ── 存储原语 ────────────────────────────────────────────────────────

    def _change(self, sql: str, params=()) -> int:
        with self._lock:
            try:
                return self._db.execute(sql, params).rowcount
            except sqlite3.Error as exc:
                logger.warning("local turn outbox update failed: %s", type(exc).__name__)
                return 0

    def _one(self, sql: str, params=()):
        with self._lock:
            try:
                return self._db.execute(sql, params).fetchone()
            except sqlite3.Error as exc:
                logger.warning("local turn outbox read failed: %s", type(exc).__name__)
                return None

    def _all(self, sql: str, params=()) -> list:
        with self._lock:
            try:
                return self._db.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                logger.warning("local turn outbox read failed: %s", type(exc).__name__)
                return []


class TurnOutboxReplayer:
    """发件箱的重放循环（端侧进程内）。只经记忆客户端的 `append` 写，不碰 VAL、不碰车。

    一次重放按发生时刻逐个 exchange 送：收下或拒收都删掉这一轮；没送到就给这一轮记退避、本次停手（记忆服务
    多半整个不可用，后面的照样送不到）。同一会话里前一轮还在退避，后面的轮这次不送——会话历史的先后不能乱。
    """

    def __init__(self, outbox: EdgeTurnOutbox, append: Callable[[OutboxTurn], Awaitable[str]], *,
                 batch: int = BATCH_EXCHANGES, backoff_s: Sequence[float] = BACKOFF_S, idle_s: float = IDLE_S):
        self.outbox = outbox
        self._append = append
        self._batch = int(batch)
        self._backoff_s = tuple(backoff_s) or (5,)
        self._idle_s = float(idle_s)
        self._wake = asyncio.Event()
        self._kicked = False

    def kick(self) -> None:
        """记忆服务又够得着了（取到代际 / 直写成功）：退避作废、马上重放一轮。箱子空着时什么也不做。"""
        if self.outbox.pending():
            self._kicked = True
            self._wake.set()

    def _backoff_ms(self, attempts: int) -> int:
        index = min(max(attempts, 1), len(self._backoff_s)) - 1
        return int(self._backoff_s[index] * 1000)

    async def replay_once(self) -> dict:
        now = self.outbox.now_ms()
        self.outbox.purge(now)
        if self._kicked:
            self._kicked = False
            self.outbox.make_due(now)
        sent = refused = 0
        unavailable = False
        blocked: set[str] = set()
        for exchange in self.outbox.exchanges(self._batch):
            head = exchange[0]
            if head.session_id in blocked:
                continue
            if max(t.next_ms for t in exchange) > now:
                blocked.add(head.session_id)          # 前一轮还在退避：同一会话后面的轮这次不送
                continue
            outcome = await self._send(exchange)
            if outcome == UNAVAILABLE:
                attempts = max(t.attempts for t in exchange) + 1
                self.outbox.defer(head.exchange_id, attempts=attempts, next_ms=now + self._backoff_ms(attempts))
                unavailable = True
                break
            self.outbox.remove(head.exchange_id)
            if outcome == APPENDED:
                sent += 1
            else:
                refused += 1
        if sent or refused:
            logger.info("local turn outbox: %d exchange(s) replayed, %d refused and dropped, %d row(s) left",
                        sent, refused, self.outbox.pending())
        return {"sent": sent, "refused": refused, "unavailable": unavailable}

    async def _send(self, exchange: list[OutboxTurn]) -> str:
        for turn in exchange:
            try:
                outcome = await self._append(turn)
            except Exception as exc:  # noqa: BLE001 —— 记忆客户端本不该抛；抛了也只当没送到
                logger.warning("local turn outbox replay failed: %s", type(exc).__name__)
                return UNAVAILABLE
            if outcome == REFUSED:
                return REFUSED
            if outcome != APPENDED:
                return UNAVAILABLE
        return APPENDED

    def _delay_s(self) -> float:
        due = self.outbox.next_due_ms()
        if due is None:
            return self._idle_s
        return min(self._idle_s, max(1.0, (due - self.outbox.now_ms()) / 1000))

    async def run_forever(self) -> None:
        while True:
            try:
                await self.replay_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 一次重放出错不许拖垮端侧进程
                # 出错的这一轮不因叫醒而立即重来（否则同一个错会原地空转），按延迟再等
                self._kicked = False
                logger.warning("local turn outbox replay pass failed: %s", type(exc).__name__)
            self._wake.clear()
            if self._kicked:
                await asyncio.sleep(0)                  # 重放期间又被叫醒：让出事件循环后马上再来一轮
                continue
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._delay_s())

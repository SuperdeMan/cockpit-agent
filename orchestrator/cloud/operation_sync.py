"""CA2-11 v2：车端操作结局的后台对账、结果补记与告知。

设计 docs/design/2026-10-12-v2-edge-operation-sync.md。车端日志是唯一权威（v1 §2.4）；云端只记一份索引——「哪些操作没收到
回执、结论是什么」（SessionStore 的 `planner:opsync:*`），后台按退避去问车端日志，问到定论就写回，执行审计据此如实回答。

三条不变量：
1. **只读**：后台只发恢复查询（`EdgeCall.operation_query`），**绝不重发写命令**。「车端从没收到」的那一次重发只在同步执行
   路径里由执行器做（v1），后台查到「查无此条」只记「没有执行」。
2. **不补偿**：查到已经执行也不做反向动作。补偿是新的受控动作，要用户重新下达，照常经权限与确认。
3. **不宣称恰好一次**：结论只有「车端记录显示已完成 / 没有执行 / 执行失败 / 确认不了」，问不到就说还在核实。
"""
from __future__ import annotations

import asyncio
import logging
import time

from google.protobuf import json_format

from runtime import operation as op
from runtime.proactive import DIRECT, GOVERNED, P_ADVISORY
from runtime.session_facts import operation_clause

from .session import SAVE_OK

logger = logging.getLogger("planner.operation_sync")

#: 结论未定时再问车端的间隔（秒），按已问次数取；问满之后每 15 分钟一次。
BACKOFF_S = (5, 15, 30, 60, 120, 300, 600, 900)
#: 对账期限：车端日志保留 24 h（`orchestrator/edge/operation_log.RETENTION_MS`），过了保留期「查无此条」就不再说明
#: 「从没收到」。留 1 h 余量；到期仍没定论 ⇒ 确认不了。
HORIZON_S = 23 * 3600
#: 刚超时就问到「查无此条」：执行器同步重发的那一份可能还在路上。这段时间内的 absent 不下结论（与车端「执行中过久」
#: 的阈值 `OPERATION_EXECUTING_STALE_S` 缺省值同量级：任何还没送到的命令过了它都不该再被执行）。
ABSENT_GRACE_S = 120
#: 定论之后再留给审计的时间（「刚才执行了吗」），之后随会话 hash 过期。
RETAIN_AFTER_S = 2 * 3600
#: 只在这个窗口内把后台定论主动告诉用户；再晚只补记（用户多半已不再惦记这件事）。
NOTIFY_WINDOW_S = 15 * 60
#: 主动告知在治理器里最多攒多久（驾驶负荷 / 免打扰 / 频控时延后，过了就不说）。
NOTIFY_TTL_MS = 10 * 60 * 1000
QUERY_TIMEOUT_S = 2.0
TICK_S = 5.0
#: 一轮最多同时问几条（车端离线时每条都要等满超时）。
BATCH = 16
AGENT_ID = "cloud-planner"
MESSAGE_TYPE = "operation_outcome"

#: 同步路径收尾时对用户说的话（零领域词）。「会继续核实」只在真登记上了后台对账时才说。
SYNC_SPEECH = {
    op.SYNC_CHECKING: "车端没有回执，这个操作做没做成还不确定，我会继续向车端核实，先别重复下这个指令。",
    op.SYNC_UNKNOWN: "车端没有回执，车端记录也确认不了这个操作做没做成，请留意车辆状态，先别重复下这个指令。",
    op.SYNC_NOT_EXECUTED: "车端没有回执，车端记录显示这个操作没有执行，需要的话请再说一次。",
}


def edge_operation_status(response) -> str:
    """恢复查询的回包 → 车端记录的状态（done / failed / accepted / orphaned / cancelled / absent）；问不出来 ⇒ ""。"""
    try:
        if response is None or response.status != op.STATUS_OK or not response.HasField("data"):
            return ""
        record = json_format.MessageToDict(response.data).get("_operation")
    except Exception:            # 回包形状不可信：认不出就当没问到
        return ""
    status = record.get("status") if isinstance(record, dict) else ""
    return status if isinstance(status, str) else ""


def backoff_ms(attempts: int) -> int:
    return BACKOFF_S[min(max(int(attempts), 1), len(BACKOFF_S)) - 1] * 1000


def audit_items(records) -> list[dict]:
    """对账记录 → 执行审计要的 `[{"what", "outcome"}]`（按登记先后，形状见 `session_facts.audit_answer`）。"""
    return [{"what": str(rec.get("what") or ""), "outcome": str(rec.get("status") or "")}
            for rec in records or [] if isinstance(rec, dict) and rec.get("status") in op.SYNC_STATES]


def outcome_notice(record: dict) -> str:
    """后台定论的主动告知话术：与审计同一句（`operation_clause`），确认不了时再提醒留意车况。"""
    clause = operation_clause(record.get("what"), record.get("status"))
    if not clause:
        return ""
    tail = "请留意车辆状态。" if record.get("status") == op.SYNC_UNKNOWN else ""
    return f"刚才{clause}。{tail}"


class EdgeOperationSync:
    """登记（执行器调）、后台对账（`run_forever`）、告知。存储全在 SessionStore（隐私删除同一个出口）。"""

    def __init__(self, store, query, *, publish=None, now_ms=None):
        self._store = store
        # `clients.query_edge_operation(vehicle_id, operation_id, *, step_id, timeout)` → ExecuteResponse
        self._query = query
        # async (payload) -> governed / direct / skipped；NATS 连上之后再装（见 main.py），没有就只补记不告知。
        self.publish = publish
        self._now = now_ms or (lambda: int(time.time() * 1000))

    # ── 登记：同步路径收尾时 ─────────────────────────────────────────────────

    async def note(self, step, ctx, edge_status: str) -> bool:
        """把一条车端步的结局登记进对账记录，返回是否登记上。

        `edge_status` 是同步路径最后一次问到的车端状态（没问到 = ""）。结论未定 ⇒ 交给后台；已定（恢复成功 / 等确认过期 /
        车端也确认不了）⇒ 只补记给审计。同一 operation 只登记一次。
        """
        session_id = str(getattr(ctx, "session_id", "") or "")
        owner = str(getattr(ctx, "user_id", "") or "")
        operation_id = str(getattr(step, "operation_id", "") or "")
        vehicle_id = str(getattr(ctx, "vehicle_id", "") or "")
        if not (session_id and owner and vehicle_id and op.valid_operation_id(operation_id)):
            return False
        outcome = op.sync_outcome(edge_status)
        if edge_status == op.EDGE_ABSENT:
            outcome = op.SYNC_CHECKING        # 同步路径刚重发过同一份：查无此条不下结论
        checking = outcome == op.SYNC_CHECKING
        now = self._now()
        record = {
            "operation_id": operation_id, "step_id": str(getattr(step, "id", "") or ""),
            "vehicle_id": vehicle_id, "occupant_id": str(getattr(ctx, "occupant_id", "") or "primary"),
            "intent": str(getattr(step, "intent", "") or ""),
            "what": str(getattr(step, "capability_description", "") or "")[:20],
            "exchange_id": str(getattr(ctx, "request_id", "") or ""),
            "created_ms": now, "status": outcome, "edge_status": edge_status or "unanswered",
            "attempts": 0, "next_ms": now + backoff_ms(1), "deadline_ms": now + HORIZON_S * 1000,
            "resolved_ms": 0 if checking else now, "via": "" if checking else "sync",
            # 只有对用户说过「会继续核实」的才在后台定论后告知；同步路径已定论的当场就说清楚了。
            "notify": checking, "notified": False,
        }
        ttl = (HORIZON_S + RETAIN_AFTER_S) if checking else RETAIN_AFTER_S
        try:
            saved = await self._store.note_operation(session_id, record, owner_user_id=owner,
                                                     ttl_s=ttl, active=checking)
        except Exception as exc:
            logger.warning("operation sync: note failed (%s)", type(exc).__name__)
            return False
        logger.info("operation sync: noted %s as %s (vehicle said %s)", operation_id, outcome,
                    edge_status or "nothing")
        return saved == SAVE_OK

    # ── 后台对账 ──────────────────────────────────────────────────────────────

    async def tick(self) -> int:
        """处理一轮到期的待对账记录，返回处理条数。后端读不到就等下一轮。"""
        rows = await self._store.active_operations()
        if not rows:
            return 0
        now = self._now()
        due = [(member, record) for member, record in rows
               if int(record.get("next_ms") or 0) <= now][:BATCH]
        if due:
            await asyncio.gather(*(self._advance(member, record, now) for member, record in due))
        return len(due)

    async def run_forever(self, tick_s: float = TICK_S) -> None:
        logger.info("operation sync: background reconciliation started (every %ss, announce=%s)",
                    tick_s, "on" if self.publish is not None else "off")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("operation sync: tick failed (%s)", type(exc).__name__)
            await asyncio.sleep(tick_s)

    async def _advance(self, member: str, record: dict, now: int) -> None:
        try:
            if record.get("status") != op.SYNC_CHECKING:
                await self._store.resolve_operation(member, record, active=False)   # 已定论却还挂在集合里
                return
            if now >= int(record.get("deadline_ms") or 0):
                await self._settle(member, record, op.SYNC_UNKNOWN, "deadline", now)
                return
            edge_status = await self._ask(record)
            outcome = op.sync_outcome(edge_status)
            if (edge_status == op.EDGE_ABSENT
                    and now - int(record.get("created_ms") or 0) < ABSENT_GRACE_S * 1000):
                outcome = op.SYNC_CHECKING
            if outcome == op.SYNC_CHECKING:
                attempts = int(record.get("attempts") or 0) + 1
                await self._store.resolve_operation(
                    member, {**record, "attempts": attempts, "edge_status": edge_status or "unanswered",
                             "next_ms": now + backoff_ms(attempts)}, active=True)
                return
            await self._settle(member, record, outcome, edge_status, now)
        except Exception as exc:
            logger.warning("operation sync: %s not advanced (%s)", record.get("operation_id"),
                           type(exc).__name__)

    async def _ask(self, record: dict) -> str:
        """只读：问车端日志这条操作的状态。永远只发恢复查询，从不重发写命令。"""
        try:
            response = await asyncio.wait_for(
                self._query(str(record.get("vehicle_id") or ""), str(record.get("operation_id") or ""),
                            step_id=f"{record.get('step_id') or 'op'}-sync", timeout=QUERY_TIMEOUT_S),
                timeout=QUERY_TIMEOUT_S)
        except Exception as exc:
            logger.info("operation sync: vehicle did not answer for %s (%s)", record.get("operation_id"),
                        type(exc).__name__)
            return ""
        return edge_operation_status(response)

    async def _settle(self, member: str, record: dict, outcome: str, edge_status: str, now: int) -> None:
        settled = {**record, "status": outcome, "edge_status": edge_status or "unanswered",
                   "resolved_ms": now, "via": "background", "next_ms": 0,
                   "attempts": int(record.get("attempts") or 0) + (0 if edge_status == "deadline" else 1)}
        if not await self._store.resolve_operation(member, settled, active=False):
            return                           # 已被删除 / 隐私写栅栏：不告知，也不复活
        logger.info("operation sync: %s settled as %s (vehicle said %s)", record.get("operation_id"),
                    outcome, edge_status)
        if self._should_notify(settled, now) and await self._notify(settled, now):
            await self._store.resolve_operation(member, {**settled, "notified": True}, active=False)

    def _should_notify(self, record: dict, now: int) -> bool:
        return (self.publish is not None and bool(record.get("notify")) and not record.get("notified")
                and record.get("status") in op.SYNC_TERMINAL
                and now - int(record.get("created_ms") or 0) <= NOTIFY_WINDOW_S * 1000)

    async def _notify(self, record: dict, now: int) -> bool:
        speech = outcome_notice(record)
        if not speech:
            return False
        payload = {
            "type": MESSAGE_TYPE, "agent_id": AGENT_ID, "speech": speech, "ts": now,
            # 建议类：全套治理（驾驶负荷 / 免打扰 / 频控照常拦）。这是系统自己许诺的回音，不是用户显式约定。
            "priority": P_ADVISORY, "ttl_ms": NOTIFY_TTL_MS,
            "dedup_key": f"{MESSAGE_TYPE}|{record.get('operation_id')}",
            # 网关按车辆 + 主体投递（`broadcastFor`）：只告诉下达这条命令的人，不串车。
            "user_id": str(record.get("owner_user_id") or ""), "vehicle_id": str(record.get("vehicle_id") or ""),
            "occupant_id": str(record.get("occupant_id") or "primary"),
        }
        try:
            result = await self.publish(payload)
        except Exception as exc:
            logger.warning("operation sync: notice not published (%s)", type(exc).__name__)
            return False
        return result in (GOVERNED, DIRECT)

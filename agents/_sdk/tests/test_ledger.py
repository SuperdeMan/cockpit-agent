"""Task Ledger 单测（M2 P0）：状态机 / 幂等 / orphan 惰性判定 / 预算截停 / PG 不可达降级。

分层策略：
- **纯函数层**（idem_key/budget_exhausted/merge_used/is_orphaned）离线全覆盖；
- **分支接线层**用可编程假连接驱动（Duplicate / orphan 改判放行 / 预算截停 / 终态不可逆），
  断言「走了哪条分支、发了什么 SQL、传了什么参数」；
- **SQL 语义正确性**由真栈 e2e 兜（本文件不自造 SQL 引擎，见 RFC §6 DoD）。
"""
import json
import time

import pytest

from agents._sdk.ledger import (
    ACCEPTED, ACTIVE, CANCELLED, DONE, FAILED, ORPHANED, RUNNING, STOP_BUDGET,
    STOP_DEADLINE, STOP_USER, TERMINAL, Duplicate, LedgerTask, TaskLedger,
    budget_exhausted, idem_key, is_orphaned, merge_used, normalize_goal,
    row_to_task,
)


# ── 纯函数：幂等键 ─────────────────────────────────────────────────────────

def test_idem_key_ignores_punctuation_and_whitespace():
    """「查一下固态电池」与「查一下 固态电池。」是同一个任务——连说两遍不该双跑。"""
    a = idem_key("u1", "research", "深入调研固态电池现状")
    b = idem_key("u1", "research", " 深入调研，固态电池现状。 ")
    assert a == b and len(a) == 16


def test_idem_key_separates_user_kind_and_goal():
    base = idem_key("u1", "research", "固态电池")
    assert idem_key("u2", "research", "固态电池") != base     # 换人
    assert idem_key("u1", "trip", "固态电池") != base         # 换类型
    assert idem_key("u1", "research", "钠离子电池") != base    # 换主题


def test_normalize_goal_keeps_content_chars():
    assert normalize_goal("A/B 对比！") == "a/b对比"


# ── 纯函数：预算 ───────────────────────────────────────────────────────────

def test_budget_exhausted_none_when_unset():
    """弱声明不制造误截停：没设上限 = 不限。"""
    assert budget_exhausted({}) == ""
    assert budget_exhausted(None) == ""
    assert budget_exhausted({"llm_calls_used": 99}) == ""


def test_budget_exhausted_deadline():
    now = 1_000_000.0
    assert budget_exhausted({"deadline_ts": now - 1}, now=now) == STOP_DEADLINE
    assert budget_exhausted({"deadline_ts": now + 10}, now=now) == ""


def test_budget_exhausted_call_caps():
    assert budget_exhausted({"llm_calls_max": 4, "llm_calls_used": 4}) == STOP_BUDGET
    assert budget_exhausted({"ext_calls_max": 20, "ext_calls_used": 21}) == STOP_BUDGET
    assert budget_exhausted({"llm_calls_max": 4, "llm_calls_used": 3}) == ""


def test_budget_exhausted_ignores_garbage_caps():
    assert budget_exhausted({"llm_calls_max": "abc", "llm_calls_used": 99}) == ""
    assert budget_exhausted({"deadline_ts": "nope"}) == ""


def test_merge_used_accumulates_and_keeps_ints():
    b = merge_used({"llm_calls_max": 4}, {"llm_calls": 1})
    assert b == {"llm_calls_max": 4, "llm_calls_used": 1}
    b = merge_used(b, {"llm_calls_used": 2, "ext_calls": 3})
    assert b["llm_calls_used"] == 3 and b["ext_calls_used"] == 3
    assert isinstance(b["llm_calls_used"], int)      # 整数不变成 3.0（进 JSONB 好看）


def test_merge_used_never_touches_caps():
    b = merge_used({"llm_calls_max": 4, "deadline_ts": 123}, {"llm_calls": 1})
    assert b["llm_calls_max"] == 4 and b["deadline_ts"] == 123


# ── 纯函数：orphan 惰性判定 ───────────────────────────────────────────────

def test_is_orphaned_only_for_active_states():
    old = time.time() - 10_000
    for st in TERMINAL + (ORPHANED,):
        assert not is_orphaned(st, old, old, ttl=90)
    for st in ACTIVE:
        assert is_orphaned(st, old, old, ttl=90)


def test_is_orphaned_uses_created_at_when_never_beat():
    """刚 accepted 就崩：从没心跳过，以 created_at 为参照才判得出来。"""
    now = 1_000_000.0
    assert is_orphaned(ACCEPTED, 0.0, now - 200, now=now, ttl=90)
    assert not is_orphaned(ACCEPTED, 0.0, now - 10, now=now, ttl=90)


def test_is_orphaned_fresh_heartbeat_not_convicted():
    now = 1_000_000.0
    assert not is_orphaned(RUNNING, now - 5, now - 5000, now=now, ttl=90)


def test_orphan_ttl_env_override(monkeypatch):
    from agents._sdk.ledger import orphan_ttl
    monkeypatch.setenv("LEDGER_ORPHAN_TTL_S", "30")
    assert orphan_ttl() == 30
    monkeypatch.setenv("LEDGER_ORPHAN_TTL_S", "garbage")
    assert orphan_ttl() == 90       # 垃圾值回默认，不炸


# ── row → dataclass ───────────────────────────────────────────────────────

def _row(**over) -> dict:
    row = {"task_id": "t1", "user_id": "u1", "session_id": "s1",
           "agent_id": "deep-research", "kind": "research", "goal": "固态电池",
           "idempotency_key": "abc", "status": RUNNING, "progress": "检索中",
           "budget": {}, "result_ref": {}, "origin_trace_id": "tr1",
           "heartbeat_at": None, "created_at": None, "updated_at": None}
    row.update(over)
    return row


def test_row_to_task_parses_jsonb_string():
    """asyncpg 在没注册 codec 时 JSONB 回来是 str——不解析会让预算全程失效。"""
    task = row_to_task(_row(budget=json.dumps({"llm_calls_max": 4}),
                            result_ref='{"card":"x"}'))
    assert task.budget == {"llm_calls_max": 4} and task.result_ref == {"card": "x"}


def test_row_to_task_tolerates_bad_json():
    task = row_to_task(_row(budget="not json", result_ref=None))
    assert task.budget == {} and task.result_ref == {}


def test_stop_reason_and_active_helpers():
    task = row_to_task(_row(status=CANCELLED, budget={"stop_reason": STOP_DEADLINE}))
    assert task.stop_reason == STOP_DEADLINE and not task.active
    assert row_to_task(_row(status=ACCEPTED)).active


# ── PG 不可达：全面诚实降级 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_dsn_degrades_silently():
    """账本是增强不是执行依赖：拿不到 PG 时不炸、不阻塞，Agent 照常干活。"""
    led = TaskLedger(dsn="")
    assert await led.init() is False and led.pg_ok is False
    assert await led.open("u1", "s1", "deep-research", "research", "x") is None
    assert await led.query_active("u1") == []
    assert await led.recent("u1") == []
    assert await led.get("t1") is None
    assert await led.close("t1", DONE) is False
    assert await led.cancel("t1") is False
    # 心跳返回 running：账本挂了不能反过来误杀正在跑的任务
    assert await led.heartbeat("t1") == RUNNING


@pytest.mark.asyncio
async def test_close_rejects_non_terminal_status():
    led = TaskLedger(dsn="")
    with pytest.raises(ValueError):
        await led.close("t1", RUNNING)


# ── 分支接线：可编程假连接 ────────────────────────────────────────────────

class _FakeConn:
    """按调用序回放 fetchrow/fetch 结果，并记录所有 SQL 与参数。"""

    def __init__(self, fetchrow=None, fetch=None, execute_tag="UPDATE 1"):
        self._fetchrow = list(fetchrow or [])
        self._fetch = list(fetch or [])
        self._execute_tag = execute_tag
        self.sql: list[tuple] = []

    async def fetchrow(self, q, *args):
        self.sql.append((q, args))
        return self._fetchrow.pop(0) if self._fetchrow else None

    async def fetch(self, q, *args):
        self.sql.append((q, args))
        return self._fetch.pop(0) if self._fetch else []

    async def execute(self, q, *args):
        self.sql.append((q, args))
        return self._execute_tag


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


def _ledger(conn) -> TaskLedger:
    led = TaskLedger(dsn="postgres://fake")
    led._pool = _FakePool(conn)
    led._pg_ok = True
    led._init_done = True
    return led


@pytest.mark.asyncio
async def test_open_returns_duplicate_on_active_same_goal():
    """连说两遍「慢慢查固态电池」→ 第二遍拿到 Duplicate，不重复开跑。"""
    now = time.time()
    conn = _FakeConn(fetchrow=[_row(task_id="t-old", heartbeat_at=now - 3,
                                    created_at=now - 30)])
    led = _ledger(conn)
    res = await led.open("u1", "s1", "deep-research", "research", "固态电池")
    assert isinstance(res, Duplicate) and res.existing.task_id == "t-old"
    assert len(conn.sql) == 1                       # 只查不插
    assert "INSERT" not in conn.sql[0][0]


@pytest.mark.asyncio
async def test_open_reclaims_orphan_and_proceeds():
    """幂等命中的是上次崩掉的尸体 → 就地改判 orphaned 后放行新开单，
    否则用户被一条永不销单的任务永久挡住重试。"""
    stale = time.time() - 10_000
    conn = _FakeConn(fetchrow=[_row(task_id="t-dead", status=RUNNING,
                                    heartbeat_at=stale, created_at=stale),
                               _row(task_id="t-new", status=ACCEPTED)])
    led = _ledger(conn)
    res = await led.open("u1", "s1", "deep-research", "research", "固态电池")
    assert isinstance(res, LedgerTask) and res.task_id == "t-new"
    kinds = [q.split()[0] for q, _ in conn.sql]
    assert kinds == ["SELECT", "UPDATE", "INSERT"]   # 查 → 改判尸体 → 插新单


@pytest.mark.asyncio
async def test_open_generates_uuid_task_id_not_object_id():
    """task_id 必须 uuid4：id(obj) 会因 GC 地址复用撞键（corr_id 老教训）。"""
    conn = _FakeConn(fetchrow=[None, _row(task_id="t-new")])
    led = _ledger(conn)
    await led.open("u1", "s1", "deep-research", "research", "固态电池")
    insert_args = conn.sql[-1][1]
    assert len(insert_args[0]) == 32 and insert_args[0].isalnum()


@pytest.mark.asyncio
async def test_open_failure_degrades_to_none():
    class _Boom(_FakeConn):
        async def fetchrow(self, q, *args):
            raise RuntimeError("pg down mid-flight")

    led = _ledger(_Boom())
    assert await led.open("u1", "s1", "a", "research", "x") is None


@pytest.mark.asyncio
async def test_heartbeat_returns_cancelled_when_row_not_advanceable():
    """拉模式的核心：用户 cancel 后行已是 cancelled，UPDATE 不匹配 → 回报 cancelled，
    后台任务据此自行收尾。"""
    conn = _FakeConn(fetchrow=[None, {"status": CANCELLED}])
    assert await _ledger(conn).heartbeat("t1") == CANCELLED


@pytest.mark.asyncio
async def test_heartbeat_missing_row_reports_cancelled():
    """行都没了（被清理/从没开成）→ 当作取消收尾，不空转。"""
    conn = _FakeConn(fetchrow=[None, None])
    assert await _ledger(conn).heartbeat("t1") == CANCELLED


@pytest.mark.asyncio
async def test_heartbeat_resurrects_orphan_to_running():
    """迟到心跳复活：orphaned 是判定不是结局，误判不该变成假的中断报告。"""
    conn = _FakeConn(fetchrow=[_row(status=RUNNING)])
    led = _ledger(conn)
    assert await led.heartbeat("t1", progress="检索中 3/9") == RUNNING
    q, args = conn.sql[0]
    assert "status=ANY($4)" in q and ORPHANED in args[3]   # orphaned 也在可推进集合里


@pytest.mark.asyncio
async def test_heartbeat_enforces_budget_and_stops():
    """预算强制（Background 守卫③）：累加后超上限 → 就地 cancelled + 写 stop_reason。"""
    conn = _FakeConn(fetchrow=[_row(budget={"llm_calls_max": 2,
                                            "llm_calls_used": 1})])
    led = _ledger(conn)
    assert await led.heartbeat("t1", used={"llm_calls": 1}) == CANCELLED
    stop_sql, stop_args = conn.sql[-1]
    assert "status=$2" in stop_sql and stop_args[1] == CANCELLED
    assert json.loads(stop_args[2])["stop_reason"] == STOP_BUDGET


@pytest.mark.asyncio
async def test_heartbeat_deadline_stops_with_deadline_reason():
    """超期与用户取消都返回 cancelled，靠 stop_reason 区分「超时停了」话术。"""
    conn = _FakeConn(fetchrow=[_row(budget={"deadline_ts": time.time() - 1})])
    led = _ledger(conn)
    assert await led.heartbeat("t1") == CANCELLED
    assert json.loads(conn.sql[-1][1][2])["stop_reason"] == STOP_DEADLINE


@pytest.mark.asyncio
async def test_heartbeat_under_budget_persists_usage():
    conn = _FakeConn(fetchrow=[_row(budget={"llm_calls_max": 9,
                                            "llm_calls_used": 1})])
    led = _ledger(conn)
    assert await led.heartbeat("t1", used={"llm_calls": 1}) == RUNNING
    assert json.loads(conn.sql[-1][1][1])["llm_calls_used"] == 2


@pytest.mark.asyncio
async def test_heartbeat_error_keeps_task_running():
    class _Boom(_FakeConn):
        async def fetchrow(self, q, *args):
            raise RuntimeError("pg blip")

    assert await _ledger(_Boom()).heartbeat("t1") == RUNNING


@pytest.mark.asyncio
async def test_close_is_terminal_guarded():
    """终态不可逆：SQL 里必须带 status<>ALL(TERMINAL)，否则完成的任务会被迟到的
    失败回调改写成 failed。"""
    conn = _FakeConn(execute_tag="UPDATE 1")
    led = _ledger(conn)
    assert await led.close("t1", DONE, result_ref={"sections": 9}) is True
    q, args = conn.sql[0]
    assert "status<>ALL($5)" in q
    assert set(args[4]) == set(TERMINAL)
    assert json.loads(args[2]) == {"sections": 9}


@pytest.mark.asyncio
async def test_close_reports_false_when_already_terminal():
    assert await _ledger(_FakeConn(execute_tag="UPDATE 0")).close("t1", FAILED) is False


@pytest.mark.asyncio
async def test_cancel_writes_stop_reason_and_guards_terminal():
    conn = _FakeConn(execute_tag="UPDATE 1")
    led = _ledger(conn)
    assert await led.cancel("t1") is True
    q, args = conn.sql[0]
    assert "stop_reason" in q and args[2] == STOP_USER
    assert set(args[3]) == set(TERMINAL)


@pytest.mark.asyncio
async def test_query_active_drops_orphans_lazily():
    """查询侧惰性判定：孤儿不该出现在「还在跑」名单里（否则用户问进度得到假的在跑）。"""
    fresh, stale = time.time() - 2, time.time() - 10_000
    conn = _FakeConn(fetch=[[_row(task_id="alive", heartbeat_at=fresh,
                                  created_at=fresh),
                             _row(task_id="dead", heartbeat_at=stale,
                                  created_at=stale)]])
    tasks = await _ledger(conn).query_active("u1")
    assert [t.task_id for t in tasks] == ["alive"]
    assert any("SET status=$2" in q and ORPHANED in a for q, a in conn.sql[1:])


@pytest.mark.asyncio
async def test_recent_keeps_orphan_but_relabels():
    """「刚才那个调研怎么样了」要能答出「查到一半中断了」——recent 保留孤儿但改标。"""
    stale = time.time() - 10_000
    conn = _FakeConn(fetch=[[_row(task_id="dead", heartbeat_at=stale,
                                  created_at=stale)]])
    tasks = await _ledger(conn).recent("u1")
    assert len(tasks) == 1 and tasks[0].status == ORPHANED


@pytest.mark.asyncio
async def test_get_relabels_orphan():
    stale = time.time() - 10_000
    conn = _FakeConn(fetchrow=[_row(heartbeat_at=stale, created_at=stale)])
    task = await _ledger(conn).get("t1")
    assert task is not None and task.status == ORPHANED


@pytest.mark.asyncio
async def test_mark_orphaned_reconfirms_ttl_in_where():
    """并发心跳刚好落在读与写之间时，UPDATE 的 WHERE 再钉一次 TTL → 不误改判。"""
    conn = _FakeConn()
    await TaskLedger._mark_orphaned(conn, "t1")
    q, args = conn.sql[0]
    assert "heartbeat_at, created_at" in q and "seconds" in q
    assert set(args[2]) == set(ACTIVE)

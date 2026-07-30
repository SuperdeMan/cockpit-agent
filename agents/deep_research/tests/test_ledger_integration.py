"""deep-research × Task Ledger 接入契约测试（M2 P0）。

覆盖 RFC §2.6 的四条用户可见语义：幂等受理（连说两遍不双跑）、状态确定性直答、
拉模式取消、中断（orphaned）诚实报告；外加「无账本诚实降级」——账本挂了不能让
Agent 假装承诺可停可问。

账本用假实现注入（不连 PG）：真 SQL 语义由 `agents/_sdk/tests/test_ledger.py` 的分支
接线测试 + 真栈 e2e 兜。
"""
import asyncio
import time

import pytest

from agents._sdk.ledger import (
    ACCEPTED, CANCELLED, DONE, FAILED as LEDGER_FAILED, ORPHANED, RUNNING,
    STOP_BUDGET, STOP_DEADLINE, STOP_USER, Duplicate, LedgerTask,
)
from agents._sdk.testing import make_context, run_handle
from agents.deep_research.src.agent import DeepResearchAgent


class _FakeLedger:
    """内存假账本：语义与 TaskLedger 对齐，供 Agent 侧话术/分支测试。"""

    def __init__(self, *, pg_ok=True, active=None, recent=None):
        self.pg_ok = pg_ok
        self._active = list(active or [])
        self._recent = list(recent or [])
        self.opened: list[dict] = []
        self.cancelled: list[str] = []
        self.closed: list[tuple] = []
        self.beats: list[tuple] = []
        self.duplicate: LedgerTask | None = None
        self.heartbeat_returns = RUNNING
        self.get_returns: LedgerTask | None = None

    async def init(self):
        return self.pg_ok

    async def open(self, user_id, session_id, agent_id, kind, goal, *,
                   budget=None, origin_trace_id="", idempotency_goal=""):
        self.opened.append({"user_id": user_id, "kind": kind, "goal": goal,
                            "budget": budget or {},
                            "idempotency_goal": idempotency_goal})
        if not self.pg_ok:
            return None
        if self.duplicate is not None:
            return Duplicate(existing=self.duplicate)
        return LedgerTask(task_id="t-new", user_id=user_id, goal=goal,
                          kind=kind, status=ACCEPTED, created_at=time.time())

    async def heartbeat(self, task_id, *, progress="", used=None):
        self.beats.append((task_id, progress, used))
        return self.heartbeat_returns

    async def close(self, task_id, status, *, result_ref=None, progress=""):
        self.closed.append((task_id, status, result_ref, progress))
        return True

    async def cancel(self, task_id, *, reason=STOP_USER):
        self.cancelled.append(task_id)
        return True

    async def query_active(self, user_id, *, kind="", limit=10):
        return list(self._active)

    async def recent(self, user_id, *, kind="", limit=5):
        return list(self._recent)

    async def get(self, task_id):
        return self.get_returns


def _task(**over) -> LedgerTask:
    base = {"task_id": "t1", "user_id": "u1", "kind": "research",
            "goal": "固态电池现状", "status": RUNNING, "progress": "",
            "created_at": time.time() - 120}
    base.update(over)
    return LedgerTask(**base)


def _agent(ledger=None) -> DeepResearchAgent:
    agent = DeepResearchAgent()
    agent.ledger = ledger if ledger is not None else _FakeLedger()
    return agent


# ── 受理：开单 / 幂等 / 降级 ──────────────────────────────────────────────

def test_kickoff_opens_ledger_and_promises_control():
    """开单成功才承诺「可停可问」——承诺得起才说。"""
    led = _FakeLedger()
    agent = _agent(led)
    agent._run_deep_async = lambda *a, **k: asyncio.sleep(0)   # 不真跑流水线
    res = asyncio.run(run_handle(agent, "research.run", slots={"query": "固态电池"},
                                 raw_text="慢慢查一下固态电池，查完告诉我"))
    assert res.status == "ok" and res.data["task_id"] == "t-new"
    assert "别查了" in res.follow_up and "怎么样了" in res.follow_up
    assert led.opened and led.opened[0]["kind"] == "research"


def test_kickoff_uses_raw_request_for_stable_idempotency():
    """同一句原话的 Planner query 即使抽取有波动，也不能重复开后台任务。"""
    led = _FakeLedger()
    agent = _agent(led)
    agent._run_deep_async = lambda *a, **k: asyncio.sleep(0)
    raw = "慢慢查一下钠离子电池的产业化进展，查完告诉我"
    asyncio.run(run_handle(
        agent,
        "research.run",
        slots={"query": "钠离子电池产业化进展"},
        raw_text=raw,
    ))

    assert led.opened[0]["goal"] == "钠离子电池产业化进展"
    assert led.opened[0]["idempotency_goal"] == raw


def test_kickoff_budget_carries_deadline_and_caps():
    """Background 守卫①③：deadline 与调用次数上限在开单时就写进账本，不是空头支票。"""
    led = _FakeLedger()
    agent = _agent(led)
    agent._run_deep_async = lambda *a, **k: asyncio.sleep(0)
    asyncio.run(run_handle(agent, "research.run", slots={"query": "固态电池"},
                           raw_text="慢慢查一下固态电池"))
    budget = led.opened[0]["budget"]
    assert budget["deadline_ts"] > time.time()
    assert budget["llm_calls_max"] > 0 and budget["ext_calls_max"] > 0


def test_kickoff_duplicate_does_not_double_run():
    """连说两遍：第二遍出「已经在查了」并**不 spawn 第二个后台任务**。"""
    led = _FakeLedger()
    led.duplicate = _task(progress="检索中 3/9 个子问题")
    agent = _agent(led)
    spawned = []
    agent._run_deep_async = lambda *a, **k: spawned.append(1) or asyncio.sleep(0)
    res = asyncio.run(run_handle(agent, "research.run", slots={"query": "固态电池"},
                                 raw_text="慢慢查一下固态电池，查完告诉我"))
    assert res.status == "ok" and res.data.get("duplicate") is True
    assert "已经在查了" in res.speech and "检索中 3/9" in res.speech
    assert not spawned


def test_kickoff_without_ledger_degrades_honestly():
    """无 PG：照常受理照常跑，但话术**不承诺**可取消/可查询（诚实降级）。"""
    led = _FakeLedger(pg_ok=False)
    agent = _agent(led)
    agent._run_deep_async = lambda *a, **k: asyncio.sleep(0)
    res = asyncio.run(run_handle(agent, "research.run", slots={"query": "固态电池"},
                                 raw_text="慢慢查一下固态电池，查完告诉我"))
    assert res.status == "ok" and res.data["task_id"] == ""
    assert "别查了" not in res.follow_up and "怎么样了" not in res.follow_up


# ── 状态查询：确定性直答 ─────────────────────────────────────────────────

def test_status_active_reports_real_progress():
    """任务状态是**系统持有的事实**：从账本读后确定性作答，不进 LLM。"""
    led = _FakeLedger(active=[_task(progress="检索中 5/9 个子问题")])
    res = asyncio.run(run_handle(_agent(led), "research.status",
                                 raw_text="那个调研查得怎么样了"))
    assert res.status == "ok"
    assert "还在查" in res.speech and "检索中 5/9" in res.speech
    assert "分钟" in res.speech          # 已跑时长
    assert "别查了" in res.follow_up


def test_status_orphaned_is_honest_about_interruption():
    """中断的诚实报告（RFC §2.5）：不假装还在跑，也不假装查完了。"""
    led = _FakeLedger(recent=[_task(status=ORPHANED)])
    res = asyncio.run(run_handle(_agent(led), "research.status",
                                 raw_text="刚才那个调研怎么样了"))
    assert "中断" in res.speech and "重新查" in res.speech


def test_status_done_gives_summary():
    led = _FakeLedger(recent=[_task(status=DONE,
                                    result_ref={"summary": "固态电池 2027 量产。"})])
    res = asyncio.run(run_handle(_agent(led), "research.status", raw_text="调研好了吗"))
    assert "已经查完" in res.speech and "2027" in res.speech


@pytest.mark.parametrize("reason,expect", [
    (STOP_USER, "按你说的停"),
    (STOP_DEADLINE, "超时停了"),
    (STOP_BUDGET, "预算上限"),
])
def test_status_cancelled_distinguishes_stop_reason(reason, expect):
    """三种停法话术要能区分——用户主动停 vs 超时 vs 预算用尽是三件事。"""
    led = _FakeLedger(recent=[_task(status=CANCELLED,
                                    budget={"stop_reason": reason})])
    res = asyncio.run(run_handle(_agent(led), "research.status", raw_text="调研怎么样了"))
    assert expect in res.speech


def test_status_failed_reports_failure():
    led = _FakeLedger(recent=[_task(status=LEDGER_FAILED)])
    res = asyncio.run(run_handle(_agent(led), "research.status", raw_text="调研怎么样了"))
    assert "没能查完" in res.speech


def test_status_no_task_says_so():
    res = asyncio.run(run_handle(_agent(_FakeLedger()), "research.status",
                                 raw_text="调研怎么样了"))
    assert "没让我查过" in res.speech


def test_status_without_ledger_says_unknown_not_fake_progress():
    res = asyncio.run(run_handle(_agent(_FakeLedger(pg_ok=False)), "research.status",
                                 raw_text="调研怎么样了"))
    assert res.status == "ok"           # R9：话术型降级走 OK，不用 FAILED
    assert "查不到" in res.speech


# ── 取消：拉模式 ─────────────────────────────────────────────────────────

def test_cancel_single_active_sets_cancelled():
    led = _FakeLedger(active=[_task()])
    res = asyncio.run(run_handle(_agent(led), "research.cancel", raw_text="别查了"))
    assert res.status == "ok" and led.cancelled == ["t1"]
    assert "正在停下" in res.speech and res.data["cancelled"] is True


def test_cancel_nothing_running():
    res = asyncio.run(run_handle(_agent(_FakeLedger()), "research.cancel",
                                 raw_text="别查了"))
    assert "没有正在跑的调研" in res.speech


def test_cancel_multiple_asks_which_one():
    """多条在跑且原话没指名 → 反问，不猜也不一次全停（停错要重等几分钟）。"""
    led = _FakeLedger(active=[_task(task_id="a", goal="固态电池现状"),
                              _task(task_id="b", goal="港股打新规则")])
    res = asyncio.run(run_handle(_agent(led), "research.cancel", raw_text="别查了"))
    assert res.status == "need_slot" and not led.cancelled
    assert "固态电池" in res.speech and "港股打新" in res.speech


def test_cancel_multiple_disambiguated_by_topic_in_utterance():
    led = _FakeLedger(active=[_task(task_id="a", goal="固态电池现状"),
                              _task(task_id="b", goal="港股打新规则")])
    res = asyncio.run(run_handle(_agent(led), "research.cancel",
                                 raw_text="固态电池那个调研不用查了"))
    assert res.status == "ok" and led.cancelled == ["a"]


def test_cancel_without_ledger_degrades_honestly():
    res = asyncio.run(run_handle(_agent(_FakeLedger(pg_ok=False)), "research.cancel",
                                 raw_text="别查了"))
    assert res.status == "ok" and "管不了" in res.speech


# ── 后台流水线：心跳 / 拉模式截停 / 销单 ─────────────────────────────────

def test_background_stops_at_first_heartbeat_when_cancelled():
    """拉模式生效点：心跳读到 cancelled → 立刻收尾，不再跑后续阶段。"""
    led = _FakeLedger()
    led.heartbeat_returns = CANCELLED
    led.get_returns = _task(status=CANCELLED, budget={"stop_reason": STOP_USER})
    agent = _agent(led)
    published = []
    agent._publish_research_stopped = lambda *a: published.append(a) or asyncio.sleep(0)

    async def _boom(*a, **k):
        raise AssertionError("cancelled 后不应继续跑流水线")

    import agents.deep_research.src.agent as mod
    orig = mod.plan
    mod.plan = _boom
    try:
        asyncio.run(agent._run_deep_async("固态电池", {}, "s1", "u1", "v1", {}, "t1"))
    finally:
        mod.plan = orig
    assert led.beats and not published        # 用户主动停：不再补播（他刚说完别查了）


def test_background_deadline_stop_publishes_notice():
    """超时/预算截停用户没喊过停 → 必须主动告知，静默消失是不诚实的。"""
    led = _FakeLedger()
    led.heartbeat_returns = CANCELLED
    led.get_returns = _task(status=CANCELLED, budget={"stop_reason": STOP_DEADLINE})
    agent = _agent(led)
    published = []

    async def _pub(question, reason, ref=None):
        published.append(reason)
        assert (ref or {}).get("task_id") == "t1"   # 任务级归因随推送带回（RFC §5 obs）
    agent._publish_research_stopped = _pub

    import agents.deep_research.src.agent as mod
    orig = mod.plan

    async def _boom(*a, **k):
        raise AssertionError("cancelled 后不应继续跑流水线")
    mod.plan = _boom
    try:
        asyncio.run(agent._run_deep_async("固态电池", {}, "s1", "u1", "v1", {}, "t1"))
    finally:
        mod.plan = orig
    assert published == [STOP_DEADLINE]


def test_background_closes_ledger_done_with_result_ref():
    """完成即销单，且**先销单再推送**——推送失败不该让账本停在 running 变假孤儿。"""
    led = _FakeLedger()
    agent = _agent(led)
    order = []

    class _Report:
        sections = [object()]
        sources = [{"idx": 1}]
        summary = "核心结论。"

    import agents.deep_research.src.agent as mod
    saved = (mod.plan, mod.investigate, mod.synthesize, mod.brief)

    async def _plan(*a, **k):
        return []

    async def _investigate(*a, **k):
        return None

    async def _synthesize(*a, **k):
        return _Report()

    mod.plan, mod.investigate, mod.synthesize = _plan, _investigate, _synthesize
    mod.brief = lambda report, question="": ("简报", {"type": "research_report"})
    agent._save_task = lambda *a, **k: asyncio.sleep(0)

    async def _pub(*a, **k):
        order.append("publish")
    agent._publish_research_done = _pub
    led_close = led.close

    async def _close(*a, **k):
        order.append("close")
        return await led_close(*a, **k)
    led.close = _close
    try:
        asyncio.run(agent._run_deep_async("固态电池", {}, "s1", "u1", "v1", {}, "t1"))
    finally:
        mod.plan, mod.investigate, mod.synthesize, mod.brief = saved
    assert order == ["close", "publish"]
    task_id, status, result_ref, _ = led.closed[0]
    assert task_id == "t1" and status == DONE
    assert result_ref["sections"] == 1 and result_ref["summary"] == "核心结论。"


def test_background_closes_ledger_failed_on_exception():
    led = _FakeLedger()
    agent = _agent(led)
    agent._publish_research_failed = lambda *a, **k: asyncio.sleep(0)

    import agents.deep_research.src.agent as mod
    orig = mod.plan

    async def _boom(*a, **k):
        raise RuntimeError("llm down")
    mod.plan = _boom
    try:
        asyncio.run(agent._run_deep_async("固态电池", {}, "s1", "u1", "v1", {}, "t1"))
    finally:
        mod.plan = orig
    assert led.closed and led.closed[0][1] == LEDGER_FAILED


def test_background_without_task_id_never_touches_ledger():
    """无账本时后台任务行为与 M2 之前逐字一致（零心跳、零销单）。"""
    led = _FakeLedger()
    agent = _agent(led)
    agent._publish_research_failed = lambda *a, **k: asyncio.sleep(0)

    import agents.deep_research.src.agent as mod
    orig = mod.plan

    async def _boom(*a, **k):
        raise RuntimeError("llm down")
    mod.plan = _boom
    try:
        asyncio.run(agent._run_deep_async("固态电池", {}, "s1", "u1", "v1", {}, ""))
    finally:
        mod.plan = orig
    assert not led.beats and not led.closed

"""T2 分档预算 + 重复副作用防抖单测（M2 P2）。

两件事都以「Ledger 能中断长任务 + Verifier 能对账假完成」为前置（M2 P0/P1 已落）——
在那之前放宽预算等于把弱模型的失误放大成用户可见的重复副作用，故此处才敢动。
"""
import asyncio

import pytest

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.loop import LoopController, tier_budget
from orchestrator.cloud.models import (
    Plan, PlanContext, Step, StepResult, StepStatus,
)


# ── 分档预算 ─────────────────────────────────────────────────────────────

def test_tier_defaults(monkeypatch):
    """Interactive（simple 误入 T2 的兜底）比 Complex（条件依赖链）紧。"""
    for k in ("PLANNER_LOOP_MAX_ITERS", "PLANNER_LOOP_BUDGET_MS",
              "PLANNER_LOOP_MAX_ITERS_COMPLEX", "PLANNER_LOOP_BUDGET_MS_COMPLEX"):
        monkeypatch.delenv(k, raising=False)
    assert tier_budget("simple") == (2, 8000)
    assert tier_budget("adaptive") == (3, 12000)


def test_unknown_complexity_falls_back_to_interactive(monkeypatch):
    for k in ("PLANNER_LOOP_MAX_ITERS", "PLANNER_LOOP_BUDGET_MS"):
        monkeypatch.delenv(k, raising=False)
    assert tier_budget("") == (2, 8000)
    assert tier_budget("background") == (2, 8000)   # Background 归 Ledger，不占循环预算


def test_global_env_overrides_all_tiers(monkeypatch):
    """一键回退到放宽前（子 RFC §4.2「任一步红灯回退上一档 = 改一行 env」）。"""
    monkeypatch.setenv("PLANNER_LOOP_MAX_ITERS", "2")
    monkeypatch.setenv("PLANNER_LOOP_BUDGET_MS", "5000")
    assert tier_budget("simple") == (2, 5000)
    assert tier_budget("adaptive") == (2, 5000)


def test_per_tier_env_override(monkeypatch):
    monkeypatch.delenv("PLANNER_LOOP_MAX_ITERS", raising=False)
    monkeypatch.delenv("PLANNER_LOOP_BUDGET_MS", raising=False)
    monkeypatch.setenv("PLANNER_LOOP_MAX_ITERS_COMPLEX", "4")
    monkeypatch.setenv("PLANNER_LOOP_BUDGET_MS_COMPLEX", "15000")
    assert tier_budget("adaptive") == (4, 15000)
    assert tier_budget("simple") == (2, 8000)       # 分档 env 不外溢到别的档


def test_garbage_env_falls_back_to_tier(monkeypatch):
    monkeypatch.setenv("PLANNER_LOOP_MAX_ITERS", "abc")
    monkeypatch.delenv("PLANNER_LOOP_BUDGET_MS", raising=False)
    assert tier_budget("adaptive") == (3, 12000)


class _Planner:
    def __init__(self):
        self.calls = 0

    async def replan(self, *a, **k):
        from orchestrator.cloud.models import ReplanDecision
        self.calls += 1
        return ReplanDecision(done=False, steps=[
            Step(id=f"r{self.calls}", agent_id="a", intent="x.y",
                 latency_budget_ms=1000)])


class _Executor:
    async def run(self, plan, ctx, done=None):
        for s in plan.steps:
            yield StepResult(step_id=s.id, status=StepStatus.OK, speech="ok")


class _Aggregator:
    async def compose(self, text, results, thinking=False):
        return {"speech": "done", "actions": []}


def _drive(plan: Plan, monkeypatch) -> _Planner:
    for k in ("PLANNER_LOOP_MAX_ITERS", "PLANNER_LOOP_BUDGET_MS",
              "PLANNER_LOOP_MAX_ITERS_COMPLEX", "PLANNER_LOOP_BUDGET_MS_COMPLEX"):
        monkeypatch.delenv(k, raising=False)
    planner = _Planner()
    loop = LoopController(planner, _Executor(), _Aggregator(),
                          suspend_fn=None, clock=lambda: 0.0)

    async def _go():
        async for _ in loop.run("目标", plan, [], PlanContext(), "用户句"):
            pass
    asyncio.run(_go())
    return planner


def test_adaptive_plan_gets_complex_tier(monkeypatch):
    """条件依赖链（先查天气→再按结果补建提醒）在 3 次预算下才有第三步的空间。"""
    planner = _drive(Plan(steps=[], complexity="adaptive"), monkeypatch)
    assert planner.calls == 3


def test_simple_plan_gets_interactive_tier(monkeypatch):
    planner = _drive(Plan(steps=[], complexity="simple"), monkeypatch)
    assert planner.calls == 2


def test_explicit_ctor_args_pin_budget_over_tier(monkeypatch):
    """显式传参（既有测试与特殊接线）恒定优先于分档，避免分档改动波及它们。"""
    monkeypatch.delenv("PLANNER_LOOP_MAX_ITERS", raising=False)
    planner = _Planner()
    loop = LoopController(planner, _Executor(), _Aggregator(), suspend_fn=None,
                          max_iters=1, budget_ms=5000, clock=lambda: 0.0)

    async def _go():
        async for _ in loop.run("g", Plan(steps=[], complexity="adaptive"), [],
                                PlanContext(), "t"):
            pass
    asyncio.run(_go())
    assert planner.calls == 1


# ── 重复副作用防抖 ───────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=0, speech="已为您打开空调", actions=None):
        self.status = status
        self.speech = speech
        self.ui_card = None
        self.actions = actions or []
        self.follow_up = ""
        self.data = {}
        self.missing_slots = []


class _Action:
    def __init__(self, type_="vehicle.control.hvac"):
        self.type = type_
        self.payload = {"temp": 22}
        self.require_confirm = False


class _Dispatcher:
    def __init__(self, resp):
        self._resp = resp
        self.calls = 0

    async def dispatch(self, step, ctx):
        self.calls += 1
        return self._resp


def _step(sid="s1", intent="hvac.set", slots=None) -> Step:
    return Step(id=sid, agent_id="edge-vehicle", intent=intent,
                slots=slots if slots is not None else {"temp": "22"},
                latency_budget_ms=5000)


def _run_one(executor, step, done=None) -> StepResult:
    async def _go():
        out = []
        async for r in executor.run(Plan(steps=[step]), PlanContext(), done=done):
            out.append(r)
        return out[0]
    return asyncio.run(_go())


def test_side_effect_result_gets_fingerprint():
    d = _Dispatcher(_Resp(actions=[_Action()]))
    res = _run_one(DagExecutor(dispatcher=d), _step())
    assert res.fingerprint and res.actions


def test_query_result_gets_no_fingerprint():
    """纯查询步不参与防抖：重复查天气无害，还可能正是要刷新。"""
    d = _Dispatcher(_Resp(speech="今天晴", actions=[]))
    res = _run_one(DagExecutor(dispatcher=d), _step(intent="info.weather"))
    assert res.fingerprint == ""


def test_repeat_side_effect_is_suppressed():
    """replan 对已完成的副作用步失忆而重复产出 → 回填不重发（空调不会被开两次）。"""
    d = _Dispatcher(_Resp(actions=[_Action()]))
    first = _run_one(DagExecutor(dispatcher=d), _step("s1"))
    assert d.calls == 1 and first.actions

    second = _run_one(DagExecutor(dispatcher=d), _step("r1"),
                      done={"s1": first})
    assert d.calls == 1                      # 没有第二次下发
    assert second.status == StepStatus.OK
    assert second.actions == []              # ← 动作不重放，这正是要防的
    assert second.speech == first.speech     # 话术沿用，用户听到的一致
    assert second.step_id == "r1"


def test_different_slots_are_not_deduped():
    """「空调 22 度」和「空调 26 度」是两件事，不能当重复。"""
    d = _Dispatcher(_Resp(actions=[_Action()]))
    first = _run_one(DagExecutor(dispatcher=d), _step("s1", slots={"temp": "22"}))
    _run_one(DagExecutor(dispatcher=d), _step("r1", slots={"temp": "26"}),
             done={"s1": first})
    assert d.calls == 2


def test_different_intent_not_deduped():
    d = _Dispatcher(_Resp(actions=[_Action()]))
    first = _run_one(DagExecutor(dispatcher=d), _step("s1", intent="hvac.set"))
    _run_one(DagExecutor(dispatcher=d), _step("r1", intent="hvac.off"),
             done={"s1": first})
    assert d.calls == 2


def test_slot_order_does_not_affect_fingerprint():
    d = _Dispatcher(_Resp(actions=[_Action()]))
    first = _run_one(DagExecutor(dispatcher=d),
                     _step("s1", slots={"temp": "22", "zone": "main"}))
    _run_one(DagExecutor(dispatcher=d),
             _step("r1", slots={"zone": "main", "temp": "22"}), done={"s1": first})
    assert d.calls == 1


def test_failed_prior_is_not_deduped():
    """上次没成功 → 这次必须真跑（否则失败会被"复用"成假成功）。"""
    d = _Dispatcher(_Resp(actions=[_Action()]))
    prior = StepResult(step_id="s1", status=StepStatus.FAILED,
                       fingerprint=DagExecutor._fingerprint(_step("s1")))
    _run_one(DagExecutor(dispatcher=d), _step("r1"), done={"s1": prior})
    assert d.calls == 1


def test_dedup_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PLANNER_DEDUP_SIDE_EFFECTS", "off")
    d = _Dispatcher(_Resp(actions=[_Action()]))
    first = _run_one(DagExecutor(dispatcher=d), _step("s1"))
    _run_one(DagExecutor(dispatcher=d), _step("r1"), done={"s1": first})
    assert d.calls == 2


def test_fingerprint_is_slot_ref_aware():
    """指纹必须在 slot_refs 解析**之后**算——否则「导航去 $s1.data.name」两次会被
    误判成同一件事（槽位还是占位符时长得一样）。"""
    d = _Dispatcher(_Resp(actions=[_Action()]))
    prior_a = StepResult(step_id="p", status=StepStatus.OK,
                         data={"name": "北京南站"})
    prior_b = StepResult(step_id="p", status=StepStatus.OK,
                         data={"name": "上海虹桥"})
    s1 = Step(id="s1", agent_id="nav", intent="navigation.navigate_to",
              slot_refs={"destination": "p.data.name"}, latency_budget_ms=5000)
    s2 = Step(id="s2", agent_id="nav", intent="navigation.navigate_to",
              slot_refs={"destination": "p.data.name"}, latency_budget_ms=5000)
    r1 = _run_one(DagExecutor(dispatcher=d), s1, done={"p": prior_a})
    _run_one(DagExecutor(dispatcher=d), s2, done={"p": prior_b, "s1": r1})
    assert d.calls == 2          # 目的地不同 → 不是重复

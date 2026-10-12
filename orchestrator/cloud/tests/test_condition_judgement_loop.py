"""T2 里的条件指令前件判定（CA2-10 ② 第二步，2026-10-12，设计 docs/design/2026-10-12-conditional-instruction-judgement.md）。

首轮查了天气之后判一次前件：成立 ⇒ 再规划派出、被问句闸拦下的写步里只放行后件点名的那几步；不成立 / 判不了 ⇒ 一步不放，
终答末尾确定性说明为什么没做。2026-10-12 让再规划顺带决定时，它看到「今天不会下雪」照样开了空调（D02 0/3）。
"""
from __future__ import annotations

import asyncio
import json

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import Plan, PlanContext, ReplanDecision, Step, StepResult, StepStatus
from runtime import condition_judgement as cj

D01 = "如果深圳今天不下雪，就把空调打开"
D02 = "如果深圳今天下雪，就把空调打开"
D04 = "如果深圳今天下雪，就帮我把空调打开"
WEATHER_SPEECH = "深圳今天不会下雪，当前晴，气温26℃。"


async def _unused_call(*_args, **_kwargs):      # pragma: no cover - 只为满足构造契约
    raise AssertionError("替身不该真的调 Agent")


class _Planner:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.observations = []

    async def replan(self, goal, observations, agents, ctx, **_kwargs):
        self.observations.append(list(observations))
        return self.decisions.pop(0) if self.decisions else ReplanDecision(done=True, steps=[])


class _Executor(DagExecutor):
    def __init__(self, results_by_step):
        super().__init__(call_agent_fn=_unused_call)
        self.results_by_step = results_by_step
        self.runs = []
        self.plans = []

    async def run(self, plan, ctx, done=None):
        self.runs.append([step.id for step in plan.steps])
        self.plans.append(plan)
        for step in plan.steps:
            yield self.results_by_step[step.id]


class _Aggregator:
    async def compose(self, text, results, **kwargs):
        return {"speech": "深圳今天不会下雪", "actions": [], "cards": []}


class _Judge:
    def __init__(self, reply="", delay=0.0):
        self.reply = reply
        self.delay = delay
        self.calls = []

    async def __call__(self, messages):
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


def _verdict(verdict, evidence):
    return json.dumps({"verdict": verdict, "evidence": evidence}, ensure_ascii=False)


def _edge(step_id, intent, description, *, confirm=False):
    return Step(id=step_id, agent_id="edge-vehicle", intent=intent, deployment="edge", kind="edge_fast",
                require_confirm=confirm, capability_description=description)


def _run(origin, replan_steps, judge, *, more=None):
    planner = _Planner([ReplanDecision(done=False, steps=replan_steps), *(more or [])])
    results = {"s1": StepResult("s1", StepStatus.OK, speech=WEATHER_SPEECH,
                                data={"city": "深圳", "condition": "晴"})}
    results.update({s.id: StepResult(s.id, StepStatus.OK, speech="已为您打开空调") for s in replan_steps})
    executor = _Executor(results)
    controller = LoopController(planner, executor, _Aggregator(), None, max_iters=2, budget_ms=5000,
                                condition_judge=judge)
    initial = Plan(steps=[Step(id="s1", agent_id="info", intent="info.weather", slots={"city": "深圳"})],
                   complexity="adaptive", safety_origin_text=origin)
    ctx = PlanContext(raw_text=origin, safety_origin_text=origin)

    async def collect():
        return [event async for event in controller.run(
            goal="LLM goal", initial_plan=initial, agents=[], ctx=ctx, user_text=origin)]

    events = asyncio.run(collect())
    return planner, executor, events


def test_condition_holds_releases_the_consequent_the_user_named():
    judge = _Judge(_verdict("holds", "深圳今天不会下雪"))
    hvac = _edge("r1", "hvac.on", "打开空调")
    planner, executor, events = _run(D01, [hvac], judge)

    assert executor.runs == [["s1"], ["r1"]]
    assert hvac.condition_released is True
    told = planner.observations[0][-1]                    # 再规划看到了判定结论
    assert told["intent"] == cj.CHECK_INTENT and told["data"]["verdict"] == cj.HOLDS
    assert "没有" not in events[-1]["speech"]
    assert len(judge.calls) == 1


def test_only_steps_named_by_the_consequent_are_released():
    judge = _Judge(_verdict("holds", "深圳今天不会下雪"))
    hvac, trunk = _edge("r1", "hvac.on", "打开空调"), _edge("r2", "trunk.open", "打开后备箱")
    _, executor, _ = _run(D01, [hvac, trunk], judge)

    assert executor.runs == [["s1"], ["r1"]]
    assert trunk.condition_released is False


def test_condition_fails_keeps_the_consequent_blocked_and_says_why():
    judge = _Judge(_verdict("fails", "深圳今天不会下雪"))
    hvac = _edge("r1", "hvac.on", "打开空调")
    planner, executor, events = _run(D02, [hvac], judge)

    assert executor.runs == [["s1"]]
    assert hvac.condition_released is False
    assert planner.observations[0][-1]["data"]["verdict"] == cj.FAILS
    assert events[-1]["kind"] == "final"
    assert events[-1]["speech"] == "深圳今天不会下雪。「深圳今天下雪」不成立，所以没有把空调打开。"


def test_the_polite_form_is_judged_the_same_way():
    judge = _Judge(_verdict("fails", "深圳今天不会下雪"))
    _, executor, events = _run(D04, [_edge("r1", "hvac.on", "打开空调")], judge)

    assert executor.runs == [["s1"]]
    assert events[-1]["speech"].endswith("「深圳今天下雪」不成立，所以没有把空调打开。")


def test_a_holds_with_made_up_evidence_is_unknown():
    judge = _Judge(_verdict("holds", "今天肯定不下雪"))
    hvac = _edge("r1", "hvac.on", "打开空调")
    _, executor, events = _run(D01, [hvac], judge)

    assert executor.runs == [["s1"]]
    assert events[-1]["speech"].endswith("我没法确认「深圳今天不下雪」，所以先没有把空调打开。")


def test_a_judge_that_times_out_releases_nothing(monkeypatch):
    monkeypatch.setenv("CONDITION_JUDGEMENT_TIMEOUT_S", "0.05")
    judge = _Judge(_verdict("holds", "深圳今天不会下雪"), delay=0.5)
    _, executor, events = _run(D01, [_edge("r1", "hvac.on", "打开空调")], judge)

    assert executor.runs == [["s1"]]
    assert "我没法确认" in events[-1]["speech"]


def test_the_condition_is_judged_once_per_turn():
    judge = _Judge(_verdict("holds", "深圳今天不会下雪"))
    more = [ReplanDecision(done=False, steps=[_edge("r2", "seat.heating.on", "打开座椅加热")])]
    planner, executor, _ = _run(D01, [_edge("r1", "hvac.on", "打开空调")], judge, more=more)

    assert len(judge.calls) == 1
    assert executor.runs == [["s1"], ["r1"]]               # 座椅加热不是后件点名的，照拦
    assert sum(1 for obs in planner.observations[-1] if obs.get("intent") == cj.CHECK_INTENT) == 1


def test_a_plain_request_is_not_judged():
    judge = _Judge(_verdict("holds", "深圳今天不会下雪"))
    _, executor, events = _run("把空调打开", [_edge("r1", "hvac.on", "打开空调")], judge)

    assert judge.calls == []
    assert executor.runs == [["s1"], ["r1"]]              # 不是提问也不是条件指令，本来就不拦
    assert events[-1]["speech"] == "深圳今天不会下雪"


def test_without_a_judge_the_consequent_stays_blocked_as_before():
    _, executor, events = _run(D01, [_edge("r1", "hvac.on", "打开空调")], None)

    assert executor.runs == [["s1"]]
    assert events[-1]["speech"] == "深圳今天不会下雪"

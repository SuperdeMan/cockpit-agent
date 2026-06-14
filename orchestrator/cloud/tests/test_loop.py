"""Bounded adaptive loop behavior."""
from __future__ import annotations

import asyncio

from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import (
    Plan, PlanContext, ReplanDecision, Step, StepResult, StepStatus,
)


class _Planner:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.observations = []

    async def replan(self, goal, observations, agents, ctx, granted_permissions=None):
        self.observations.append(list(observations))
        return self.decisions.pop(0)


class _Executor:
    def __init__(self, results_by_step):
        self.results_by_step = results_by_step
        self.runs = []
        self.done_seeds = []

    async def run(self, plan, ctx, done=None):
        self.runs.append([step.id for step in plan.steps])
        self.done_seeds.append(set((done or {}).keys()))
        for step in plan.steps:
            yield self.results_by_step[step.id]


class _Aggregator:
    def __init__(self):
        self.calls = []

    async def compose(self, text, results):
        self.calls.append((text, list(results)))
        return {"speech": "best effort", "actions": [], "cards": []}


def _collect(controller, **kwargs):
    async def run():
        return [event async for event in controller.run(**kwargs)]
    return asyncio.run(run())


def test_adaptive_loop_executes_initial_batch_then_replans_until_done():
    planner = _Planner([
        ReplanDecision(done=False, steps=[
            Step(id="r1", agent_id="navigation", intent="navigation.search_poi"),
        ]),
        ReplanDecision(done=True),
    ])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="最近的满了",
                         data={"available": False}),
        "r1": StepResult("r1", StepStatus.OK, speech="次近的可用",
                         data={"available": True}),
    })
    aggregator = _Aggregator()
    suspended = []

    async def suspend(*args):
        suspended.append(args)
        return {"kind": "final", "speech": "suspended"}

    controller = LoopController(
        planner, executor, aggregator, suspend,
        max_iters=2, budget_ms=5000,
    )
    events = _collect(
        controller,
        goal="找到可用充电站",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="navigation")],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(),
        user_text="找充电站，满了就换次近的",
    )

    assert events[0]["kind"] == "speech"
    assert executor.runs == [["s1"], ["r1"]]
    assert executor.done_seeds == [set(), {"s1"}]
    assert len(planner.observations) == 2
    assert planner.observations[0][-1]["data"] == {"available": False}
    assert events[-1]["speech"] == "best effort"
    assert suspended == []


def test_need_confirm_suspends_immediately_inside_loop():
    planner = _Planner([])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.NEED_CONFIRM, speech="确认开后备箱？"),
    })
    aggregator = _Aggregator()
    suspend_calls = []

    async def suspend(step_result, results, plan, ctx):
        suspend_calls.append((step_result, results, plan, ctx))
        return {"kind": "final", "speech": step_result.speech, "need_confirm": True}

    controller = LoopController(
        planner, executor, aggregator, suspend,
        max_iters=2, budget_ms=5000,
    )
    events = _collect(
        controller,
        goal="打开后备箱后继续",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="edge-vehicle")],
                          complexity="adaptive"),
        agents=[],
        ctx=PlanContext(),
        user_text="打开后备箱",
    )

    assert events[-1]["need_confirm"] is True
    assert len(suspend_calls) == 1
    assert aggregator.calls == []


def test_budget_exhaustion_returns_best_effort_and_continue_prompt():
    planner = _Planner([])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="只完成了一部分"),
    })
    aggregator = _Aggregator()

    async def suspend(*_args):
        raise AssertionError("should not suspend")

    controller = LoopController(
        planner, executor, aggregator, suspend,
        max_iters=2, budget_ms=0,
    )
    events = _collect(
        controller,
        goal="完成复杂任务",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="a")],
                          complexity="adaptive"),
        agents=[],
        ctx=PlanContext(),
        user_text="复杂任务",
    )

    assert executor.runs == [["s1"]]
    assert planner.observations == []
    assert events[-1]["follow_up"] == "要我继续吗？"

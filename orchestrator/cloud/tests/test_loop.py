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

    async def replan(self, goal, observations, agents, ctx, granted_permissions=None,
                     working_set=None):
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

    async def compose(self, text, results, **kwargs):
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


# ─── T2 streaming tests ───

def test_stream_yields_speech_deltas_for_single_cloud_step():
    """Single-step cloud agent in T2 loop should stream speech deltas."""
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({})
    aggregator = _Aggregator()

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", "正在搜索")
        yield ("speech", "附近的充电站")
        from cockpit.agent.v1 import agent_pb2
        yield ("final", agent_pb2.ExecuteResponse(
            status=0, speech="找到3个充电站"))

    controller = LoopController(
        planner, executor, aggregator, None,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    events = _collect(
        controller,
        goal="找充电站",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="nav", kind="agent",
                        deployment="cloud", intent="nav.search",
                        latency_budget_ms=5000)],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(),
        user_text="找充电站",
    )

    speech_events = [e for e in events if e.get("kind") == "speech"]
    # First is the THINKING_FILLER, then the two streamed deltas
    assert any("搜索" in e.get("delta", "") for e in speech_events)
    assert any("充电站" in e.get("delta", "") for e in speech_events)
    # Executor should NOT have been called (streaming succeeded)
    assert executor.runs == []


def test_stream_failure_falls_back_to_executor():
    """When streaming fails, the loop should fall back to the unary executor."""
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="executor result"),
    })
    aggregator = _Aggregator()

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        raise RuntimeError("agent does not support streaming")
        yield  # make it an async generator

    controller = LoopController(
        planner, executor, aggregator, None,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    events = _collect(
        controller,
        goal="test",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="a", kind="agent",
                        deployment="cloud", intent="test.do",
                        latency_budget_ms=5000)],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(),
        user_text="test",
    )

    assert executor.runs == [["s1"]]
    assert events[-1]["speech"] == "best effort"


def test_stream_need_confirm_suspends_in_loop():
    """Streaming a NEED_CONFIRM response should suspend inside the loop."""
    planner = _Planner([])
    executor = _Executor({})
    aggregator = _Aggregator()
    suspend_calls = []

    async def suspend(step_result, results, plan, ctx):
        suspend_calls.append(step_result)
        return {"kind": "final", "speech": step_result.speech, "need_confirm": True}

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        from cockpit.agent.v1 import agent_pb2
        yield ("speech", "确认")
        yield ("final", agent_pb2.ExecuteResponse(
            status=1, speech="确认开后备箱？"))

    controller = LoopController(
        planner, executor, aggregator, suspend,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    events = _collect(
        controller,
        goal="open trunk",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="edge-vehicle", kind="agent",
                        deployment="cloud", intent="trunk.open",
                        latency_budget_ms=5000)],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(),
        user_text="打开后备箱",
    )

    assert events[-1]["need_confirm"] is True
    assert len(suspend_calls) == 1
    assert executor.runs == []


def test_stream_emits_step_agent_span(monkeypatch):
    """T2 streaming direct path must emit a step.agent span (parity with engine D0).

    Regression: a single-step adaptive plan that streams used to append results
    without emitting step.agent:<id>, so the trace lost the agent identity —
    most visible on NEED_CONFIRM/NEED_SLOT suspends (e.g. parking.pay).
    """
    from observability import events

    spans = []

    class FakeEmitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((node, kwargs.get("status")))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        events, "get_emitter",
        lambda service="cloud": FakeEmitter(), raising=False,
    )

    planner = _Planner([])
    executor = _Executor({})
    aggregator = _Aggregator()
    suspend_calls = []

    async def suspend(step_result, results, plan, ctx):
        suspend_calls.append(step_result)
        return {"kind": "final", "speech": step_result.speech, "need_confirm": True}

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        from cockpit.agent.v1 import agent_pb2
        yield ("speech", "确认")
        yield ("final", agent_pb2.ExecuteResponse(
            status=1, speech="确认支付吗？"))

    controller = LoopController(
        planner, executor, aggregator, suspend,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    _collect(
        controller,
        goal="pay",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="parking-payment", kind="agent",
                        deployment="cloud", intent="parking.pay",
                        latency_budget_ms=5000)],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(trace_id="t-loop-1"),
        user_text="把停车费付了",
    )

    assert ("step.agent:parking-payment", "wait") in spans
    assert len(suspend_calls) == 1


def test_no_stream_fn_keeps_existing_behavior():
    """Without stream_fn, the loop uses the executor as before."""
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="done"),
    })
    aggregator = _Aggregator()

    controller = LoopController(
        planner, executor, aggregator, None,
        max_iters=2, budget_ms=5000, stream_fn=None,
    )
    events = _collect(
        controller,
        goal="test",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="a", kind="agent",
                        deployment="cloud", intent="test.do")],
            complexity="adaptive",
        ),
        agents=[],
        ctx=PlanContext(),
        user_text="test",
    )

    assert executor.runs == [["s1"]]
    assert events[-1]["speech"] == "best effort"

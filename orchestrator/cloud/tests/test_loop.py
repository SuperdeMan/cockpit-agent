"""Bounded adaptive loop behavior."""
from __future__ import annotations

import asyncio

from orchestrator.cloud.loop import LoopController, summarize
from orchestrator.cloud.models import (
    Plan, PlanContext, ReplanDecision, Step, StepResult, StepStatus,
)


class _Planner:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.observations = []
        self.adaptive_flags = []

    async def replan(self, goal, observations, agents, ctx, granted_permissions=None,
                     working_set=None, skill_names=None, exemplar_names=None,
                     adaptive=False):
        self.observations.append(list(observations))
        self.adaptive_flags.append(adaptive)
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


def test_observation_summary_carries_known_step_intent():
    """Replanner needs the completed capability identity, not only an opaque step id."""
    result = StepResult("s1", StepStatus.OK, speech="明天有雨")

    assert summarize(result, intent="info.weather")["intent"] == "info.weather"
    assert "intent" not in summarize(result)


def test_observation_summary_promotes_explicit_same_intent_retry_signal():
    result = StepResult(
        "s1", StepStatus.OK,
        data={"available": False, "retry_same_intent": True},
    )

    observation = summarize(result, intent="navigation.search_poi")

    assert observation["retry_same_intent"] is True


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

    async def suspend(*args, **kwargs):
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


def test_adaptive_selfcheck_flag_is_raised_only_on_the_first_replan():
    """`adaptive` 声明只在**第一次** replan 上兑现成一次自查。

    后续 replan 返回 done 是 adaptive 的**正常收尾**——在那里也纠偏等于给每个
    adaptive 请求白加一次 LLM 往返。这条把「守卫的作用域」钉住，防的是
    「为了修一个 ~10% 的形态，给 100% 的 adaptive 请求加成本」（findings §22.3）。
    """
    planner = _Planner([
        ReplanDecision(done=False, steps=[
            Step(id="r1", agent_id="nearby", intent="nearby.search"),
        ]),
        ReplanDecision(done=True),
    ])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="今天中雨",
                         data={"condition": "中雨"}),
        "r1": StepResult("r1", StepStatus.OK, speech="推荐几个室内去处"),
    })

    async def suspend(*args, **kwargs):
        return {"kind": "final", "speech": "suspended"}

    controller = LoopController(
        planner, executor, _Aggregator(), suspend, max_iters=2, budget_ms=5000,
    )
    _collect(
        controller,
        goal="根据今天的天气推荐游玩地点",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="info")],
                          complexity="adaptive"),
        agents=[],
        ctx=PlanContext(),
        user_text="今天的天气适合去哪玩",
    )

    assert planner.adaptive_flags == [True, False]


def test_simple_plan_never_raises_the_adaptive_selfcheck_flag():
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({"s1": StepResult("s1", StepStatus.OK, speech="今天多云")})

    async def suspend(*args, **kwargs):
        return {"kind": "final", "speech": "suspended"}

    controller = LoopController(
        planner, executor, _Aggregator(), suspend, max_iters=2, budget_ms=5000,
    )
    _collect(
        controller,
        goal="查询今天天气",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="info")], complexity="simple"),
        agents=[],
        ctx=PlanContext(),
        user_text="今天天气怎么样",
    )

    assert planner.adaptive_flags == [False]


def test_need_confirm_suspends_immediately_inside_loop():
    planner = _Planner([])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.NEED_CONFIRM, speech="确认开后备箱？"),
    })
    aggregator = _Aggregator()
    suspend_calls = []

    async def suspend(step_result, results, plan, ctx, prior=None):
        suspend_calls.append((step_result, results, plan, ctx, prior))
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

    async def suspend(*_args, **_kwargs):
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

    async def suspend(step_result, results, plan, ctx, prior=None):
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

    async def suspend(step_result, results, plan, ctx, prior=None):
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


def test_replan_plan_inherits_skills_through_suspend_chain():
    """T2 知识继承贯通挂起链（2026-07-27 评审三批）：initial → replan（to_plan 新 Plan）
    → 该步 NEED_SLOT 挂起 → 序列化/恢复 → 再规划。此前 to_plan 产物 skills=[]，
    挂起序列化的正是它——恢复后再规划失忆。"""
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.models import SessionState

    skills = ["full:conditional-reminder@vec", "full:freshness-and-depth"]
    planner = _Planner([
        ReplanDecision(done=False, steps=[
            Step(id="r1", agent_id="reminder", intent="reminder.create"),
        ]),
    ])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="明天有雨",
                         data={"replan": True}),
        "r1": StepResult("r1", StepStatus.NEED_SLOT, speech="几点提醒？",
                         missing_slots=["time_text"]),
    })
    suspended_plans = []

    async def suspend(step_result, results, plan, ctx, prior=None):
        suspended_plans.append(plan)
        return {"kind": "final", "speech": "suspended"}

    controller = LoopController(
        _wrap_planner(planner), executor, _Aggregator(), suspend,
        max_iters=3, budget_ms=8000,
    )
    initial = Plan(steps=[Step(id="s1", agent_id="info", intent="info.weather")],
                   raw_text="查明天下雨吗，下雨提醒我", complexity="adaptive", goal="g")
    initial.skills = list(skills)
    exemplars = ["full:reminder#3@vec:0.68"]      # M5 P1 走同一条继承链
    initial.exemplars = list(exemplars)
    _collect(controller, goal="g", initial_plan=initial,
             agents=[], ctx=PlanContext(session_id="t"),
             user_text="查明天下雨吗，下雨提醒我")

    # ① replan 收到初规划的 skill / 范例名单
    assert planner.skill_names_seen == [skills]
    assert planner.exemplar_names_seen == [exemplars]
    # ② to_plan 产物（挂起序列化的就是它）继承两者
    assert suspended_plans and suspended_plans[0].skills == skills
    assert suspended_plans[0].exemplars == exemplars
    # ③ 序列化 → 恢复 round-trip 后名单仍在（恢复计划将作为下一轮 initial_plan 进 loop）
    data = PlannerEngine._serialize_plan(suspended_plans[0])
    state = SessionState(phase="wait_slot", pending_plan=data, pending_step_id="r1")
    restored, _ = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert restored is not None and restored.skills == skills
    assert restored.exemplars == exemplars


def _wrap_planner(planner):
    """记录 replan 收到的 skill_names / exemplar_names（不改共享 _Planner 契约面）。"""
    class _Rec:
        def __init__(self, inner):
            self._inner = inner
            inner.skill_names_seen = []
            inner.exemplar_names_seen = []

        async def replan(self, goal, observations, agents, ctx,
                         granted_permissions=None, working_set=None,
                         skill_names=None, exemplar_names=None, adaptive=False):
            self._inner.skill_names_seen.append(list(skill_names or []))
            self._inner.exemplar_names_seen.append(list(exemplar_names or []))
            return await self._inner.replan(
                goal, observations, agents, ctx,
                granted_permissions=granted_permissions,
                working_set=working_set, skill_names=skill_names,
                exemplar_names=exemplar_names)
    return _Rec(planner)


# ── B1：T2 流式部分输出后不许 unary 重跑 ────────────────────────────────────
#
# 修的是一个变量名级 bug：`elif streamed:` 里的 `streamed` 唯一置 True 的位置在
# `final_sr is not None` 分支内部，于是那条 elif **永不可达**——只要流出了输出而
# final 丢失（流断/超时/Agent 崩），`if not streamed:` 必然成立，executor 完整重跑：
# 话术播两遍、Agent 调两遍、外部 API 调两遍；action 已发出时形成重复副作用。
# 对照 engine.py 的 T1 D0 路径（speech/action 一到就置 True）实现是对的。


def _one_step_plan(intent="test.do"):
    return Plan(
        steps=[Step(id="s1", agent_id="a", kind="agent", deployment="cloud",
                    intent=intent, latency_budget_ms=5000)],
        complexity="adaptive",
    )


def _run_stream_case(stream_fn):
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="executor result"),
    })
    controller = LoopController(
        planner, executor, _Aggregator(), None,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    events = _collect(
        controller, goal="test", initial_plan=_one_step_plan(), agents=[],
        ctx=PlanContext(), user_text="test",
    )
    return executor, events


def test_stream_speech_then_lost_final_does_not_rerun():
    """场景 1：话术已流出、final 丢失 → 不许 unary 重跑（否则话术播两遍）。"""
    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", "正在为您查询")
        # 没有 final：流在这里断了

    executor, events = _run_stream_case(stream_fn)

    assert executor.runs == [], "部分输出后仍走了 executor——话术会播两遍"
    assert any(e.get("kind") == "speech" and "查询" in e.get("delta", "")
               for e in events)


def test_stream_action_then_lost_final_marks_outcome_uncertain():
    """场景 2：action 已发出、final 丢失 → 不重跑（重跑=重复副作用），
    且结果标为不确定，不假装成功。"""
    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("action", {"type": "vehicle.control",
                          "payload": {"command": "hvac.on"}})

    aggregator = _Aggregator()
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="executor result"),
    })
    controller = LoopController(
        planner, executor, aggregator, None,
        max_iters=2, budget_ms=5000, stream_fn=stream_fn,
    )
    _collect(controller, goal="test", initial_plan=_one_step_plan(), agents=[],
             ctx=PlanContext(), user_text="test")

    assert executor.runs == [], "action 已发出还重跑——重复副作用"
    composed = [r for _text, results in aggregator.calls for r in results]
    assert composed, "聚合器没拿到任何结果"
    assert composed[-1].data.get("_outcome_uncertain") is True
    assert "无法确认" in composed[-1].speech


def test_stream_no_output_still_falls_back_to_unary():
    """场景 3：零输出（Agent 不支持流式 / 立刻断） → 仍必须回退 unary。
    这条是防修过头的对照：`_outcome_uncertain` 那档不能把正常回退也吃掉。"""
    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        return
        yield  # pragma: no cover - 使其成为 async generator

    executor, _events = _run_stream_case(stream_fn)

    assert executor.runs == [["s1"]], "零输出时没有回退到 unary 执行"


def test_stream_empty_speech_delta_is_not_output():
    """空 delta 不算流出过输出——否则一个空串就能把 unary 回退整条关掉。"""
    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", "")

    executor, _events = _run_stream_case(stream_fn)

    assert executor.runs == [["s1"]]

import asyncio

import pytest

from cockpit.agent.v1 import agent_pb2
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.models import PlanContext, Step


def test_build_context_reads_trace_id():
    engine = PlannerEngine(
        clients=None,
        planner=None,
        executor=None,
        aggregator=None,
        session=None,
        loop=object(),
    )

    class Request:
        request_id = "request-1"
        session_id = "session-1"
        is_confirmation = False
        meta = {"trace_id": "front-7"}
        context = None

    context = engine._build_context(Request())

    assert context.trace_id == "front-7"


def _capture_spans(monkeypatch):
    from observability import events

    spans = []

    class FakeEmitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((trace_id, node, kwargs))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        events,
        "get_emitter",
        lambda service="cloud": FakeEmitter(),
        raising=False,
    )
    return spans


def _step():
    return Step(
        id="step-1",
        agent_id="navigation",
        intent="navigation.search_poi",
        endpoint="navigation:50061",
        kind="agent",
        deployment="cloud",
    )


def _context():
    return PlanContext(
        request_id="request-1",
        session_id="session-1",
        trace_id="trace-cloud-1",
        granted_permissions=["navigation"],
    )


def _statuses_for(spans, node):
    return [kwargs["status"] for _, span_node, kwargs in spans if span_node == node]


def test_dispatch_emits_step_span(monkeypatch):
    spans = _capture_spans(monkeypatch)

    async def fake_cloud(endpoint, intent, slots, context, meta, **kwargs):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech="ok",
        )

    dispatcher = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = _step()
    context = _context()

    asyncio.run(dispatcher.dispatch(step, context))

    assert any(node == "step.agent:navigation" for _, node, _ in spans)
    assert all(trace_id == "trace-cloud-1" for trace_id, _, _ in spans)
    assert _statuses_for(spans, "step.agent:navigation") == ["ok"]


@pytest.mark.parametrize(
    "status",
    [
        agent_pb2.ExecuteResponse.NEED_CONFIRM,
        agent_pb2.ExecuteResponse.NEED_SLOT,
    ],
)
def test_finish_emits_wait_step_span_for_pending_response(monkeypatch, status):
    spans = _capture_spans(monkeypatch)
    dispatcher = UnifiedDispatcher(cloud_call=None, edge_call=None)
    step = _step()
    context = _context()
    response = agent_pb2.ExecuteResponse(status=status)

    asyncio.run(dispatcher._finish(step, context, response))

    assert _statuses_for(spans, "step.agent:navigation") == ["wait"]


@pytest.mark.parametrize(
    "status",
    [
        agent_pb2.ExecuteResponse.NEED_CONFIRM,
        agent_pb2.ExecuteResponse.NEED_SLOT,
    ],
)
def test_dispatch_emits_wait_step_span_for_pending_response(monkeypatch, status):
    spans = _capture_spans(monkeypatch)

    async def fake_cloud(endpoint, intent, slots, context, meta, **kwargs):
        return agent_pb2.ExecuteResponse(status=status)

    dispatcher = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = _step()
    context = _context()

    asyncio.run(dispatcher.dispatch(step, context))

    assert _statuses_for(spans, "step.agent:navigation") == ["wait"]


# ── goal 是免费的对照物：值算出来了却没写进 slots（2026-08-04）────────────

def _plan_with(raw_llm, steps_slots):
    from orchestrator.cloud.models import Plan, Step
    steps = [Step(id=f"s{i+1}", agent_id="a", intent="x.y", slots=s)
             for i, s in enumerate(steps_slots)]
    p = Plan(steps=steps)
    p.raw_llm = raw_llm
    return p


def test_goal_value_dropped_flags_the_b3_3_shape():
    """journeys `B3-3` 的原形：goal 写着 26，plan 是 `hvac.set` + `slots:{}`。"""
    from orchestrator.cloud.engine import _goal_value_dropped
    plan = _plan_with('{"goal":"把空调调到用户最喜欢的温度（26度）","steps":[]}', [{}])
    assert _goal_value_dropped(plan) is True


def test_goal_value_dropped_is_quiet_when_the_value_landed():
    from orchestrator.cloud.engine import _goal_value_dropped
    plan = _plan_with('{"goal":"把空调调到26度"}', [{"temperature": "26"}])
    assert _goal_value_dropped(plan) is False


def test_goal_value_dropped_needs_a_number_in_the_goal():
    """goal 里本来就没有数字 → 没有「丢值」这回事，不许报。"""
    from orchestrator.cloud.engine import _goal_value_dropped
    assert _goal_value_dropped(_plan_with('{"goal":"打开空调"}', [{}])) is False


def test_goal_value_dropped_ignores_empty_and_unparsable_plans():
    """空计划归「缺步」检测器管；`raw_llm` 解析不了时不猜——观测信号不值得抛异常。"""
    from orchestrator.cloud.engine import _goal_value_dropped
    assert _goal_value_dropped(_plan_with('{"goal":"调到26度"}', [])) is False
    assert _goal_value_dropped(_plan_with("not json at all", [{}])) is False
    assert _goal_value_dropped(_plan_with("", [{}])) is False


def test_goal_value_dropped_counts_any_step_slot():
    """值落在**任何一步**的槽位里都算落地了——它判的是「丢没丢」不是「填对没填对」。"""
    from orchestrator.cloud.engine import _goal_value_dropped
    plan = _plan_with('{"goal":"查明天天气并把空调调到26度"}', [{}, {"temperature": "26"}])
    assert _goal_value_dropped(plan) is False

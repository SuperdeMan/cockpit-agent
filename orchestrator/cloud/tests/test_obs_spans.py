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


# ── C5-A 多意图覆盖度观测（2026-08-28，QA P1-04 前半 / P1-03 部分）───────
# 真栈原形：同一句话在一个 persona 出两步、在另一个 persona 只出一步——
# **方差本身就是零护栏的读数**。这一族断言把「哪一半丢了」变成机器可判的。

def test_clause_uncovered_flags_the_t18_shape():
    """「先查瑞幸，再点生椰拿铁不加糖」只出 nearby 一步 ⇒ 第二个诉求零覆盖。"""
    from orchestrator.cloud.engine import _clause_uncovered
    plan = _plan_with("", [{"keyword": "瑞幸"}])
    assert _clause_uncovered(plan, "先查瑞幸，再点生椰拿铁不加糖") == "1/2"


def test_clause_uncovered_is_quiet_when_both_halves_landed():
    """对照：两半都有 step 的槽值落在里面 ⇒ 不报（T21 成功轮）。"""
    from orchestrator.cloud.engine import _clause_uncovered
    plan = _plan_with("", [{"keyword": "瑞幸"}, {"item": "生椰拿铁"}])
    assert _clause_uncovered(plan, "先查瑞幸，再点生椰拿铁不加糖") == ""


def test_clause_uncovered_needs_at_least_two_positive_clauses():
    """这是个**多**意图判据：单句时它退化成「有没有槽值」，那是另一回事。"""
    from orchestrator.cloud.engine import _clause_uncovered
    assert _clause_uncovered(_plan_with("", [{}]), "打开空调") == ""
    assert _clause_uncovered(_plan_with("", [{}]), "") == ""


def test_clause_uncovered_drops_negated_clauses():
    """「车窗别开」不是一个待覆盖的诉求——否定分句不进分母（runtime.polarity 共用）。"""
    from orchestrator.cloud.engine import _clause_uncovered
    plan = _plan_with("", [{"keyword": "瑞幸"}])
    # 两个分句里有一个是否定式 ⇒ 肯定分句只剩 1 个 ⇒ 无信号
    assert _clause_uncovered(plan, "查一下瑞幸，别开车窗") == ""


def test_clause_uncovered_ignores_one_char_slot_values():
    """1 字的槽值在任何句子里都可能撞上，拿它判覆盖是把噪声当信号。"""
    from orchestrator.cloud.engine import _clause_uncovered
    plan = _plan_with("", [{"n": "1"}, {"ok": "是"}])
    assert _clause_uncovered(plan, "先查瑞幸，再点生椰拿铁") == "2/2"


def test_clause_uncovered_needs_steps():
    """零步计划归「缺步」检测器管，本条不重复报。"""
    from orchestrator.cloud.engine import _clause_uncovered
    assert _clause_uncovered(_plan_with("", []), "先查瑞幸，再点生椰拿铁") == ""

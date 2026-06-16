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
        perms=None,
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

    async def fake_cloud(endpoint, intent, slots, context, meta):
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

    async def fake_cloud(endpoint, intent, slots, context, meta):
        return agent_pb2.ExecuteResponse(status=status)

    dispatcher = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = _step()
    context = _context()

    asyncio.run(dispatcher.dispatch(step, context))

    assert _statuses_for(spans, "step.agent:navigation") == ["wait"]

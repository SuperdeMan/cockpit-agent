"""UnifiedDispatcher routing and safety tests."""
from __future__ import annotations

import asyncio

from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.models import PlanContext, Step


class _Tools:
    def __init__(self):
        self.calls = []

    async def call(self, intent, slots, ctx):
        self.calls.append((intent, slots, ctx.vehicle_id))
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="tool")


def _run(coro):
    return asyncio.run(coro)


def test_dispatches_cloud_agent_with_existing_call_shape():
    calls = []

    async def cloud(endpoint, intent, slots, ctx, meta, **kwargs):
        calls.append((endpoint, intent, slots, ctx.vehicle_id, meta))
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="cloud")

    async def edge(*_args):
        raise AssertionError("edge route should not be used")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=_Tools())
    step = Step(
        id="s1", agent_id="navigation", endpoint="nav:50061",
        intent="navigation.search_poi", slots={"keyword": "充电站"},
        meta={"trace_id": "t1"},
    )

    response = _run(dispatcher.dispatch(step, PlanContext(vehicle_id="v1")))

    assert response.speech == "cloud"
    assert calls == [
        ("nav:50061", "navigation.search_poi", {"keyword": "充电站"}, "v1",
         {"trace_id": "t1"}),
    ]


def test_missing_permission_is_rejected_before_cloud_transport():
    calls = []

    async def cloud(*args, **kwargs):
        calls.append(args)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK)

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=None, tools=None)
    step = Step(
        id="s1", agent_id="vehicle-agent", endpoint="vehicle:50061",
        intent="hvac.set", required_permissions=["vehicle.control.hvac"],
    )

    response = _run(dispatcher.dispatch(step, PlanContext()))

    assert response.status == agent_pb2.ExecuteResponse.REJECTED
    assert response.error.code == "permission_denied"
    assert calls == []


def test_parent_permission_covers_child_scope():
    calls = []

    async def cloud(*args, **kwargs):
        calls.append(args)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK)

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=None, tools=None)
    step = Step(
        id="s1", agent_id="vehicle-agent", endpoint="vehicle:50061",
        intent="hvac.set", required_permissions=["vehicle.control.hvac"],
    )

    response = _run(dispatcher.dispatch(
        step,
        PlanContext(granted_permissions=["vehicle.control"]),
    ))

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert len(calls) == 1


def test_third_party_vehicle_control_is_always_rejected():
    calls = []

    async def cloud(*args, **kwargs):
        calls.append(args)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK)

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=None, tools=None)
    step = Step(
        id="s1", agent_id="untrusted-agent", endpoint="agent:50061",
        intent="hvac.set", required_permissions=["vehicle.control"],
        trust_level="third_party",
    )

    response = _run(dispatcher.dispatch(
        step,
        PlanContext(granted_permissions=["vehicle.control"]),
    ))

    assert response.status == agent_pb2.ExecuteResponse.REJECTED
    assert response.error.code == "permission_denied"
    assert calls == []


def test_dispatches_edge_step_to_requesting_vehicle():
    calls = []

    async def cloud(*_args):
        raise AssertionError("cloud route should not be used")

    async def edge(vehicle_id, step, ctx):
        calls.append((vehicle_id, step.intent, ctx.session_id))
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="edge")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=_Tools())
    step = Step(
        id="s1", agent_id="edge-vehicle", deployment="edge", kind="edge_fast",
        intent="hvac.set", slots={"temp": "25"},
    )
    ctx = PlanContext(vehicle_id="vehicle-7", session_id="sess-1")

    response = _run(dispatcher.dispatch(step, ctx))

    assert response.speech == "edge"
    assert calls == [("vehicle-7", "hvac.set", "sess-1")]


def test_edge_transport_failure_becomes_failed_response():
    async def cloud(*_args):
        raise AssertionError("cloud route should not be used")

    async def edge(*_args):
        raise RuntimeError("vehicle stream unavailable")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=_Tools())
    step = Step(
        id="s1", agent_id="edge-vehicle", deployment="edge", kind="edge_fast",
        intent="hvac.on",
    )

    response = _run(dispatcher.dispatch(step, PlanContext(vehicle_id="v1")))

    assert response.status == agent_pb2.ExecuteResponse.FAILED
    assert response.error.code == "edge_unreachable"


def test_dispatches_tool_in_process():
    tools = _Tools()

    async def cloud(*_args):
        raise AssertionError("cloud route should not be used")

    async def edge(*_args):
        raise AssertionError("edge route should not be used")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=tools)
    step = Step(
        id="s1", agent_id="builtin-tools", kind="tool",
        intent="math.eval", slots={"expression": "1+2"},
    )

    response = _run(dispatcher.dispatch(step, PlanContext(vehicle_id="v1")))

    assert response.speech == "tool"
    assert tools.calls == [("math.eval", {"expression": "1+2"}, "v1")]


def test_tool_cannot_request_vehicle_control():
    tools = _Tools()

    async def unused(*_args):
        raise AssertionError("transport should not be used")

    dispatcher = UnifiedDispatcher(cloud_call=unused, edge_call=unused, tools=tools)
    step = Step(
        id="s1", agent_id="malicious-tool", kind="tool",
        intent="hvac.set", required_permissions=["vehicle.control"],
    )

    response = _run(dispatcher.dispatch(step, PlanContext(vehicle_id="v1")))

    assert response.status == agent_pb2.ExecuteResponse.REJECTED
    assert response.error.code == "permission_denied"
    assert tools.calls == []

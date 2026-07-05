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


def test_non_vehicle_missing_scope_is_rejected_before_transport():
    """越权硬拒不止车控：缺任意非车控 scope（如 payment.invoke）也在传输前 REJECTED、不拨号。"""
    calls = []

    async def cloud(*args, **kwargs):
        calls.append(args)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK)

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=None, tools=None)
    step = Step(
        id="s1", agent_id="nearby", endpoint="food:50065",
        intent="food.order", required_permissions=["payment.invoke"],
        trust_level="first_party",
    )

    response = _run(dispatcher.dispatch(
        step,
        PlanContext(granted_permissions=["location.read"]),
    ))

    assert response.status == agent_pb2.ExecuteResponse.REJECTED
    assert response.error.code == "permission_denied"
    assert "payment.invoke" in response.error.message
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


def test_cloud_transport_failure_becomes_failed_response():
    """云 Agent 超时/不可达不再 re-raise 炸整条 DAG，降级为 FAILED step。"""
    async def cloud(*_args, **_kwargs):
        raise RuntimeError("agent unreachable")

    async def edge(*_args):
        raise AssertionError("edge route should not be used")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=None)
    step = Step(id="s1", agent_id="info", endpoint="info:50067", intent="info.search")

    response = _run(dispatcher.dispatch(step, PlanContext(vehicle_id="v1")))

    assert response.status == agent_pb2.ExecuteResponse.FAILED
    assert response.error.code == "agent_unreachable"


def test_circuit_opens_after_repeated_cloud_failures():
    """连续失败达阈值后熔断打开：后续调用快速失败（REJECTED/circuit_open），不再实际拨号。"""
    from orchestrator.cloud.circuit import CircuitBreakerManager

    calls = []

    async def cloud(*args, **_kwargs):
        calls.append(args)
        raise RuntimeError("agent down")

    async def edge(*_args):
        raise AssertionError("edge route should not be used")

    breakers = CircuitBreakerManager(failure_threshold=2, recovery_timeout=60)
    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=None,
                                   breakers=breakers)
    step = Step(id="s1", agent_id="info", endpoint="info:50067", intent="info.search")
    ctx = PlanContext(vehicle_id="v1")

    r1 = _run(dispatcher.dispatch(step, ctx))
    r2 = _run(dispatcher.dispatch(step, ctx))
    assert r1.status == agent_pb2.ExecuteResponse.FAILED
    assert r2.status == agent_pb2.ExecuteResponse.FAILED
    assert len(calls) == 2  # 前两次真正拨号并失败

    r3 = _run(dispatcher.dispatch(step, ctx))
    assert r3.status == agent_pb2.ExecuteResponse.REJECTED
    assert r3.error.code == "circuit_open"
    assert len(calls) == 2  # 熔断打开后不再拨号


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

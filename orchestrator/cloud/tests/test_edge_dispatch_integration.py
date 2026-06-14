"""In-process P1 integration: edge result data feeds a later cloud step."""
from __future__ import annotations

import asyncio

from google.protobuf import struct_pb2
from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, PlanContext, Step, StepStatus


def _response(speech, data=None):
    payload = struct_pb2.Struct()
    payload.update(data or {})
    return agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.OK,
        speech=speech,
        data=payload,
    )


def test_edge_result_can_supply_slot_to_following_cloud_step():
    cloud_calls = []

    async def edge(vehicle_id, step, ctx):
        assert vehicle_id == "v1"
        assert step.intent == "vehicle.read_state"
        return _response("当前电量35%", {"battery_percent": 35})

    async def cloud(endpoint, intent, slots, ctx, meta):
        cloud_calls.append((endpoint, intent, dict(slots)))
        return _response("已找到适合当前电量的充电站")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge)
    executor = DagExecutor(dispatcher=dispatcher)
    plan = Plan(steps=[
        Step(
            id="s1", agent_id="edge-vehicle", kind="edge_fast",
            deployment="edge", intent="vehicle.read_state",
        ),
        Step(
            id="s2", agent_id="navigation", endpoint="nav:50061",
            intent="navigation.search_poi", depends_on=["s1"],
            slot_refs={"battery_percent": "s1.data.battery_percent"},
        ),
    ])

    async def collect():
        return [
            result async for result in executor.run(
                plan, PlanContext(vehicle_id="v1"))
        ]

    results = asyncio.run(collect())

    assert [result.status for result in results] == [
        StepStatus.OK, StepStatus.OK,
    ]
    assert cloud_calls == [
        ("nav:50061", "navigation.search_poi", {"battery_percent": "35.0"}),
    ]

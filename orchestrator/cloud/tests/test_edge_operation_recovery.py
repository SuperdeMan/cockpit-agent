"""CA2-11 in the cloud: a vehicle step that timed out is looked up, not guessed.

A deadline is "the vehicle may have run it", never "unreachable". After a timeout the
executor asks the vehicle's operation log once: done closes the step with a zero-domain
sentence, failed is a failure, absent (never delivered) is re-sent with the same
operation so a late first copy is deduplicated by the vehicle, anything else stays unknown.
"""
from __future__ import annotations

import asyncio

import pytest
from google.protobuf.struct_pb2 import Struct

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2

from orchestrator.cloud.clients import Clients
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, PlanContext, Step, StepStatus
from runtime import capability_contract as cc
from runtime import operation as op

OK = agent_pb2.ExecuteResponse.OK


def _edge_step(**over) -> Step:
    contract = cc.declaration([], "state_change", preconditions=("permission", "val"),
                              vehicle_specific=True, legacy=False, admission="durable",
                              parameters={"value": {"type": "string"}})
    cap = agent_pb2.Capability(intent="hvac.on", effect="write", contract=cc.to_proto(contract))
    manifest = agent_pb2.AgentManifest(agent_id="edge-vehicle", deployment="edge", kind="edge_fast",
                                       trust_level="first_party", requires_permissions=["vehicle.control"],
                                       capabilities=[cap])
    fields = cc.step_fields(manifest, cap)
    kw = {"id": "s1", "agent_id": "edge-vehicle", "deployment": "edge", "kind": "edge_fast",
          "intent": "hvac.on", "latency_budget_ms": 50, **fields}
    kw.update(over)
    return Step(**kw)


def _operation_response(status: str):
    data = Struct()
    data.update({"_operation": {"operation_id": "x", "decision": "query", "status": status}})
    return agent_pb2.ExecuteResponse(status=OK, data=data)


class _Deadline(Exception):
    def code(self):
        class _Code:
            name = "DEADLINE_EXCEEDED"
        return _Code()


class _Vehicle:
    """A scripted vehicle: per-call behaviour for executes, a fixed answer for queries."""

    def __init__(self, executes, query_status=None):
        self.executes = list(executes)
        self.query_status = query_status
        self.metas: list[dict] = []
        self.queries: list[str] = []

    async def edge_call(self, vehicle_id, step, ctx):
        self.metas.append(dict(step.meta))
        behaviour = self.executes.pop(0)
        if behaviour == "timeout":
            raise _Deadline()
        if behaviour == "hang":
            await asyncio.sleep(5)
        return agent_pb2.ExecuteResponse(status=OK, speech="已打开空调")

    async def edge_query(self, vehicle_id, operation_id, *, step_id, timeout):
        self.queries.append(operation_id)
        if self.query_status is None:
            raise _Deadline()
        return _operation_response(self.query_status)


def _run(vehicle, step=None):
    async def unused(*args, **kwargs):          # pragma: no cover - cloud agents are not involved
        raise AssertionError("no cloud agent here")

    dispatcher = UnifiedDispatcher(cloud_call=unused, edge_call=vehicle.edge_call,
                                   edge_query=vehicle.edge_query)
    executor = DagExecutor(dispatcher=dispatcher)
    step = step or _edge_step()
    ctx = PlanContext(vehicle_id="v1", granted_permissions=["vehicle.control"])

    async def go():
        return [r async for r in executor.run(Plan(steps=[step]), ctx)][0]
    return asyncio.run(go()), step


def test_edge_writes_carry_their_operation_to_the_vehicle():
    step = _edge_step()
    assert op.valid_operation_id(step.operation_id)
    ref = op.decode_ref(step.meta[op.HEADER])
    assert ref.operation_id == step.operation_id and ref.step_id == "s1"
    ctx = PlanContext(vehicle_id="v1", prefs={op.HEADER: "forged"})
    assert op.decode_ref(Clients._merge_meta(ctx, step.meta)[op.HEADER]).operation_id == step.operation_id


def test_a_deadline_is_a_timeout_not_unreachable():
    result, _ = _run(_Vehicle(["timeout"], query_status=None))
    assert result.status == StepStatus.FAILED and result.error == "step_timeout"


def test_a_timed_out_operation_the_vehicle_completed_is_closed_from_its_log():
    vehicle = _Vehicle(["timeout"], query_status=op.DONE)
    result, step = _run(vehicle)
    assert vehicle.queries == [step.operation_id] and len(vehicle.metas) == 1
    assert result.status == StepStatus.OK and "车端记录显示这个操作已经完成" in result.speech
    assert result.data["_operation"] == {"operation_id": step.operation_id, "decision": "recovered",
                                         "status": op.DONE}
    assert result.fingerprint                      # a later replan in this turn cannot resend it


def test_an_operation_the_vehicle_never_received_is_resent_with_the_same_identity():
    vehicle = _Vehicle(["timeout", "ok"], query_status="absent")
    result, step = _run(vehicle)
    assert result.status == StepStatus.OK and len(vehicle.metas) == 2
    assert vehicle.metas[0][op.HEADER] == vehicle.metas[1][op.HEADER]


def test_a_failure_recorded_by_the_vehicle_is_a_failure():
    result, _ = _run(_Vehicle(["timeout"], query_status=op.FAILED))
    assert result.status == StepStatus.FAILED and result.error == "edge_reported_failure"


@pytest.mark.parametrize("status", [op.ACCEPTED, op.ORPHANED, op.CANCELLED, None])
def test_anything_else_stays_unknown_and_is_not_resent(status):
    vehicle = _Vehicle(["timeout"], query_status=status)
    result, _ = _run(vehicle)
    assert result.status == StepStatus.FAILED and result.error == "step_timeout"
    assert len(vehicle.metas) == 1 and result.fingerprint


def test_an_executor_side_timeout_is_recovered_the_same_way():
    vehicle = _Vehicle(["hang"], query_status=op.DONE)
    result, _ = _run(vehicle)
    assert result.status == StepStatus.OK and result.data["_operation"]["decision"] == "recovered"


def test_steps_without_an_operation_are_not_queried():
    vehicle = _Vehicle(["timeout"], query_status=op.DONE)
    plain = Step(id="s1", agent_id="edge-vehicle", deployment="edge", kind="edge_fast",
                 intent="hvac.on", latency_budget_ms=50)
    result, _ = _run(vehicle, plain)
    assert vehicle.queries == [] and result.error == "step_timeout"


def test_the_query_reads_only_the_operation_record():
    async def query(vehicle_id, operation_id, *, step_id, timeout):
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.REJECTED)

    dispatcher = UnifiedDispatcher(cloud_call=None, edge_call=None, edge_query=query)
    step = _edge_step()
    ctx = PlanContext(vehicle_id="v1")
    assert asyncio.run(dispatcher.query_edge_operation(step, ctx, 0.5)) == ""
    assert asyncio.run(UnifiedDispatcher(cloud_call=None, edge_call=None)
                       .query_edge_operation(step, ctx, 0.5)) == ""
    assert asyncio.run(dispatcher.query_edge_operation(step, PlanContext(), 0.5)) == ""


def test_the_client_sends_a_read_only_operation_query():
    sent = []

    class _Stub:
        async def DispatchToEdge(self, envelope, timeout):
            sent.append((envelope, timeout))
            return channel_pb2.EdgeResult(step_id=envelope.call.step_id, result=_operation_response(op.DONE))

    clients = Clients.__new__(Clients)
    clients._edge_stub = lambda: _Stub()
    response = asyncio.run(clients.query_edge_operation("v1", "a" * 32, step_id="s1-recover", timeout=1.5))
    envelope, timeout = sent[0]
    assert envelope.vehicle_id == "v1" and envelope.call.operation_query == "a" * 32
    assert not envelope.call.intent.name and timeout == 1.5 and response.status == OK

"""CA2-11 at the vehicle: cloud-dispatched writes are admitted against the operation log.

Everything goes through ``EdgeCallExecutor.dispatch``, the entry the cloud reaches:
a repeated delivery of one operation never runs VAL twice, a confirmation resumes the
record its question opened, a restart between executing and recording reads as
unknown, and the read-only operation query never touches VAL.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from edge_call import EdgeCallExecutor
from val import VAL

from orchestrator.edge.operation_log import EdgeOperationLog
from runtime import operation as op

OK, NEED_CONFIRM, REJECTED = (agent_pb2.ExecuteResponse.OK, agent_pb2.ExecuteResponse.NEED_CONFIRM,
                              agent_pb2.ExecuteResponse.REJECTED)


@functools.lru_cache(maxsize=None)
def _contract(intent: str) -> tuple:
    from capabilities import build_edge_manifests
    from runtime.capability_contract import HEADER, capability_digest
    for manifest in build_edge_manifests():
        for cap in manifest.capabilities:
            if cap.intent == intent:
                return ((HEADER, capability_digest(manifest, cap)),)
    return ()


def _call(intent, operation_id="", *, confirmed=False, slots=None, query=""):
    meta = dict(_contract(intent)) if intent else {}
    if operation_id:
        meta[op.HEADER] = op.encode_ref(op.OperationRef(operation_id, step_id="s1"))
    if confirmed:
        meta["confirmed"] = "true"
    return channel_pb2.EdgeCall(step_id="s1", intent=common_pb2.Intent(name=intent, slots=slots or {}),
                                meta=meta, operation_query=query)


def _rig(path=":memory:"):
    val = VAL()
    commands = []
    real = val.execute

    def counting(*args, **kwargs):
        commands.append(args[0])
        return real(*args, **kwargs)

    val.execute = counting
    return val, commands, EdgeCallExecutor(val, operation_log=EdgeOperationLog(str(path)))


def _dispatch(executor, call):
    return asyncio.run(executor.dispatch(call))


def _operation(response) -> dict:
    return json_format.MessageToDict(response.data).get("_operation") or {}


def test_a_repeated_delivery_never_runs_the_command_twice():
    val, commands, executor = _rig()
    oid = op.new_operation_id()
    first = _dispatch(executor, _call("hvac.on", oid))
    assert first.status == OK and val.state["hvac_on"] is True and len(commands) == 1
    again = _dispatch(executor, _call("hvac.on", oid))
    assert again.status == OK and len(commands) == 1 and list(again.actions) == []
    assert _operation(again)["decision"] == op.DUPLICATE and "没有重复执行" in again.speech


def test_a_confirmation_resumes_its_question_and_runs_once():
    val, commands, executor = _rig()
    oid = op.new_operation_id()
    asked = _dispatch(executor, _call("trunk.open", oid))
    assert asked.status == NEED_CONFIRM and commands == []
    assert executor.operation_log.lookup(oid)["phase"] == op.PHASE_AWAITING
    done = _dispatch(executor, _call("trunk.open", oid, confirmed=True))
    assert done.status == OK and val.state["trunk"] == "open" and len(commands) == 1
    twice = _dispatch(executor, _call("trunk.open", oid, confirmed=True))
    assert _operation(twice)["decision"] == op.DUPLICATE and len(commands) == 1


def test_a_write_without_an_operation_id_is_refused_before_val():
    val, commands, executor = _rig()
    refused = _dispatch(executor, _call("hvac.on"))
    assert refused.status == REJECTED and refused.error.code == op.ID_REQUIRED
    assert commands == [] and val.state["hvac_on"] is False


def test_different_parameters_cannot_reuse_an_operation():
    val, commands, executor = _rig()
    oid = op.new_operation_id()
    _dispatch(executor, _call("hvac.set", oid, slots={"temp": "25"}))
    other = _dispatch(executor, _call("hvac.set", oid, slots={"temp": "18"}))
    assert other.status == REJECTED and other.error.code == op.MISMATCH
    assert val.state["hvac_temp"] == 25 and len(commands) == 1


def test_a_restart_between_executing_and_recording_reads_unknown(tmp_path):
    path, oid = tmp_path / "operations.sqlite3", op.new_operation_id()
    val, commands, executor = _rig(path)
    log = executor.operation_log
    real_run = executor._run

    def crash(call):                     # VAL ran; the process dies before the record is settled
        real_run(call)
        raise SystemExit("power loss")

    executor._run = crash
    try:
        _dispatch(executor, _call("hvac.on", oid))
    except SystemExit:
        pass
    log._db.close()
    val2, commands2, restarted = _rig(path)
    assert restarted.operation_log.orphaned_at_start in (0, 1)   # settle_uncertain may already have run
    answer = _dispatch(restarted, _call("hvac.on", oid))
    assert answer.status == OK and commands2 == [] and list(answer.actions) == []
    assert _operation(answer)["decision"] == op.UNKNOWN
    assert json_format.MessageToDict(answer.data).get("_outcome_uncertain") is True


def test_the_operation_query_reads_the_log_and_never_touches_val():
    val, commands, executor = _rig()
    oid = op.new_operation_id()
    _dispatch(executor, _call("hvac.on", oid))
    found = _dispatch(executor, _call("", query=oid))
    assert found.status == OK and _operation(found)["status"] == op.DONE
    assert _operation(found)["outcome"] == op.OUTCOME_SUCCEEDED
    absent = _dispatch(executor, _call("", query=op.new_operation_id()))
    assert _operation(absent)["status"] == "absent"
    assert len(commands) == 1
    for bad in (_call("hvac.on", query=oid), _call("", query="not-an-id")):
        assert _dispatch(executor, bad).status == REJECTED
    assert _dispatch(EdgeCallExecutor(VAL()), _call("", query=oid)).error.code == op.UNAVAILABLE


def test_a_read_capability_is_not_admitted():
    from capabilities import build_edge_manifests
    from runtime.capability_contract import contract_of
    read = next(c.intent for m in build_edge_manifests() for c in m.capabilities
                if contract_of(c).get("effect") == "read")
    val, commands, executor = _rig()
    executor.operation_log.ready = False                  # an unavailable log must not matter
    response = _dispatch(executor, _call(read))
    assert response.error.code != op.UNAVAILABLE


def test_every_vehicle_write_declares_durable_admission():
    from capabilities import build_edge_manifests
    from runtime.capability_contract import contract_of, durable_admission
    writes = [(c.intent, durable_admission(contract_of(c))) for m in build_edge_manifests()
              for c in m.capabilities if contract_of(c).get("effect") != "read"]
    assert writes and all(durable for _, durable in writes)


def test_the_cloud_channel_uses_the_admitted_entry():
    from cloud_client import CloudClient

    class _Executor:
        def __init__(self):
            self.calls = []

        async def dispatch(self, call):
            self.calls.append("dispatch")
            return agent_pb2.ExecuteResponse(status=OK)

        def execute(self, call):                          # pragma: no cover - must not be used
            self.calls.append("execute")
            return agent_pb2.ExecuteResponse(status=OK)

    class _Stream:
        async def write(self, frame):
            self.frame = frame

    executor = _Executor()
    client = CloudClient(edge_call_executor=executor)
    client._stream = _Stream()
    down = channel_pb2.DownFrame(correlation_id="c1", edge_call=channel_pb2.EdgeCall(step_id="s1"))
    asyncio.run(client._service_edge_call(down))
    assert executor.calls == ["dispatch"]


def test_the_server_admits_against_an_operation_log():
    from server import EdgeOrchestratorServicer
    service = EdgeOrchestratorServicer()
    assert isinstance(service.cloud._edge_calls.operation_log, EdgeOperationLog)

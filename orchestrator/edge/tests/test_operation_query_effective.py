"""CA2-11 v2：车端恢复查询报的是**此刻的有效状态**，与下一次认领的判定同一口径；只读，不落标记、不碰 VAL。"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf import json_format

from cockpit.channel.v1 import channel_pb2
from edge_call import EdgeCallExecutor
from val import VAL

from orchestrator.edge.operation_log import EdgeOperationLog
from runtime import operation as op
from runtime import operation_gate

STALE_MS = int(operation_gate.executing_stale_s() * 1000)


class _Clock:
    def __init__(self, now: int = 1_000_000):
        self.now = now

    def __call__(self) -> int:
        return self.now


def _rig():
    clock = _Clock()
    val = VAL()
    commands = []
    real = val.execute

    def counting(*args, **kwargs):
        commands.append(args[0])
        return real(*args, **kwargs)

    val.execute = counting
    log = EdgeOperationLog(":memory:", clock_ms=clock)
    return clock, log, commands, EdgeCallExecutor(val, operation_log=log)


def _insert(log, operation_id, envelope):
    assert asyncio.run(log.operation_insert(operation_id=operation_id, user_id="vehicle:v1", session_id="",
                                            agent_id="edge-vehicle", trace_id="", envelope=envelope))


def _query(executor, operation_id) -> dict:
    response = asyncio.run(executor.dispatch(channel_pb2.EdgeCall(step_id="s1", operation_query=operation_id)))
    return json_format.MessageToDict(response.data).get("_operation") or {}


def test_an_executing_record_that_went_stale_reads_orphaned_without_being_marked():
    clock, log, commands, executor = _rig()
    oid = op.new_operation_id()
    _insert(log, oid, {"phase": op.PHASE_EXECUTING, "binding_sha256": "b"})
    assert _query(executor, oid)["status"] == op.ACCEPTED
    clock.now += STALE_MS
    assert _query(executor, oid)["status"] == op.ORPHANED
    assert log.lookup(oid)["status"] == op.ACCEPTED          # 只读：日志里没有被改
    assert commands == []


def test_an_awaiting_record_past_its_window_reads_cancelled():
    clock, log, commands, executor = _rig()
    oid = op.new_operation_id()
    _insert(log, oid, {"phase": op.PHASE_AWAITING, "binding_sha256": "b", "expires_at_ms": clock.now + 1_000})
    assert _query(executor, oid)["status"] == op.ACCEPTED
    clock.now += 1_000
    found = _query(executor, oid)
    assert found["status"] == op.CANCELLED and found["phase"] == op.PHASE_AWAITING
    assert log.lookup(oid)["status"] == op.ACCEPTED
    assert commands == []


def test_settled_and_missing_records_read_as_before():
    clock, log, _commands, executor = _rig()
    oid = op.new_operation_id()
    _insert(log, oid, {"phase": op.PHASE_EXECUTING, "binding_sha256": "b"})
    assert asyncio.run(log.operation_settle(oid, binding="b", status=op.DONE, phase=op.PHASE_EXECUTING,
                                            result_ref={"outcome": op.OUTCOME_SUCCEEDED}))
    clock.now += 10 * STALE_MS
    assert _query(executor, oid)["status"] == op.DONE
    assert _query(executor, op.new_operation_id())["status"] == op.EDGE_ABSENT

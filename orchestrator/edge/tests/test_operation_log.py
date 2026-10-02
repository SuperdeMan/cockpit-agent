"""CA2-11: the vehicle SQLite log behaves like the cloud ledger.

The CA2-08 admission scenarios kept the in-memory twin honest against PostgreSQL;
here the same scenarios run through the real SDK servicer against the SQLite log,
plus what only the vehicle has: a restart between executing and recording.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agents._sdk.tests import test_operation_admission as ca208
from orchestrator.edge.operation_log import EdgeOperationLog
from runtime import operation as op


class SqliteHarness:
    def __init__(self, path):
        self.ledger = EdgeOperationLog(str(path))
        self._settle = self.ledger.operation_settle

    def _raw(self, sql, params=()):
        with self.ledger._lock:
            return self.ledger._db.execute(sql, params)

    async def row(self, operation_id):
        row = self._raw("SELECT status, envelope, result_ref FROM edge_operation WHERE operation_id = ?",
                        (operation_id,)).fetchone()
        return None if row is None else {"status": row[0], "envelope": json.loads(row[1]),
                                         "result_ref": json.loads(row[2])}

    async def age(self, operation_id, seconds):
        self._raw("UPDATE edge_operation SET touched_ms = touched_ms - ? WHERE operation_id = ?",
                  (int(seconds * 1000), operation_id))

    async def expire(self, operation_id):
        self._raw("UPDATE edge_operation SET envelope = json_set(envelope, '$.expires_at_ms', 0)"
                  " WHERE operation_id = ?", (operation_id,))

    async def set_ready(self, ready):
        self.ledger.ready = ready

    async def fail_settle(self, fail):
        async def broken(*args, **kwargs):
            raise op.OperationStoreError("operation_settle_failed")
        self.ledger.operation_settle = broken if fail else self._settle


@pytest.mark.parametrize("scenario", ca208.SCENARIOS, ids=[s.__name__ for s in ca208.SCENARIOS])
def test_admission_scenario_against_the_vehicle_log(scenario, tmp_path):
    harness = SqliteHarness(tmp_path / "operations.sqlite3")
    try:
        asyncio.run(scenario(harness))
    finally:
        harness.ledger.close()


def _claim(oid):
    manifest = ca208.durable_manifest()
    return ca208._claim(manifest, oid)


def test_a_restart_turns_executing_into_unknown_and_never_reruns(tmp_path):
    path, oid = tmp_path / "operations.sqlite3", op.new_operation_id()
    claim = _claim(oid)
    first = EdgeOperationLog(str(path))
    asyncio.run(first.operation_insert(operation_id=oid, user_id="u1", session_id="", agent_id="mcp-bridge",
                                       trace_id="", envelope=op.envelope(claim, phase=op.PHASE_EXECUTING,
                                                                         attempts=1)))
    first._db.close()                                  # the process dies before settling
    second = EdgeOperationLog(str(path))
    assert second.orphaned_at_start == 1
    row = asyncio.run(second.operation_get(oid))
    record = op.Record(status=row["status"], user_id=row["user_id"], agent_id=row["agent_id"],
                       envelope=row["envelope"], touched_ms=row["touched_ms"], now_ms=row["now_ms"])
    assert op.decide(record, claim, stale_ms=10**9).kind == op.UNKNOWN
    assert second.lookup(oid)["status"] == op.ORPHANED
    second.close()


def test_settled_records_survive_a_restart(tmp_path):
    path, oid = tmp_path / "operations.sqlite3", op.new_operation_id()
    claim = _claim(oid)
    log = EdgeOperationLog(str(path))
    asyncio.run(log.operation_insert(operation_id=oid, user_id="u1", session_id="", agent_id="mcp-bridge",
                                     trace_id="", envelope=op.envelope(claim, phase=op.PHASE_EXECUTING,
                                                                       attempts=1)))
    ref = op.outcome_ref(op.STATUS_OK, outcome=op.OUTCOME_SUCCEEDED)
    assert asyncio.run(log.operation_settle(oid, binding=claim.binding_sha256, status=op.DONE,
                                            phase=op.PHASE_EXECUTING, result_ref=ref))
    log.close()
    again = EdgeOperationLog(str(path))
    assert again.orphaned_at_start == 0
    assert again.lookup(oid) == {"status": op.DONE, "phase": op.PHASE_EXECUTING, "result_ref": ref}
    assert again.lookup(op.new_operation_id()) is None
    again.close()


def test_records_expire_after_the_retention_window(tmp_path):
    clock = {"ms": 1_000_000}
    log = EdgeOperationLog(str(tmp_path / "operations.sqlite3"), clock_ms=lambda: clock["ms"],
                           retention_ms=1000)
    old, new = op.new_operation_id(), op.new_operation_id()
    for oid in (old, new):
        asyncio.run(log.operation_insert(operation_id=oid, user_id="u1", session_id="", agent_id="a",
                                         trace_id="", envelope=op.envelope(_claim(oid), phase=op.PHASE_EXECUTING,
                                                                           attempts=1)))
        clock["ms"] += 1500
    assert log.lookup(old) is None and log.lookup(new) is not None
    log.close()


def test_without_a_path_the_log_is_memory_only():
    log = EdgeOperationLog.from_env({})
    assert not log.persistent and log.path == ":memory:"
    log.close()
    assert EdgeOperationLog.from_env({"EDGE_OPERATION_LOG": ":memory:"}).path == ":memory:"


def test_store_failures_read_as_unavailable(tmp_path):
    log = EdgeOperationLog(str(tmp_path / "operations.sqlite3"))
    log.ready = False
    with pytest.raises(op.OperationStoreError):
        asyncio.run(log.operation_get(op.new_operation_id()))
    log.ready = True
    log.close()
    with pytest.raises(op.OperationStoreError):
        log.lookup(op.new_operation_id())

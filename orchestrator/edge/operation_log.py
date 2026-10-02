"""Vehicle-side operation log (CA2-11): admission records for cloud-dispatched commands.

SQLite on the vehicle (a named volume in the PoC deployment). It is the receiver
store behind ``runtime.operation_gate``: the same conditional writes as the cloud
ledger (``agents/_sdk/ledger.py``) and its in-memory twin, one statement each, so a
write either matches the record state it expects or changes nothing.

A record is written as ``executing`` before VAL runs and settled afterwards. A
process that dies in between leaves ``executing`` behind; the next process marks
those ``orphaned`` before it serves anything, so a repeated delivery is answered
"unknown" and never re-run. Records hold digests, IDs, statuses and outcome
references only (the subject is the vehicle, not a user) and expire after 24 h.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

from runtime import operation as op

logger = logging.getLogger("edge.operation_log")

PATH_ENV = "EDGE_OPERATION_LOG"
RETENTION_MS = 24 * 3600 * 1000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_operation (
    operation_id TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL DEFAULT '',
    trace_id     TEXT NOT NULL DEFAULT '',
    envelope     TEXT NOT NULL,
    result_ref   TEXT NOT NULL DEFAULT '{}',
    created_ms   INTEGER NOT NULL,
    touched_ms   INTEGER NOT NULL
)"""
_PHASE = "json_extract(envelope, '$.phase')"
_BINDING = "json_extract(envelope, '$.binding_sha256')"
_EXPIRES = "json_extract(envelope, '$.expires_at_ms')"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EdgeOperationLog:
    def __init__(self, path: str, *, clock_ms=None, retention_ms: int = RETENTION_MS):
        self.path = path
        self.persistent = path != ":memory:"
        self.ready = True
        self._now = clock_ms or (lambda: int(time.time() * 1000))
        self._retention_ms = int(retention_ms)
        self._lock = threading.Lock()
        if self.persistent:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if self.persistent:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(_SCHEMA)
        self.orphaned_at_start = self._recover()

    @classmethod
    def from_env(cls, env=None) -> "EdgeOperationLog":
        """The deployment mounts a volume and names the file; without it the log only lives in memory."""
        env = os.environ if env is None else env
        path = (env.get(PATH_ENV) or "").strip()
        if not path:
            logger.warning("%s is not set: the operation log is not persistent", PATH_ENV)
        return cls(path or ":memory:")

    # ── store primitives ────────────────────────────────────────────────

    def _run(self, sql: str, params=()) -> int:
        if not self.ready:
            raise op.OperationStoreError(op.UNAVAILABLE)
        try:
            with self._lock:
                return self._db.execute(sql, params).rowcount
        except sqlite3.Error as exc:
            raise op.OperationStoreError("operation_store_failed") from exc

    def _one(self, sql: str, params=()):
        if not self.ready:
            raise op.OperationStoreError(op.UNAVAILABLE)
        try:
            with self._lock:
                return self._db.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise op.OperationStoreError("operation_store_failed") from exc

    def _recover(self) -> int:
        ref = op.outcome_ref(op.STATUS_FAILED, error_code="executor_restarted",
                             outcome=op.OUTCOME_UNCERTAIN)
        orphaned = self._run(
            f"UPDATE edge_operation SET status = ?, result_ref = ? WHERE status = ? AND {_PHASE} = ?",
            (op.ORPHANED, _json(ref), op.ACCEPTED, op.PHASE_EXECUTING))
        if orphaned:
            logger.warning("operation log: %d executing record(s) from a previous process are now unknown",
                           orphaned)
        return orphaned

    # ── the ledger interface used by runtime.operation_gate ─────────────────

    async def operations_ready(self) -> bool:
        return self.ready

    async def operation_insert(self, *, operation_id, user_id, session_id, agent_id, trace_id, envelope) -> bool:
        now = self._now()
        self._run("DELETE FROM edge_operation WHERE touched_ms < ?", (now - self._retention_ms,))
        return self._run(
            "INSERT OR IGNORE INTO edge_operation (operation_id, status, user_id, agent_id, session_id,"
            " trace_id, envelope, result_ref, created_ms, touched_ms) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)",
            (operation_id, op.ACCEPTED, user_id, agent_id, session_id or "", trace_id or "",
             _json(envelope), now, now)) == 1

    async def operation_get(self, operation_id):
        row = self._one("SELECT status, user_id, agent_id, envelope, touched_ms FROM edge_operation"
                        " WHERE operation_id = ?", (operation_id,))
        if row is None:
            return None
        return {"status": row[0], "user_id": row[1], "agent_id": row[2], "envelope": json.loads(row[3]),
                "touched_ms": int(row[4]), "now_ms": self._now()}

    async def operation_claim(self, operation_id, *, observed_binding, envelope, session_id, trace_id) -> bool:
        now = self._now()
        return self._run(
            "UPDATE edge_operation SET envelope = ?, session_id = ?, trace_id = ?, touched_ms = ?"
            f" WHERE operation_id = ? AND status = ? AND {_PHASE} = ? AND {_BINDING} = ?"
            f" AND typeof({_EXPIRES}) = 'integer' AND {_EXPIRES} > ?",
            (_json(envelope), session_id or "", trace_id or "", now, operation_id, op.ACCEPTED,
             op.PHASE_AWAITING, observed_binding, now)) == 1

    async def operation_settle(self, operation_id, *, binding, status, phase, result_ref, await_ttl_ms=0) -> bool:
        if await_ttl_ms > 0:
            envelope_sql = "json_set(envelope, '$.phase', ?, '$.expires_at_ms', ?)"
            envelope_args = (phase, self._now() + int(await_ttl_ms))
        else:
            envelope_sql, envelope_args = "json_set(envelope, '$.phase', ?)", (phase,)
        return self._run(
            f"UPDATE edge_operation SET status = ?, envelope = {envelope_sql}, result_ref = ?"
            f" WHERE operation_id = ? AND {_BINDING} = ?"
            f" AND (status = ? OR (status = ? AND {_PHASE} = ?))",
            (status, *envelope_args, _json(result_ref), operation_id, binding, op.ORPHANED,
             op.ACCEPTED, op.PHASE_EXECUTING)) == 1

    async def operation_mark_stale(self, operation_id, *, binding, stale_s, result_ref) -> bool:
        return self._run(
            f"UPDATE edge_operation SET status = ?, result_ref = ? WHERE operation_id = ? AND status = ?"
            f" AND {_PHASE} = ? AND {_BINDING} = ? AND touched_ms <= ?",
            (op.ORPHANED, _json(result_ref), operation_id, op.ACCEPTED, op.PHASE_EXECUTING, binding,
             self._now() - int(stale_s * 1000))) == 1

    async def operation_mark_expired(self, operation_id, *, binding, result_ref) -> bool:
        return self._run(
            f"UPDATE edge_operation SET status = ?, result_ref = ? WHERE operation_id = ? AND status = ?"
            f" AND {_PHASE} = ? AND {_BINDING} = ? AND COALESCE({_EXPIRES}, 0) <= ?",
            (op.CANCELLED, _json(result_ref), operation_id, op.ACCEPTED, op.PHASE_AWAITING, binding,
             self._now())) == 1

    # ── read-only recovery lookup (EdgeCall.operation_query) ─────────────────

    def lookup(self, operation_id: str) -> dict | None:
        """What the vehicle knows about one operation; never touches VAL."""
        row = self._one("SELECT status, envelope, result_ref FROM edge_operation WHERE operation_id = ?",
                        (operation_id,))
        if row is None:
            return None
        envelope = json.loads(row[1])
        return {"status": row[0], "phase": str(envelope.get("phase") or ""),
                "result_ref": json.loads(row[2] or "{}")}

    def close(self) -> None:
        with self._lock:
            self._db.close()

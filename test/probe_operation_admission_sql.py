"""CA2-08 offline probe: run the receiver admission scenarios against real PostgreSQL.

The pytest suite runs ``SCENARIOS`` against an in-memory twin of the SQL. This
probe runs the very same scenarios through ``TaskLedger`` and asyncpg, so a
divergence between the twin and the SQL shows up here. It never touches a
shared database: pass ``--embedded`` (needs ``pgserver``) for a throwaway local
PostgreSQL, or ``--dsn`` for a disposable database you own.

Needs asyncpg, protobuf, grpcio, pyyaml and pytest in the running interpreter;
it is not collected by pytest and is not part of CI.

    python test/probe_operation_admission_sql.py --embedded --output .artifacts/ca2-08/sql-probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "gen" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agents._sdk.ledger import OperationStoreError, TaskLedger  # noqa: E402

# The column this package proposes. Applied only to the throwaway database when the
# checked-out ledger_schema.sql does not carry it yet (pre-authorization branch state).
PROPOSED_COLUMN = "ALTER TABLE task_ledger ADD COLUMN IF NOT EXISTS operation JSONB NOT NULL DEFAULT '{}'"


def load_scenarios():
    path = ROOT / "agents" / "_sdk" / "tests" / "test_operation_admission.py"
    spec = importlib.util.spec_from_file_location("ca208_scenarios", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCENARIOS


class SqlHarness:
    def __init__(self, ledger: TaskLedger, admin):
        self.ledger, self._admin = ledger, admin
        self._settle = ledger.operation_settle

    async def row(self, operation_id):
        row = await self._admin.fetchrow(
            "SELECT status, operation, result_ref FROM task_ledger WHERE task_id=$1 AND kind='operation'",
            operation_id)
        if row is None:
            return None
        return {"status": row["status"], "envelope": json.loads(row["operation"]),
                "result_ref": json.loads(row["result_ref"])}

    async def age(self, operation_id, seconds):
        await self._admin.execute(
            "UPDATE task_ledger SET heartbeat_at = heartbeat_at - make_interval(secs => $2::double precision) "
            "WHERE task_id=$1", operation_id, float(seconds))

    async def expire(self, operation_id):
        await self._admin.execute(
            "UPDATE task_ledger SET operation = jsonb_set(operation, '{expires_at_ms}', '0'::jsonb) "
            "WHERE task_id=$1", operation_id)

    async def set_ready(self, ready):
        """Unavailable = a closed pool, so the real connection-failure path is exercised."""
        if not ready:
            await self.ledger._pool.close()
            return
        self.ledger._pool = None
        self.ledger._pg_ok = False
        self.ledger._init_done = False
        assert await self.ledger.init() and await self.ledger.operations_ready()

    async def fail_settle(self, fail):
        if fail:
            async def broken(*args, **kwargs):
                raise OperationStoreError("operation_settle_failed")
            self.ledger.operation_settle = broken
        else:
            self.ledger.operation_settle = self._settle


async def run(dsn: str) -> dict:
    import asyncpg
    admin = await asyncpg.connect(dsn)
    await admin.execute("DROP TABLE IF EXISTS task_ledger")
    ledger = TaskLedger(dsn=dsn)
    assert await ledger.init(), "ledger init failed"
    schema_has_column = await ledger.operations_ready()
    if not schema_has_column:
        await admin.execute(PROPOSED_COLUMN)
        ledger._init_done = False
        ledger._pg_ok = False
        assert await ledger.init() and await ledger.operations_ready(), "column check failed"
    columns = await admin.fetch(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name='task_ledger' ORDER BY ordinal_position")
    harness = SqlHarness(ledger, admin)
    results = []
    for scenario in load_scenarios():
        started = time.monotonic()
        try:
            await scenario(harness)
            results.append({"scenario": scenario.__name__, "passed": True})
        except Exception:
            results.append({"scenario": scenario.__name__, "passed": False,
                            "error": traceback.format_exc(limit=6)})
        results[-1]["ms"] = round((time.monotonic() - started) * 1000)
    rows = await admin.fetch("SELECT kind, status, count(*) AS n FROM task_ledger GROUP BY kind, status "
                             "ORDER BY kind, status")
    version = await admin.fetchval("SHOW server_version")
    await ledger.close_pool()
    await admin.close()
    return {
        "server_version": version,
        "schema_had_operation_column": schema_has_column,
        "task_ledger_columns": [dict(r) for r in columns],
        "scenarios": results,
        "passed": sum(r["passed"] for r in results),
        "failed": sum(not r["passed"] for r in results),
        "rows_by_kind_status": [dict(r) for r in rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--dsn")
    target.add_argument("--embedded", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    server = None
    workdir = None
    if args.embedded:
        import pgserver
        workdir = tempfile.TemporaryDirectory(prefix="ca208-pg-")
        server = pgserver.get_server(workdir.name, cleanup_mode="stop")
        dsn = server.get_uri()
    else:
        dsn = args.dsn
    try:
        report = asyncio.run(run(dsn))
    finally:
        if server is not None:
            server.cleanup()
    report.update({"python": platform.python_version(), "embedded": bool(args.embedded)})
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("server_version", "passed", "failed",
                                             "schema_had_operation_column")}, ensure_ascii=False))
    for item in report["scenarios"]:
        if not item["passed"]:
            print(item["scenario"], item["error"], sep="\n", file=sys.stderr)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

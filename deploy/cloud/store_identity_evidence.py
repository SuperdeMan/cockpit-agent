#!/usr/bin/env python3
"""Bounded, content-free identity evidence for migration stores."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable


MAX_ITEMS = 20_000
PAGE_SIZE = 256
PG_TABLES = {
    "memory_item": ("id", None),
    "memory_relation": ("id", None),
    "reminder_item": ("id", "status"),
    "task_ledger": ("task_id", "status"),
    "proactive_delivery": ("delivery_id", "state"),
    "scene_item": ("id", "status"),
    "voiceprint": ("id", None),
}
COLLECTOR_TABLES = ("turns", "spans", "llm_calls", "logs")
REDIS_PAGE_LUA = r'''
local info=redis.call("INFO","server")
redis.setresp(3)
local page=redis.call("SCAN",ARGV[1],"COUNT",256)
local out={}
for _,key in ipairs(page[2]) do
  local kind=redis.call("TYPE",key); if type(kind)=="table" then kind=kind.ok end
  local ttl=redis.call("PTTL",key)
  local dump=redis.call("DUMP",key)
  if ttl ~= -2 and dump then
    local deadline=-1; if ttl>=0 then deadline=redis.call("PEXPIRETIME",key) end
    local prefix=string.match(key,"^([A-Za-z0-9_-]+):") or "other"
    if string.len(prefix)>32 then prefix="other" end
    table.insert(out,{map={"identity_material",redis.sha1hex(key),"logical_material",redis.sha1hex(dump),"type",kind,"prefix",prefix,"deadline_ms",deadline}})
  end
end
local now=redis.call("TIME")
local version=string.match(info,"redis_version:([^\r\n]+)") or "unknown"
return {map={"cursor",page[1],"checked_at_ms",tonumber(now[1])*1000+math.floor(tonumber(now[2])/1000),"version",version,"rows",out}}
'''.strip()


def _hmac(key: bytes, value: bytes) -> str:
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def _load_key(control: Path) -> bytes:
    if control.is_symlink() or control.stat().st_size > 1024 * 1024:
        raise ValueError("identity key control is unsafe")
    payload = json.loads(control.read_text(encoding="utf-8"))
    value = payload.get("identity_hmac_key")
    if value is None and isinstance(payload.get("redis_identity"), dict):
        value = payload["redis_identity"].get("digest_key")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("identity HMAC key is invalid")
    return bytes.fromhex(value)


def _atomic_json(path: Path | None, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if path is None:
        os.write(1, encoded)
        return
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _pg_copy_query() -> str:
    selects = []
    for table, (identity, state) in PG_TABLES.items():
        state_sql = f"{state}::text" if state else "NULL::text"
        identity_material = (
            f"md5('id:1:' || {identity}::text) || md5('id:2:' || {identity}::text)"
        )
        row_json = f"(to_jsonb(t) - '{state}')::text" if state else "to_jsonb(t)::text"
        logical_material = (
            f"md5('row:1:' || {row_json}) || md5('row:2:' || {row_json})"
        )
        selects.append(
            f"SELECT '{table}'::text,{identity_material},{logical_material},{state_sql} "
            f"FROM {table} t"
        )
    return "COPY (" + " UNION ALL ".join(selects) + ") TO STDOUT WITH (FORMAT CSV);\n"


def collect_postgres(
    container: str, key: bytes, *, db_user: str = "postgres", database_name: str = "postgres",
) -> dict[str, object]:
    identities = {table: [] for table in PG_TABLES}
    logical = {table: {} for table in PG_TABLES}
    state_maps = {
        f"{table}.{state}": {} for table, (_, state) in PG_TABLES.items() if state
    }
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as sql_file, tempfile.TemporaryFile() as errors:
        sql_file.write(_pg_copy_query())
        sql_file.seek(0)
        process = subprocess.Popen(
            ["docker", "exec", "-i", container, "psql", "-U", db_user, "-d", database_name,
             "-v", "ON_ERROR_STOP=1", "-f", "-"],
            stdin=sql_file, stdout=subprocess.PIPE, stderr=errors, text=True,
            start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        assert process.stdout is not None
        count = 0
        for table, identity_material, logical_material, state in csv.reader(process.stdout):
            if table not in PG_TABLES:
                raise ValueError("unexpected PostgreSQL identity table")
            if (len(identity_material) != 64 or len(logical_material) != 64
                    or any(ch not in "0123456789abcdef" for ch in identity_material)
                    or any(ch not in "0123456789abcdef" for ch in logical_material)):
                raise ValueError("PostgreSQL identity digest material is invalid")
            identity = _hmac(
                key, f"pg:id:{table}:".encode() + bytes.fromhex(identity_material),
            )
            row_digest = _hmac(
                key, f"pg:row:{table}:".encode() + bytes.fromhex(logical_material),
            )
            identities[table].append(identity)
            logical[table][identity] = row_digest
            state_name = PG_TABLES[table][1]
            if state_name:
                state_maps[f"{table}.{state_name}"][identity] = state
            count += 1
            if count > MAX_ITEMS:
                process.kill()
                raise ValueError("PostgreSQL identity evidence exceeds item limit")
        if process.wait(timeout=30) != 0:
            raise RuntimeError("PostgreSQL identity collection failed")
    for values in identities.values():
        values.sort()
    return {"identity_sets": identities, "logical_rows": logical, "state_by_identity": state_maps}


def _redis_page(container: str, cursor: str) -> dict[str, object]:
    completed = subprocess.run(
        ["docker", "exec", container, "redis-cli", "--json", "EVAL",
         REDIS_PAGE_LUA, "0", cursor],
        text=True, capture_output=True, timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Redis identity page collection failed")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or set(payload) != {"cursor", "checked_at_ms", "version", "rows"}:
        raise ValueError("Redis identity page is invalid")
    return payload


def collect_redis(container: str, key: bytes) -> dict[str, object]:
    cursor = "0"
    rows: dict[str, dict[str, object]] = {}
    prefixes: dict[str, int] = {}
    types: dict[str, int] = {}
    checked_at_ms = 0
    version = ""
    while True:
        page = _redis_page(container, cursor)
        cursor = page["cursor"]
        checked_at_ms = page["checked_at_ms"]
        page_version = page["version"]
        if (not isinstance(cursor, str) or type(checked_at_ms) is not int
                or not isinstance(page_version, str) or not isinstance(page["rows"], list)):
            raise ValueError("Redis identity page fields are invalid")
        if version and version != page_version:
            raise ValueError("Redis version changed during identity scan")
        version = page_version
        for record in page["rows"]:
            if not isinstance(record, dict) or set(record) != {
                "identity_material", "logical_material", "type", "prefix", "deadline_ms",
            }:
                raise ValueError("Redis identity record is invalid")
            identity_material = record["identity_material"]
            logical_material = record["logical_material"]
            deadline = record["deadline_ms"]
            if (not isinstance(identity_material, str) or len(identity_material) != 40
                    or any(ch not in "0123456789abcdef" for ch in identity_material)
                    or not isinstance(logical_material, str) or len(logical_material) != 40
                    or any(ch not in "0123456789abcdef" for ch in logical_material)
                    or type(deadline) is not int or deadline < -1
                    or not isinstance(record["type"], str) or not isinstance(record["prefix"], str)
                    ):
                raise ValueError("Redis identity record is invalid")
            identity = _hmac(key, b"redis:id:" + bytes.fromhex(identity_material))
            logical = _hmac(key, b"redis:row:" + bytes.fromhex(logical_material))
            if identity in rows:
                raise ValueError("Redis identity record is duplicated")
            rows[identity] = {
                "logical": logical, "deadline_ms": deadline,
            }
            prefixes[record["prefix"]] = prefixes.get(record["prefix"], 0) + 1
            types[record["type"]] = types.get(record["type"], 0) + 1
            if len(rows) > MAX_ITEMS:
                raise ValueError("Redis identity evidence exceeds item limit")
        if cursor == "0":
            break
    deadlines = [record["deadline_ms"] for record in rows.values() if record["deadline_ms"] >= 0]
    return {"rows": rows, "checked_at_ms": checked_at_ms, "version": version,
            "prefixes": prefixes, "types": types,
            "persistent": sum(record["deadline_ms"] == -1 for record in rows.values()),
            "expiring": len(deadlines),
            "min_ttl_ms": max(0, min(deadlines) - checked_at_ms) if deadlines else 0,
            "max_ttl_ms": max(0, max(deadlines) - checked_at_ms) if deadlines else 0}


def _row_bytes(row: Iterable[object]) -> bytes:
    return json.dumps(list(row), ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str).encode()


def collect_collector(database: Path, key: bytes, retention_days: float = 7.0) -> dict[str, object]:
    rows: dict[str, dict[str, dict[str, object]]] = {table: {} for table in COLLECTOR_TABLES}
    total_rows = 0
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Collector integrity check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        schema_fingerprint = hashlib.sha256(json.dumps(
            schema, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        table_counts = {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in COLLECTOR_TABLES
        }
        turn_columns = [row[1] for row in connection.execute('PRAGMA table_info("turns")')]
        protected_traces: set[object] = set()
        if {"trace_id", "badcase", "gold_intents"}.issubset(turn_columns):
            protected_cursor = connection.execute(
                "SELECT trace_id,badcase,gold_intents FROM turns"
            )
            while protected_batch := protected_cursor.fetchmany(PAGE_SIZE):
                for trace, badcase, gold in protected_batch:
                    if badcase == 1 or gold not in (None, ""):
                        protected_traces.add(trace)
                        if len(protected_traces) > MAX_ITEMS:
                            raise ValueError(
                                "Collector protected trace evidence exceeds item limit"
                            )
        for table in COLLECTOR_TABLES:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            if not columns:
                raise ValueError("Collector table schema is missing")
            identity_columns = [row[1] for row in connection.execute(
                f'PRAGMA table_info("{table}")'
            ) if row[5] > 0]
            if not identity_columns:
                identity_columns = columns
            cursor = connection.execute(f'SELECT * FROM "{table}"')
            while batch := cursor.fetchmany(PAGE_SIZE):
                for row in batch:
                    identity_values = [row[columns.index(name)] for name in identity_columns]
                    identity = _hmac(key, f"collector:id:{table}:".encode() + _row_bytes(identity_values))
                    logical = _hmac(
                        key, f"collector:row:{table}:".encode() + _row_bytes(row),
                    )
                    trace = row[columns.index("trace_id")] if "trace_id" in columns else identity_values[0]
                    ts = row[columns.index("ts")] if "ts" in columns else 0
                    rows[table][identity] = {
                        "logical": logical,
                        "ts_ms": int(ts) if isinstance(ts, (int, float)) else 0,
                        "protected": trace in protected_traces,
                        "relation": _hmac(key, b"collector:trace:" + str(trace).encode()),
                    }
                    total_rows += 1
                    if total_rows > MAX_ITEMS:
                        raise ValueError("Collector identity evidence exceeds item limit")
    return {"rows": rows, "user_version": version, "schema_fingerprint": schema_fingerprint,
            "tables": table_counts, "integrity_check": "ok",
            "cleanup_cutoff_ms": int(time.time() * 1000 - retention_days * 86_400_000)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("store", choices=("postgres", "redis", "collector"))
    parser.add_argument("--container")
    parser.add_argument("--database", type=Path)
    key_source = parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument("--key-control", type=Path)
    key_source.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-key", action="store_true")
    parser.add_argument("--retention-days", type=float, default=7.0)
    parser.add_argument("--db-user", default="postgres")
    parser.add_argument("--database-name", default="postgres")
    args = parser.parse_args()
    if args.key_stdin:
        raw_key = sys.stdin.readline().strip()
        if len(raw_key) != 64:
            raise ValueError("identity HMAC key is invalid")
        key = bytes.fromhex(raw_key)
    else:
        key = _load_key(args.key_control)
    if args.store == "postgres":
        if not args.container: parser.error("postgres requires --container")
        payload = collect_postgres(
            args.container, key, db_user=args.db_user, database_name=args.database_name,
        )
    elif args.store == "redis":
        if not args.container: parser.error("redis requires --container")
        payload = collect_redis(args.container, key)
    else:
        if args.database is None: parser.error("collector requires --database")
        payload = collect_collector(args.database, key, args.retention_days)
    if args.include_key:
        payload["identity_hmac_key"] = key.hex()
    _atomic_json(None if args.output == "-" else Path(args.output), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

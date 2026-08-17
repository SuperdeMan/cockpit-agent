#!/usr/bin/env python3
"""Assemble and compare bounded store evidence without content or keys."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


MAX_CONTROL_BYTES = 16 * 1024 * 1024
TABLES = ("memory_item", "memory_relation", "reminder_item", "task_ledger",
          "proactive_delivery", "scene_item", "voiceprint")
TRANSITIONS = {
    "reminder_item.status": {
        "pending": {"pending", "fired", "done", "cancelled"},
        "fired": {"fired", "pending", "done", "cancelled"},
        "done": {"done"}, "cancelled": {"cancelled"},
    },
    "task_ledger.status": {
        "accepted": {"accepted", "running", "done", "failed", "cancelled", "orphaned"},
        "running": {"running", "done", "failed", "cancelled", "orphaned"},
        "orphaned": {"orphaned", "running", "done", "failed", "cancelled"},
        "done": {"done"}, "failed": {"failed"}, "cancelled": {"cancelled"},
    },
    "proactive_delivery.state": {
        "pending": {"pending", "dispatched", "presented", "dropped", "expired"},
        "dispatched": {"dispatched", "presented", "dropped", "expired"},
        "presented": {"presented"}, "dropped": {"dropped"}, "expired": {"expired"},
    },
    "scene_item.status": {"enabled": {"enabled", "disabled"},
                          "disabled": {"disabled", "enabled"}},
}


def _load(path: Path) -> dict[str, object]:
    if path.is_symlink() or path.stat().st_size > MAX_CONTROL_BYTES:
        raise ValueError("attestation control file is unsafe or too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("attestation control must be an object")
    return value


def _schema_fingerprint(schema: object) -> str:
    encoded = json.dumps(
        schema, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _exact_pre_start(manifest: dict[str, object], current: dict[str, object]) -> None:
    source_pg = manifest["postgres"]
    pg = current["postgres"]
    if (pg["tables"] != source_pg["tables"] or pg["states"] != source_pg["states"]
            or pg["schema_fingerprint"] != source_pg["schema_fingerprint"]):
        raise ValueError("PostgreSQL pre-start aggregate mismatch")
    source_identity = source_pg["source_identity"]
    for field in ("identity_sets", "logical_rows", "state_by_identity"):
        if pg[field] != source_identity[field]:
            raise ValueError("PostgreSQL pre-start keyed logical identity mismatch")

    source_redis = manifest["redis"]
    redis = current["redis"]
    if redis["version"] != source_redis["version"]:
        raise ValueError("Redis pre-start version mismatch")
    source_rows = source_redis["source_identity"]["rows"]
    target_rows = redis["rows"]
    if not set(target_rows).issubset(source_rows):
        raise ValueError("Redis pre-start contains an unknown identity")
    checked = redis["checked_at_ms"]
    for identity, source_record in source_rows.items():
        deadline = source_record["deadline_ms"]
        if deadline == -1 or deadline > checked:
            if target_rows.get(identity) != source_record:
                raise ValueError("Redis pre-start keyed logical identity mismatch")

    source_collector = manifest["collector"]
    collector = current["collector"]
    for field in ("user_version", "schema_fingerprint", "tables", "integrity_check"):
        if collector[field] != source_collector[field]:
            raise ValueError("Collector pre-start aggregate mismatch")
    source_rows = source_collector["source_identity"]["rows"]
    current_logical = {
        table: {identity: record["logical"] for identity, record in rows.items()}
        for table, rows in collector["rows"].items()
    }
    if current_logical != source_rows:
        raise ValueError("Collector pre-start keyed logical identity mismatch")


def _post_start(baseline: dict[str, object], current: dict[str, object]) -> None:
    old_pg = baseline["postgres"]
    pg = current["postgres"]
    if pg["schema_fingerprint"] != old_pg["schema_fingerprint"]:
        raise ValueError("PostgreSQL schema changed after start")
    for table in TABLES:
        old_rows = old_pg["logical_rows"][table]
        rows = pg["logical_rows"].get(table, {})
        for identity, logical in old_rows.items():
            if rows.get(identity) != logical:
                raise ValueError("PostgreSQL stable logical row disappeared or changed")
    for state_name, old_states in old_pg["state_by_identity"].items():
        states = pg["state_by_identity"].get(state_name, {})
        for identity, old_state in old_states.items():
            if states.get(identity) not in TRANSITIONS[state_name][old_state]:
                raise ValueError("PostgreSQL state transition is invalid")

    old_redis = baseline["redis"]
    redis = current["redis"]
    if redis["version"] != old_redis["version"]:
        raise ValueError("Redis version changed after start")
    for identity, record in old_redis["rows"].items():
        if record["deadline_ms"] == -1 and identity not in redis["rows"]:
            raise ValueError("persistent Redis identity disappeared")
        if record["deadline_ms"] > redis["checked_at_ms"] and identity not in redis["rows"]:
            raise ValueError("unexpired Redis identity disappeared")

    old_collector = baseline["collector"]
    collector = current["collector"]
    if (collector["user_version"] != old_collector["user_version"]
            or collector["schema_fingerprint"] != old_collector["schema_fingerprint"]):
        raise ValueError("Collector schema changed after start")
    deleted: dict[str, int] = {}
    for table, old_rows in old_collector["rows"].items():
        current_rows = collector["rows"].get(table, {})
        deleted[table] = 0
        for identity, record in old_rows.items():
            if identity in current_rows:
                if current_rows[identity] != record:
                    raise ValueError("Collector logical row changed")
                continue
            if record["protected"] or record["ts_ms"] >= collector["cleanup_cutoff_ms"]:
                raise ValueError("Collector deletion violates retention predicate")
            deleted[table] += 1
    collector["retention_deleted"] = deleted


def assemble(
    manifest_path: Path, pg_aggregate_path: Path, pg_identity_path: Path,
    redis_path: Path, collector_path: Path, stage: str, baseline_path: Path | None,
    migration_id: str,
) -> dict[str, object]:
    manifest = _load(manifest_path)
    pg_aggregate = _load(pg_aggregate_path)
    pg_identity = _load(pg_identity_path)
    redis = _load(redis_path)
    collector = _load(collector_path)
    schema = pg_aggregate.pop("schema")
    pg_aggregate["schema_fingerprint"] = _schema_fingerprint(schema)
    pg_aggregate.update(pg_identity)
    current = {"postgres": pg_aggregate, "redis": redis, "collector": collector}
    if stage == "pre-start":
        _exact_pre_start(manifest, current)
    elif stage == "post-start":
        if baseline_path is None:
            raise ValueError("post-start attestation requires a baseline")
        baseline = _load(baseline_path)
        if (baseline.get("schema_version") != 1 or baseline.get("migration_id") != migration_id
                or baseline.get("stage") != "pre-start"):
            raise ValueError("pre-start baseline is invalid")
        _post_start(baseline, current)
    else:
        raise ValueError("invalid attestation stage")
    return {"schema_version": 1, "migration_id": migration_id, "stage": stage, **current}


def _atomic(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pg-aggregate", type=Path, required=True)
    parser.add_argument("--pg-identity", type=Path, required=True)
    parser.add_argument("--redis", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--stage", choices=("pre-start", "post-start"), required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = assemble(
        args.manifest, args.pg_aggregate, args.pg_identity, args.redis, args.collector,
        args.stage, args.baseline, args.migration_id,
    )
    _atomic(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a durable backup manifest from streamed Redis identity evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TextIO


MAX_CONTROL_BYTES = 16 * 1024 * 1024


def load_redis_evidence(source: TextIO) -> dict[str, object]:
    encoded = source.read(MAX_CONTROL_BYTES + 1)
    if len(encoded.encode("utf-8")) > MAX_CONTROL_BYTES:
        raise ValueError("Redis evidence control JSON is too large")
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError("Redis evidence control JSON must be an object")
    return payload


def record(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise SystemExit("usage: build_backup_manifest.py TARGET STAMP PG REDIS COLLECTOR")
    target = Path(argv[1])
    redis = load_redis_evidence(sys.stdin)
    rows = redis["rows"]
    identity = {
        "digest_key": redis.pop("identity_hmac_key"),
        "persistent_digests": sorted(
            key for key, value in rows.items() if value["deadline_ms"] == -1
        ),
        "expiring_deadlines_ms": {
            key: value["deadline_ms"] for key, value in rows.items()
            if value["deadline_ms"] >= 0
        },
        "checked_at_ms": redis["checked_at_ms"],
    }
    aggregate = {key: redis[key] for key in ("prefixes", "types", "persistent", "expiring")}
    aggregate["key_count"] = len(rows)
    payload = {
        "schema_version": 1, "backup_stamp": argv[2],
        "redis_aggregate": aggregate, "redis_identity": identity,
        "files": {
            "postgres.dump": record(Path(argv[3])),
            "redis.rdb": record(Path(argv[4])),
            "collector.sql.gz": record(Path(argv[5])),
        },
    }
    partial = target.with_suffix(target.suffix + ".partial")
    descriptor = os.open(
        partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    try:
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(partial, target)
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import cloud_data_migration_lib as migration


VALID_ID = "20260816T010203Z-aaaaaaa-online"


def valid_manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "migration_id": VALID_ID,
        "phase": "online",
        "source_sha": "a" * 40,
        "created_at": "2026-08-16T01:02:03Z",
        "files": {
            name: {"size_bytes": 1, "sha256": "b" * 64}
            for name in ("postgres.dump", "redis.rdb", "collector.db")
        },
        "postgres": {},
        "redis": {},
        "collector": {},
    }


def test_migration_id_and_artifact_root_are_fail_closed(tmp_path: Path):
    migration_id = migration.new_migration_id(
        datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC),
        "a" * 40,
        "online",
    )
    assert migration_id == VALID_ID
    assert migration.artifact_directory(tmp_path, migration_id) == tmp_path / migration_id
    with pytest.raises(migration.MigrationError):
        migration.artifact_directory(tmp_path, "../escape")


def test_manifest_rejects_unknown_keys_and_bad_checksums():
    payload = valid_manifest_payload()
    payload["unexpected"] = True
    with pytest.raises(migration.MigrationError, match="unknown manifest keys"):
        migration.parse_manifest(payload)

    payload = valid_manifest_payload()
    payload["files"]["postgres.dump"]["sha256"] = "0" * 63  # type: ignore[index]
    with pytest.raises(migration.MigrationError, match="sha256"):
        migration.parse_manifest(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["files"].update({"../../secret": {"size_bytes": 1, "sha256": "b" * 64}}),
        lambda p: p["files"].pop("redis.rdb"),
        lambda p: p["files"]["redis.rdb"].update({"extra": 1}),
        lambda p: p["files"]["redis.rdb"].update({"size_bytes": True}),
        lambda p: p.update({"schema_version": True}),
    ],
)
def test_manifest_rejects_non_exact_nested_contract(mutation):
    payload = valid_manifest_payload()
    mutation(payload)
    with pytest.raises(migration.MigrationError):
        migration.parse_manifest(payload)


def test_atomic_private_json_writes_canonical_json(tmp_path: Path):
    target = tmp_path / "manifest.json"
    migration.atomic_private_json(target, {"b": 1, "a": "值"})
    assert target.read_bytes() == b'{"a":"\xe5\x80\xbc","b":1}\n'
    assert not target.with_name("manifest.json.partial").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": "值", "b": 1}


def test_manifest_round_trip_is_typed_and_immutable():
    manifest = migration.parse_manifest(valid_manifest_payload())
    assert manifest.migration_id == VALID_ID
    assert manifest.files["postgres.dump"].size_bytes == 1
    with pytest.raises(TypeError):
        manifest.files["postgres.dump"] = manifest.files["redis.rdb"]  # type: ignore[index]

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import cloud_data_migration_lib as migration


VALID_ID = "20260816T010203Z-aaaaaaa-online"


class FakeBinaryRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.stopped: set[str] = set()
        self.services = {
            "postgres": ("a" * 12, "postgres:16", "car-agent-postgres-data", "/var/lib/postgresql/data"),
            "redis": ("b" * 12, "redis:7", "car-agent-redis-data", "/data"),
            "observability-collector": ("c" * 12, "collector:test", "car-agent-obs-data", "/data"),
        }

    def _record(self, argv) -> tuple[str, ...]:
        call = tuple(str(part) for part in argv)
        self.calls.append(call)
        return call

    def text(self, argv, *, cwd: Path) -> str:
        call = self._record(argv)
        if "ps" in call and "-q" in call:
            return self.services[call[-1]][0] + "\n"
        if call[:2] == ("git", "rev-parse"):
            return "a" * 40 + "\n"
        if call == ("whoami",):
            return "TEST\\migration\n"
        if "config" in call and "--services" in call:
            return "postgres\nredis\nobservability-collector\ngateway-cloud\n"
        if "pg_restore" in call and "--list" in call:
            return "; Archive created at 2026-08-16\n"
        if "redis-check-rdb" in call:
            return "[offset 0] CRC64 checksum is OK\n"
        return ""

    def json(self, argv, *, cwd: Path):
        call = self._record(argv)
        if call[:2] != ("docker", "inspect"):
            raise AssertionError(call)
        container_id = call[-1]
        service = next(item for item in self.services.values() if item[0] == container_id)
        name = next(name for name, item in self.services.items() if item[0] == container_id)
        running = name not in self.stopped
        return [{
            "Id": container_id,
            "State": {"Running": running, "Restarting": False, "Status": "running" if running else "exited"},
            "Config": {"Image": service[1]},
            "Mounts": [{"Type": "volume", "Name": service[2], "Destination": service[3]}],
        }]

    def run(self, argv, *, cwd: Path):
        call = self._record(argv)
        if "stop" in call:
            self.stopped.update(call[call.index("stop") + 1:])
        if call[:2] == ("docker", "run"):
            mount = next((part for part in call if part.startswith("type=bind,source=")), None)
            if mount:
                source = Path(mount.split(",target=", 1)[0].split("source=", 1)[1])
                if "redis-cli" in call:
                    (source / "redis.rdb.partial").write_bytes(b"REDIS0011fixture")
                elif "python" in call:
                    connection = sqlite3.connect(source / "collector.db.partial")
                    connection.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY)")
                    connection.commit()
                    connection.close()
        return migration.CommandResult(call, 0, "", "")

    def stream_to_file(self, argv, target: Path, *, cwd: Path):
        self._record(argv)
        target.write_bytes(b"PGDMPfixture")


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


def test_capture_discovers_exact_active_services_without_mutating_stack(tmp_path: Path):
    runner = FakeBinaryRunner()
    bundle = migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="online",
        runner=runner,
        now=lambda: datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC),
    )
    assert bundle.manifest.phase == "online"
    forbidden = {"stop", "restart", "down", "rm", "kill", "pause"}
    assert not any(forbidden.intersection(call) for call in runner.calls)
    assert {path.name for path in bundle.files} == {
        "postgres.dump", "redis.rdb", "collector.db", "manifest.json", "status.json",
    }


def test_final_capture_stops_writers_and_leaves_them_stopped(tmp_path: Path):
    runner = FakeBinaryRunner()
    migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="final",
        quiesce_local=True,
        apply=True,
        runner=runner,
        now=lambda: datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC),
    )
    stop = next(call for call in runner.calls if "stop" in call)
    assert "postgres" not in stop and "redis" not in stop
    assert "observability-collector" in stop and "gateway-cloud" in stop
    assert not any("start" in call or "restart" in call for call in runner.calls)


def test_online_capture_rejects_mutating_flags(tmp_path: Path):
    with pytest.raises(migration.MigrationError, match="online"):
        migration.capture_local_snapshot(
            repo=tmp_path,
            artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
            phase="online",
            quiesce_local=True,
            apply=True,
            runner=FakeBinaryRunner(),
        )


def test_discovery_rejects_bind_mount(tmp_path: Path):
    runner = FakeBinaryRunner()
    original_json = runner.json
    def bad_json(argv, *, cwd):
        result = original_json(argv, cwd=cwd)
        if argv[-1] == "a" * 12:
            result[0]["Mounts"][0]["Type"] = "bind"
        return result
    runner.json = bad_json
    with pytest.raises(migration.MigrationError, match="named volume"):
        migration.discover_source_services(tmp_path, runner)

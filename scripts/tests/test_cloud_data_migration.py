from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import cloud_data_migration as cli
from scripts import cloud_data_migration_lib as migration
from scripts.cloud_release_lib import ReleaseError
from scripts.tests.process_timeout_support import (
    ProcessReadinessProbe,
    arm_communicate_timeout_after_ready,
    atomic_pid_ready_code,
)


VALID_ID = "20260816T010203Z-aaaaaaa-online"


def test_redis_rdb_validation_accepts_legacy_and_redis_7_success_markers():
    assert migration._redis_rdb_check_succeeded("CRC64 checksum is OK")
    assert migration._redis_rdb_check_succeeded("[offset 42] Checksum OK")
    assert not migration._redis_rdb_check_succeeded("RDB looks broken")


def test_collector_identity_limit_fits_existing_corpus_without_raising_other_store_limits():
    assert migration.MAX_COLLECTOR_IDENTITY_ITEMS == 50_000
    assert migration.MAX_IDENTITY_ITEMS == 20_000
    assert migration.CONTROL_JSON_MAX_ITEMS == 600_000


def test_wait_for_nonempty_local_file_tolerates_delayed_docker_bind_sync(tmp_path, monkeypatch):
    target = tmp_path / "collector.db.partial"
    target.touch()
    sleeps = []

    def make_visible(delay):
        sleeps.append(delay)
        target.write_bytes(b"sqlite")

    monkeypatch.setattr(migration.time, "sleep", make_visible)

    migration._wait_for_nonempty_local_file(target, attempts=2, delay_s=0.01)

    assert sleeps == [0.01]


class FakeBinaryRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.stopped: set[str] = set()
        self.writer_ids = {"gateway-cloud": "d" * 12}
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
            if call[-1] in self.writer_ids:
                return self.writer_ids[call[-1]] + "\n"
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
        if "psql" in call:
            return {
                "major": "16", "vector_version": "0.7.4",
                "tables": {
                    "memory_item": 370, "memory_relation": 12, "reminder_item": 57,
                    "task_ledger": 4, "proactive_delivery": 2, "scene_item": 1,
                    "voiceprint": 0, "agents": 22, "agent_capability_vec": 307,
                },
                "states": {
                    "reminder_item.status": {"pending": 57},
                    "task_ledger.status": {"done": 4},
                    "proactive_delivery.state": {"presented": 2},
                    "scene_item.status": {"enabled": 1},
                },
                "columns": [["memory_item", "id", 1, "text", "text", "NO", None]],
                "primary_keys": [["memory_item", "memory_item_pkey", "id"]],
                "indexes": [["memory_item", "memory_item_pkey", "CREATE UNIQUE INDEX memory_item_pkey ON public.memory_item USING btree (id)"]],
            }
        if "redis-cli" in call and "--json" in call:
            return {
                "version": "7.2.5", "rdb_version": 11, "key_count": 3271,
                "prefixes": {"memory": 3000, "session": 271},
                "types": {"hash": 3000, "string": 271},
                "persistent": 2700, "expiring": 571, "min_ttl_ms": 100,
                "max_ttl_ms": 900000,
            }
        if call[:2] != ("docker", "inspect"):
            raise AssertionError(call)
        container_id = call[-1]
        if container_id in self.writer_ids.values():
            name = next(name for name, item in self.writer_ids.items() if item == container_id)
            service = (container_id, "writer:test", "unused", "/unused")
        else:
            service = next(item for item in self.services.values() if item[0] == container_id)
            name = next(name for name, item in self.services.items() if item[0] == container_id)
        running = name not in self.stopped
        return [{
            "Id": container_id,
            "Image": "sha256:" + container_id.ljust(64, "0"),
            "State": {"Running": running, "Restarting": False, "Status": "running" if running else "exited"},
            "Config": {"Image": service[1]},
            "Mounts": [{"Type": "volume", "Name": service[2], "Destination": service[3]}],
        }]

    def run(self, argv, *, cwd: Path):
        call = self._record(argv)
        if any(str(part).endswith("store_identity_evidence.py") for part in call):
            store = call[call.index(next(part for part in call if str(part).endswith("store_identity_evidence.py"))) + 1]
            output = Path(call[call.index("--output") + 1])
            def dig(label: str) -> str:
                return hashlib.sha256(label.encode()).hexdigest()
            if store == "postgres":
                counts = {
                    "memory_item": 370, "memory_relation": 12, "reminder_item": 57,
                    "task_ledger": 4, "proactive_delivery": 2, "scene_item": 1,
                    "voiceprint": 0,
                }
                identity_sets = {table: [dig(f"{table}:{i}") for i in range(count)] for table, count in counts.items()}
                logical = {table: {item: dig(f"row:{item}") for item in values} for table, values in identity_sets.items()}
                states = {
                    "reminder_item.status": {item: "pending" for item in identity_sets["reminder_item"]},
                    "task_ledger.status": {item: "done" for item in identity_sets["task_ledger"]},
                    "proactive_delivery.state": {item: "presented" for item in identity_sets["proactive_delivery"]},
                    "scene_item.status": {item: "enabled" for item in identity_sets["scene_item"]},
                }
                payload = {"identity_sets": identity_sets, "logical_rows": logical, "state_by_identity": states}
            elif store == "redis":
                rows = {}
                for index in range(3271):
                    rows[dig(f"redis:{index}")] = {
                        "logical": dig(f"redis-row:{index}"),
                        "deadline_ms": -1 if index < 2700 else 2_000_000,
                    }
                payload = {"rows": rows, "checked_at_ms": 1_000_000,
                           "prefixes": {"memory": 3000, "session": 271},
                           "types": {"hash": 3000, "string": 271}}
            else:
                database = Path(call[call.index("--database") + 1])
                collector_rows = {}
                with sqlite3.connect(database) as connection:
                    for table in migration.COLLECTOR_TABLES:
                        values = connection.execute(f'SELECT id FROM "{table}"').fetchall()
                        collector_rows[table] = {}
                        for value in values:
                            identity = dig(f"collector:{table}:{value[0]}")
                            collector_rows[table][identity] = {
                                "logical": dig(f"collector-row:{table}:{value[0]}"),
                                "ts_ms": 0, "protected": False,
                                "relation": dig(f"collector-relation:{table}:{value[0]}"),
                            }
                payload = {"rows": collector_rows}
            output.write_text(json.dumps(payload), encoding="utf-8")
            return migration.CommandResult(call, 0, "", "")
        if "stop" in call:
            self.stopped.update(call[call.index("stop") + 1:])
        if "start" in call:
            started = set(call[call.index("start") + 1:])
            self.stopped.difference_update(started)
            self.stopped.difference_update(
                name for name, container_id in self.writer_ids.items() if container_id in started
            )
        if call[:2] == ("docker", "run"):
            mount = next((part for part in call if part.startswith("type=bind,source=")), None)
            if mount:
                source = Path(mount.split(",target=", 1)[0].split("source=", 1)[1])
                if "redis-cli" in call:
                    (source / "redis.rdb.partial").write_bytes(b"REDIS0011fixture")
                elif "python" in call:
                    connection = sqlite3.connect(source / "collector.db.partial")
                    for table in ("turns", "spans", "llm_calls", "logs"):
                        connection.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY)")
                    connection.commit()
                    connection.close()
        return migration.CommandResult(call, 0, "", "")

    def stream_to_file(self, argv, target: Path, *, cwd: Path):
        self._record(argv)
        target.write_bytes(b"PGDMPfixture")

    def run_from_file(self, argv, source: Path, *, cwd: Path):
        call = self._record(argv)
        assert source.is_file()
        return migration.CommandResult(call, 0, "", "")


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
        "postgres": {
            "major": "16", "vector_version": "0.7.4",
            "tables": {name: 0 for name in migration.BUSINESS_TABLES + migration.DERIVED_TABLES},
            "states": {name: {} for name in migration.POSTGRES_STATE_COLUMNS},
            "schema_fingerprint": "c" * 64, "archive_fingerprint": "d" * 64,
            "source_identity": {
                "identity_sets": {name: [] for name in migration.BUSINESS_TABLES},
                "logical_rows": {name: {} for name in migration.BUSINESS_TABLES},
                "state_by_identity": {name: {} for name in migration.POSTGRES_STATE_COLUMNS},
            },
        },
        "redis": {
            "version": "7.2.5", "rdb_version": 11, "key_count": 0,
            "prefixes": {}, "types": {}, "persistent": 0, "expiring": 0,
            "min_ttl_ms": 0, "max_ttl_ms": 0, "rdb_sha256": "b" * 64,
            "source_identity": {"rows": {}, "checked_at_ms": 0},
        },
        "collector": {
            "user_version": 0, "schema_fingerprint": "e" * 64,
            "tables": {name: 0 for name in migration.COLLECTOR_TABLES},
            "integrity_check": "ok",
            "source_identity": {"rows": {name: {} for name in migration.COLLECTOR_TABLES}},
        },
        "identity_hmac_key": "f" * 64,
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


def test_manifest_rejects_unknown_aggregate_keys_and_non_utc_time():
    payload = valid_manifest_payload()
    payload["postgres"] = {
        "major": "16", "vector_version": "0.7.4", "tables": {}, "states": {},
        "schema_fingerprint": "a" * 64, "archive_fingerprint": "b" * 64,
        "unexpected": True,
    }
    with pytest.raises(migration.MigrationError):
        migration.parse_manifest(payload)
    payload = valid_manifest_payload()
    payload["created_at"] = "2026-08-16T09:02:03+08:00"
    with pytest.raises(migration.MigrationError, match="created_at"):
        migration.parse_manifest(payload)


def test_manifest_rejects_unknown_production_state_even_when_counts_balance():
    payload = valid_manifest_payload()
    payload["postgres"]["tables"]["reminder_item"] = 1
    payload["postgres"]["states"]["reminder_item.status"] = {"expired": 1}
    with pytest.raises(migration.MigrationError, match="production contract"):
        migration.parse_manifest(payload)


@pytest.mark.parametrize("store", ["postgres", "redis", "collector"])
def test_manifest_rejects_identity_counts_above_pre_destructive_bound(store: str):
    payload = valid_manifest_payload()
    if store == "postgres":
        payload["postgres"]["tables"]["memory_item"] = 20_001
    elif store == "redis":
        payload["redis"].update({
            "key_count": 20_001, "persistent": 20_001, "expiring": 0,
            "prefixes": {"memory": 20_001}, "types": {"string": 20_001},
        })
    else:
        payload["collector"]["tables"]["turns"] = 50_001
    with pytest.raises(migration.MigrationError, match="identity count limit"):
        migration.parse_manifest(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("created_at", "2026-02-30T01:02:03Z"),
        ("created_at", "2026-08-16T01:02:03.000000Z"),
    ],
)
def test_manifest_accepts_only_v1_and_canonical_real_utc(field, value):
    payload = valid_manifest_payload()
    payload[field] = value
    with pytest.raises(migration.MigrationError, match=field):
        migration.parse_manifest(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_sha", "b" * 40), ("created_at", "2026-08-16T01:02:04Z")],
)
def test_manifest_binds_migration_id_to_source_short_sha_and_creation_second(field, value):
    payload = valid_manifest_payload()
    payload[field] = value
    with pytest.raises(migration.MigrationError, match="migration id"):
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
    forbidden = {"stop", "restart", "down", "kill", "pause"}
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
    inspected = {call[-1] for call in runner.calls if call[:2] == ("docker", "inspect")}
    assert runner.writer_ids["gateway-cloud"] in inspected


def test_snapshot_rejects_cloud_target_before_any_docker_call(tmp_path: Path):
    (tmp_path / "dev-stack.local").write_text("target=cloud\n", encoding="ascii")
    runner = FakeBinaryRunner()
    with pytest.raises(migration.MigrationError, match="local development stack"):
        migration.capture_local_snapshot(
            repo=tmp_path,
            artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
            phase="online",
            runner=runner,
        )
    assert not any(call and call[0] == "docker" for call in runner.calls)


def test_final_dry_run_rejects_cloud_target_before_docker(tmp_path: Path):
    (tmp_path / "dev-stack.local").write_text("target=cloud\n", encoding="ascii")
    runner = FakeBinaryRunner()
    rc = cli.main(["snapshot", "--phase", "final"], runner=runner, repo=tmp_path)
    assert rc == 2
    assert not any(call and call[0] == "docker" for call in runner.calls)


def test_snapshot_uses_locked_image_digest_and_never_pulls(tmp_path: Path):
    runner = FakeBinaryRunner()
    migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="online",
        runner=runner,
        now=lambda: datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC),
    )
    docker_runs = [call for call in runner.calls if call[:2] == ("docker", "run")]
    assert docker_runs
    assert all("--pull" in call and call[call.index("--pull") + 1] == "never" for call in docker_runs)
    assert all(any(part.startswith("sha256:") for part in call) for call in docker_runs)
    collector_run = next(call for call in docker_runs if "collector.db.partial" in call[-1])
    assert "--volumes-from" not in collector_run
    assert "type=volume,source=car-agent-obs-data,target=/data,readonly" in collector_run
    assert "mode=ro&immutable=1" in collector_run[-1]


def test_final_resolves_all_writer_identity_before_stop_and_recovers_on_failure(tmp_path: Path):
    class FailingRunner(FakeBinaryRunner):
        def stream_to_file(self, argv, target: Path, *, cwd: Path):
            self._record(argv)
            raise migration.MigrationError("injected pg dump failure")

    runner = FailingRunner()
    with pytest.raises(migration.MigrationError, match="injected"):
        migration.capture_local_snapshot(
            repo=tmp_path,
            artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
            phase="final",
            quiesce_local=True,
            apply=True,
            runner=runner,
            now=lambda: datetime(2026, 8, 17, 1, 2, 3, 987654, tzinfo=UTC),
        )
    stop_index = next(i for i, call in enumerate(runner.calls) if "stop" in call)
    for writer_id in runner.writer_ids.values():
        assert any(call[:2] == ("docker", "inspect") and call[-1] == writer_id
                   for call in runner.calls[:stop_index])
    assert any(call[:2] == ("docker", "start") and runner.writer_ids["gateway-cloud"] in call
               for call in runner.calls)


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("snapshot failed")])
def test_final_recovers_exact_writer_ids_for_baseexception(tmp_path: Path, failure: BaseException):
    class FailingRunner(FakeBinaryRunner):
        def stream_to_file(self, argv, target: Path, *, cwd: Path):
            self._record(argv)
            raise failure

    runner = FailingRunner()
    with pytest.raises(type(failure)):
        migration.capture_local_snapshot(
            repo=tmp_path,
            artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
            phase="final", quiesce_local=True, apply=True, runner=runner,
            now=lambda: datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC),
        )
    recovery = next(call for call in runner.calls if call[:2] == ("docker", "start"))
    expected = set(runner.writer_ids.values()) | {runner.services["observability-collector"][0]}
    assert set(recovery[2:]) == expected


def test_partial_stop_failure_still_recovers_and_preserves_original_error(tmp_path: Path):
    class PartialStopRunner(FakeBinaryRunner):
        def run(self, argv, *, cwd: Path):
            call = self._record(argv)
            if "stop" in call:
                self.stopped.add("gateway-cloud")
                raise migration.MigrationError("partial stop original")
            if call[:2] == ("docker", "start"):
                self.stopped.clear()
                return migration.CommandResult(call, 0, "", "")
            return super().run(argv, cwd=cwd)

    runner = PartialStopRunner()
    with pytest.raises(migration.MigrationError, match="partial stop original"):
        migration.capture_local_snapshot(
            repo=tmp_path,
            artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
            phase="final", quiesce_local=True, apply=True, runner=runner,
            now=lambda: datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC),
        )
    assert any(call[:2] == ("docker", "start") for call in runner.calls)


def test_snapshot_timestamp_is_canonical_utc_seconds(tmp_path: Path):
    bundle = migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="online",
        runner=FakeBinaryRunner(),
        now=lambda: datetime(2026, 8, 16, 1, 2, 3, 987654, tzinfo=UTC),
    )
    assert bundle.manifest.created_at == "2026-08-16T01:02:03Z"
    assert datetime.fromisoformat(bundle.manifest.created_at.replace("Z", "+00:00")).microsecond == 0


def test_artifact_directory_rejects_symlink_or_junction_ancestor(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink is unavailable")
    with pytest.raises(migration.MigrationError, match="link|junction|reparse"):
        migration.artifact_directory(linked, VALID_ID)


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


def test_manifest_contains_only_private_keyed_evidence(tmp_path: Path):
    directory = tmp_path / VALID_ID
    directory.mkdir()
    for name, content in {
        "postgres.dump": b"PGDMPfixture",
        "redis.rdb": b"REDIS0011fixture",
    }.items():
        (directory / name).write_bytes(content)
    connection = sqlite3.connect(directory / "collector.db")
    for table, count in {"turns": 3, "spans": 2, "llm_calls": 1, "logs": 4}.items():
        connection.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY)")
        connection.executemany(f"INSERT INTO {table} DEFAULT VALUES", [()] * count)
    connection.commit()
    connection.close()
    runner = FakeBinaryRunner()
    services = migration.discover_source_services(tmp_path, runner)
    identity_hmac_key = "f" * 64
    (directory / "status.json").write_text(json.dumps({
        "identity_hmac_key": identity_hmac_key,
    }), encoding="utf-8")
    evidence = migration.collect_aggregate_evidence(
        tmp_path, services, runner, directory, "d" * 64, identity_hmac_key
    )
    manifest = migration.build_manifest(
        migration.SnapshotEvidence(
            directory=directory,
            migration_id=VALID_ID,
            phase="online",
            source_sha="a" * 40,
            created_at="2026-08-16T01:02:03Z",
            identity_hmac_key=identity_hmac_key,
            **evidence,
        )
    )
    encoded = json.dumps(migration.manifest_payload(manifest), ensure_ascii=False)
    assert manifest.postgres["tables"]["memory_item"] == 370
    assert manifest.postgres["tables"]["voiceprint"] == 0
    assert manifest.postgres["states"]["reminder_item.status"] == {"pending": 57}
    assert manifest.redis["key_count"] == 3271
    assert manifest.collector["tables"]["turns"] == 3
    assert len(manifest.postgres["source_identity"]["identity_sets"]["memory_item"]) == 370
    assert len(manifest.redis["source_identity"]["rows"]) == 3271
    assert len(manifest.collector["source_identity"]["rows"]["turns"]) == 3
    assert set(manifest.postgres["tables"]) == set(migration.BUSINESS_TABLES + migration.DERIVED_TABLES)
    for private in ("user text", "session value", "api-token-value"):
        assert private not in encoded
    assert all("SELECT *" not in " ".join(call) for call in runner.calls)


def test_private_source_identity_and_hmac_key_are_not_emitted_in_cli_summary():
    manifest = migration.parse_manifest(valid_manifest_payload())
    summaries = {
        store: cli._public_store_summary(getattr(manifest, store))
        for store in ("postgres", "redis", "collector")
    }
    encoded = json.dumps(summaries)
    assert "source_identity" not in encoded
    assert manifest.identity_hmac_key not in encoded


def test_local_runner_timeout_terminates_grandchild_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    marker = tmp_path / "local-grandchild-survived.txt"
    pid_file = tmp_path / "local-grandchild.pid"
    ready_file = tmp_path / "local-grandchild.ready"
    child = (
        "import time; from pathlib import Path; time.sleep(600); "
        f"Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"descendant=subprocess.Popen([sys.executable, '-c', {child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL); "
        + atomic_pid_ready_code(pid_file, ready_file)
        + "time.sleep(600)"
    )
    original_popen = migration.subprocess.Popen
    processes = []
    arms = []
    probe = ProcessReadinessProbe(pid_file, ready_file)

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        arms.append(arm_communicate_timeout_after_ready(process, probe))
        return process

    monkeypatch.setattr(migration.subprocess, "Popen", popen)
    verified = False
    try:
        runner = migration.LocalCommandRunner(command_timeout_s=30)
        with pytest.raises(migration.MigrationError, match="timed out"):
            runner.run([sys.executable, "-c", parent], cwd=tmp_path)
        assert len(arms) == 1
        assert arms[0].triggered
        assert arms[0].timeout_injections == 1
        probe.assert_exited()
        assert not marker.exists()
        verified = True
    finally:
        if not verified:
            probe.force_cleanup()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=5)
                except migration.subprocess.TimeoutExpired:
                    process.kill()
        probe.close()


def _identity_evidence() -> tuple[dict[str, object], dict[str, object]]:
    baseline = {
        "postgres": {
            "identity_sets": {"reminder_item": ["a" * 64], "task_ledger": ["b" * 64]},
            "state_by_identity": {
                "reminder_item.status": {"a" * 64: "pending"},
                "task_ledger.status": {"b" * 64: "accepted"},
                "proactive_delivery.state": {"c" * 64: "pending"},
                "scene_item.status": {"d" * 64: "enabled"},
            },
        },
        "redis": {
            "persistent_digests": ["e" * 64],
            "expiring_deadlines_ms": {"f" * 64: 2_000},
        },
        "collector": {
            "rows": {
                "turns": {
                    "1" * 64: {"ts_ms": 900, "protected": False, "relation": "1" * 64},
                    "2" * 64: {"ts_ms": 100, "protected": True, "relation": "2" * 64},
                },
                "spans": {
                    "3" * 64: {"ts_ms": 100, "protected": False, "relation": "4" * 64},
                },
                "llm_calls": {}, "logs": {},
            },
            "cleanup_cutoff_ms": 500,
        },
    }
    current = json.loads(json.dumps(baseline))
    current["postgres"]["state_by_identity"]["reminder_item.status"]["a" * 64] = "fired"
    current["postgres"]["state_by_identity"]["task_ledger.status"]["b" * 64] = "running"
    current["redis"]["expiring_deadlines_ms"] = {}
    current["collector"]["cleanup_cutoff_ms"] = 800
    return baseline, current


def test_identity_evidence_allows_only_declared_state_ttl_and_retention_changes():
    baseline, current = _identity_evidence()
    migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)


@pytest.mark.parametrize(
    ("field", "before", "after"),
    [
        ("reminder_item.status", "pending", "done"),
        ("proactive_delivery.state", "pending", "presented"),
        ("task_ledger.status", "orphaned", "done"),
        ("task_ledger.status", "orphaned", "failed"),
        ("task_ledger.status", "orphaned", "cancelled"),
    ],
)
def test_post_start_state_transitions_cover_production_terminal_update_paths(
    field: str, before: str, after: str,
):
    baseline, current = _identity_evidence()
    digest = next(iter(baseline["postgres"]["state_by_identity"][field]))
    baseline["postgres"]["state_by_identity"][field][digest] = before
    current["postgres"]["state_by_identity"][field][digest] = after
    migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)


def test_migration_state_contract_is_generated_from_production_constants():
    def constants(relative: str) -> dict[str, str]:
        tree = ast.parse((Path(__file__).parents[2] / relative).read_text(encoding="utf-8"))
        values: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
                for name, value in zip(target.elts, node.value.elts, strict=True):
                    if isinstance(name, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                        values[name.id] = value.value
        return values

    reminder_store = constants("agents/reminder/src/store.py")
    ledger = constants("agents/_sdk/ledger.py")
    delivery_store = constants("proactive/delivery_store.py")
    scene_store = constants("agents/scene_orchestrator/src/store.py")
    assert migration.POSTGRES_ALLOWED_STATES["reminder_item.status"] == {
        reminder_store[name] for name in ("PENDING", "FIRED", "DONE", "CANCELLED")
    }
    assert migration.POSTGRES_ALLOWED_STATES["task_ledger.status"] == {
        ledger[name] for name in ("ACCEPTED", "RUNNING", "DONE", "FAILED", "CANCELLED", "ORPHANED")
    }
    assert migration.POSTGRES_ALLOWED_STATES["proactive_delivery.state"] == {
        delivery_store[name] for name in ("PENDING", "DISPATCHED", "PRESENTED", "DROPPED", "EXPIRED")
    }
    assert migration.POSTGRES_ALLOWED_STATES["scene_item.status"] == {
        scene_store[name] for name in ("ENABLED", "DISABLED")
    }


def test_identity_evidence_rejects_equal_count_identity_swap_and_illegal_state_change():
    baseline, current = _identity_evidence()
    current["postgres"]["identity_sets"]["reminder_item"] = ["9" * 64]
    with pytest.raises(migration.MigrationError, match="PostgreSQL identity"):
        migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)
    baseline, current = _identity_evidence()
    current["postgres"]["state_by_identity"]["task_ledger.status"]["b" * 64] = "accepted"
    current["postgres"]["state_by_identity"]["task_ledger.status"]["b" * 64] = "cancelled"
    # cancelled is legal from accepted; a terminal-to-running reversal is not.
    baseline["postgres"]["state_by_identity"]["task_ledger.status"]["b" * 64] = "done"
    current["postgres"]["state_by_identity"]["task_ledger.status"]["b" * 64] = "running"
    with pytest.raises(migration.MigrationError, match="state transition"):
        migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)


def test_identity_evidence_rejects_unexpired_redis_loss_and_nonretention_collector_loss():
    baseline, current = _identity_evidence()
    with pytest.raises(migration.MigrationError, match="unexpired Redis"):
        migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=1_999)
    baseline, current = _identity_evidence()
    del current["collector"]["rows"]["turns"]["1" * 64]
    with pytest.raises(migration.MigrationError, match="retention predicate"):
        migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)
    baseline, current = _identity_evidence()
    del current["collector"]["rows"]["turns"]["2" * 64]
    with pytest.raises(migration.MigrationError, match="protected Collector"):
        migration.verify_post_start_identity_evidence(baseline, current, checked_at_ms=2_001)


def test_snapshot_validation_and_probes_use_readonly_batch_mounts(tmp_path: Path):
    runner = FakeBinaryRunner()
    migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="online",
        runner=runner,
        now=lambda: datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC),
    )
    joined = [" ".join(call) for call in runner.calls]
    assert any("target=/snapshot,readonly" in call and "/snapshot/postgres.dump" in call for call in joined)
    assert any("target=/snapshot,readonly" in call and "/snapshot/redis.rdb" in call for call in joined)
    aggregate_calls = [call for call in runner.calls if "--command" in call or "EVAL" in call]
    assert aggregate_calls
    assert all("a" * 12 not in call and "b" * 12 not in call for call in aggregate_calls)


def test_plan_rejects_source_and_store_compatibility_mismatch(tmp_path: Path):
    directory = _write_valid_bundle(tmp_path)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["postgres"] = {
        "major": "16", "vector_version": "0.7.4", "tables": {
            name: 0 for name in migration.BUSINESS_TABLES + migration.DERIVED_TABLES
            }, "states": {name: {} for name in migration.POSTGRES_STATE_COLUMNS},
            "schema_fingerprint": "c" * 64, "archive_fingerprint": "d" * 64,
            "source_identity": valid_manifest_payload()["postgres"]["source_identity"],
    }
    payload["redis"] = {
        "version": "7.2.5", "rdb_version": 11, "key_count": 0, "prefixes": {},
        "types": {}, "persistent": 0, "expiring": 0, "min_ttl_ms": 0,
            "max_ttl_ms": 0, "rdb_sha256": payload["files"]["redis.rdb"]["sha256"],
            "source_identity": valid_manifest_payload()["redis"]["source_identity"],
    }
    payload["collector"] = {
        "user_version": 0, "schema_fingerprint": "e" * 64,
            "tables": {name: 0 for name in migration.COLLECTOR_TABLES}, "integrity_check": "ok",
            "source_identity": valid_manifest_payload()["collector"]["source_identity"],
    }
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    remote = dict(FakeRemoteRunner().remote_payload)
    remote["current_release"] = "/opt/car-agent/releases/" + "b" * 40
    runner = FakeRemoteRunner(remote)
    request = migration.MigrationRequest(
        repo=tmp_path, migration_id=VALID_ID, bundle=migration.load_bundle(tmp_path, VALID_ID),
        ssh=cli._ssh_config(cli.build_parser().parse_args([
            "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
            "plan", "--migration-id", VALID_ID,
        ])),
    )
    with pytest.raises(migration.MigrationError, match="source release"):
        migration.make_migration_plan(request, runner)


@pytest.mark.parametrize(
    ("key", "expected"),
    [(b"memory:user:1", "memory"), (b"bad.prefix:x", "other"), (b"\xff:x", "other")],
)
def test_redis_prefix_is_bounded_and_redacted(key: bytes, expected: str):
    assert migration.redis_prefix(key) == expected


def test_aggregate_probes_are_executable_and_fixed_scope():
    assert "fixed table counts" not in migration.POSTGRES_AGGREGATE_SQL
    assert "json_build_object" in migration.POSTGRES_AGGREGATE_SQL
    assert "information_schema.columns" in migration.POSTGRES_AGGREGATE_SQL
    assert all(name in migration.POSTGRES_AGGREGATE_SQL for name in migration.BUSINESS_TABLES)
    assert "cloud-migration aggregate" not in migration.REDIS_AGGREGATE_LUA
    assert "redis.setresp(3)" in migration.REDIS_AGGREGATE_LUA
    assert migration.REDIS_AGGREGATE_LUA.index('redis.call("INFO"') < migration.REDIS_AGGREGATE_LUA.index("redis.setresp(3)")
    assert "version=version" in migration.REDIS_AGGREGATE_LUA
    assert 'redis.call("SCAN"' in migration.REDIS_AGGREGATE_LUA
    assert 'redis.call("PTTL"' in migration.REDIS_AGGREGATE_LUA


class FakeRemoteRunner:
    def __init__(self, remote_payload: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.remote_payload = remote_payload or {
            "current_release": "/opt/car-agent/releases/" + "a" * 40,
            "runtime_project_name": "car-agent",
            "disk_available_bytes": 10**12,
            "stores": {
                "postgres": {"major": "16", "vector_version": "0.7.4",
                             "schema_fingerprint": "c" * 64, "running": True},
                "redis": {"version": "7.2.5", "schema_fingerprint": "f" * 64,
                          "running": True},
                "collector": {"user_version": 0, "schema_fingerprint": "e" * 64,
                              "running": True},
            },
            "status": "inspect_only",
        }

    def run(self, argv, *, cwd: Path, **kwargs):
        call = tuple(str(part) for part in argv)
        self.calls.append(call)
        command = call[-1]
        stdout = ""
        if "inspect-current" in command:
            stdout = json.dumps(self.remote_payload)
        elif "prepare-upload" in command:
            stdout = f"/opt/car-agent/shared/imports/{VALID_ID}\n"
        elif " apply " in f" {command} ":
            stdout = json.dumps({"migration_id": VALID_ID, "status": "APPLIED"})
        elif " rollback " in f" {command} ":
            stdout = json.dumps({"migration_id": VALID_ID, "status": "ROLLED_BACK"})
        elif " recover " in f" {command} ":
            stdout = json.dumps({"migration_id": VALID_ID, "status": "ROLLED_BACK"})
        elif " rollback-plan " in f" {command} ":
            stdout = json.dumps({
                "schema_version": 1, "migration_id": VALID_ID, "status": "APPLIED",
                "journal_state": "APPLIED", "operation_id": "b" * 32,
                "current_release": "/opt/car-agent/releases/" + "a" * 40,
                "backup_stamp": "20260817T010203Z",
                "backup_files": {
                    name: {"size_bytes": 12, "sha256": char * 64}
                    for name, char in (("postgres.dump", "a"), ("redis.rdb", "b"),
                                       ("collector.sql.gz", "c"))
                },
                "would_stop": ["gateway-cloud", "observability-collector"],
            })
        return migration.CommandResult(call, 0, stdout, "")


def _write_valid_bundle(root: Path) -> Path:
    directory = root / ".artifacts" / "cloud-data-migrations" / VALID_ID
    directory.mkdir(parents=True)
    for name, content in {
        "postgres.dump": b"PGDMPfixture",
        "redis.rdb": b"REDIS0011fixture",
        "collector.db": b"SQLite format 3\x00fixture",
    }.items():
        (directory / name).write_bytes(content)
    payload = valid_manifest_payload()
    for name in migration.SNAPSHOT_FILENAMES:
        payload["files"][name] = {
            "size_bytes": (directory / name).stat().st_size,
            "sha256": migration.sha256_file(directory / name),
        }
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _fake_key(root: Path) -> Path:
    key = root / "id_ed25519"
    key.write_text("synthetic-test-key", encoding="utf-8")
    return key


def test_apply_is_dry_run_without_explicit_flag(tmp_path: Path):
    _write_valid_bundle(tmp_path)
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        "apply", "--migration-id", VALID_ID,
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    joined = [" ".join(call) for call in runner.calls]
    assert any("inspect-current" in call for call in joined)
    assert not any(" prepare-upload " in call or " apply " in call for call in joined)
    assert not any(call[0] == "scp" for call in runner.calls)


@pytest.mark.parametrize("value", ["../x", "x;id", "x$(id)", "", "A" * 90])
def test_cli_rejects_unsafe_migration_id(value: str):
    with pytest.raises(migration.MigrationError):
        migration.require_migration_id(value)


def test_recover_cli_is_dry_run_without_apply_and_requires_final_remote_status(tmp_path: Path, capsys):
    _write_valid_bundle(tmp_path)
    key = _fake_key(tmp_path)
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(key),
        "recover", "--migration-id", VALID_ID,
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert len(runner.calls) == 1
    assert " rollback-plan " in f" {runner.calls[0][-1]} "
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"

    rc = cli.main([
        "--host", "cloud.example", "--identity", str(key),
        "recover", "--migration-id", VALID_ID, "--apply",
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert " recover " in f" {runner.calls[-1][-1]} "
    assert json.loads(capsys.readouterr().out)["status"] == "ROLLED_BACK"


@pytest.mark.parametrize(
    ("command", "extra", "remote_token"),
    [
        ("verify", (), " verify "),
        ("rollback", (), " rollback-plan "),
        ("rollback", ("--apply",), " rollback "),
        ("recover", (), " rollback-plan "),
        ("recover", ("--apply",), " recover "),
    ],
)
def test_remote_authoritative_actions_do_not_require_local_artifact_bundle(
    tmp_path: Path, command: str, extra: tuple[str, ...], remote_token: str,
):
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        command, "--migration-id", VALID_ID, *extra,
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert len(runner.calls) == 1
    assert remote_token in f" {runner.calls[0][-1]} "


def test_rollback_dry_run_reads_real_server_plan_without_remote_write(tmp_path: Path, capsys):
    _write_valid_bundle(tmp_path)
    key = _fake_key(tmp_path)
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(key),
        "rollback", "--migration-id", VALID_ID,
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert len(runner.calls) == 1
    assert " rollback-plan " in f" {runner.calls[0][-1]} "
    payload = json.loads(capsys.readouterr().out)
    assert payload["backup_stamp"] == "20260817T010203Z"
    assert payload["backup_files"]["redis.rdb"]["sha256"] == "b" * 64
    assert payload["would_stop"] == ["gateway-cloud", "observability-collector"]


@pytest.mark.parametrize("state", ["STOPPING_WRITERS", "STOP_FAILED", "BACKUP_FAILED"])
def test_recover_dry_run_accepts_audited_no_backup_state(
    tmp_path: Path, capsys, state: str,
):
    runner = FakeRemoteRunner()

    def no_backup_run(argv, *, cwd: Path, **kwargs):
        call = tuple(str(part) for part in argv)
        runner.calls.append(call)
        return migration.CommandResult(call, 0, json.dumps({
            "schema_version": 1,
            "migration_id": VALID_ID,
            "status": state,
            "journal_state": state,
            "operation_id": "d" * 32,
            "current_release": "/opt/car-agent/releases/" + "a" * 40,
            "backup_stamp": None,
            "backup_files": None,
            "would_stop": ["gateway-cloud", "observability-collector"],
        }), "")

    runner.run = no_backup_run
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        "recover", "--migration-id", VALID_ID,
    ], runner=runner, repo=tmp_path)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["journal_state"] == state
    assert payload["backup_stamp"] is None
    assert payload["backup_files"] is None


def test_recover_apply_accepts_terminal_recovered_without_replace(tmp_path: Path, capsys):
    runner = FakeRemoteRunner()

    def recovered_run(argv, *, cwd: Path, **kwargs):
        call = tuple(str(part) for part in argv)
        runner.calls.append(call)
        return migration.CommandResult(call, 0, json.dumps({
            "migration_id": VALID_ID, "status": "RECOVERED_WITHOUT_REPLACE",
        }), "")

    runner.run = recovered_run
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        "recover", "--migration-id", VALID_ID, "--apply",
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "RECOVERED_WITHOUT_REPLACE"


def test_mutating_ssh_timeout_reports_remote_status_unknown_in_progress(tmp_path: Path, capsys):
    _write_valid_bundle(tmp_path)
    key = _fake_key(tmp_path)

    class TimeoutRunner(FakeRemoteRunner):
        def run(self, argv, **kwargs):
            if " apply " in f" {argv[-1]} ":
                raise ReleaseError("command timed out: ssh", category="runtime")
            return super().run(argv, **kwargs)

    rc = cli.main([
        "--host", "cloud.example", "--identity", str(key),
        "apply", "--migration-id", VALID_ID, "--apply",
    ], runner=TimeoutRunner(), repo=tmp_path)
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["remote_status"] == "unknown_in_progress"
    assert payload["recovery_action"] == "recover --apply"


def test_plan_is_read_only_and_never_uploads(tmp_path: Path):
    _write_valid_bundle(tmp_path)
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        "plan", "--migration-id", VALID_ID,
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    assert len(runner.calls) == 1
    assert "inspect-current" in runner.calls[0][-1]


def test_apply_seals_only_after_all_scp_uploads_complete(tmp_path: Path):
    _write_valid_bundle(tmp_path)
    runner = FakeRemoteRunner()
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(_fake_key(tmp_path)),
        "apply", "--migration-id", VALID_ID, "--apply",
    ], runner=runner, repo=tmp_path)
    assert rc == 0
    commands = [" ".join(call) for call in runner.calls]
    scp_positions = [index for index, call in enumerate(runner.calls) if call[0] == "scp"]
    seal_position = next(index for index, command in enumerate(commands) if " seal-upload " in command)
    preflight_position = next(index for index, command in enumerate(commands) if " preflight " in command)
    apply_position = next(index for index, command in enumerate(commands) if " apply " in command)
    assert len(scp_positions) == 4
    assert max(scp_positions) < seal_position < preflight_position < apply_position
    assert not any("chmod 0600" in command for command in commands)


def test_final_snapshot_without_both_switches_is_only_a_service_plan(tmp_path: Path):
    runner = FakeBinaryRunner()
    rc = cli.main(["snapshot", "--phase", "final"], runner=runner, repo=tmp_path)
    assert rc == 0
    assert any("config" in call and "--services" in call for call in runner.calls)
    assert not any("pg_dump" in call or "redis-cli" in call for call in runner.calls)


# ── Collector schema 指纹：逻辑形态，且三份实现必须等价 ────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]


def _collector_like(path: Path, *, migrated: bool) -> None:
    """建一个 collector 形态的库。

    `migrated=True` 走「老库 + ALTER 追加列」这条路径，`False` 走「新建库一次性声明」
    ——两者**逻辑 schema 相同、DDL 文本必然不同**，正是真实的本地 vs 云端。
    """
    with sqlite3.connect(path) as connection:
        if migrated:
            connection.execute(
                "CREATE TABLE llm_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " trace_id TEXT DEFAULT '', model TEXT DEFAULT '')"
            )
            connection.execute("ALTER TABLE llm_calls ADD COLUMN provider TEXT DEFAULT ''")
        else:
            connection.execute(
                "CREATE TABLE llm_calls(\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  trace_id TEXT DEFAULT '',\n"
                "  model TEXT DEFAULT '',\n"
                "  provider TEXT DEFAULT ''  -- 服务商\n"
                ")"
            )
        connection.execute("CREATE INDEX idx_llm_trace ON llm_calls(trace_id)")


def _remote_collector_fingerprint(database: Path) -> str:
    """跑 `remote-data-migration.sh` 里那段内联 python（抽出来、把库路径换成探针）。"""
    text = (REPO_ROOT / "deploy" / "cloud" / "remote-data-migration.sh").read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^  collector_json=\"\$\(\"\$\{compose\[@\]\}\" exec -T observability-collector "
        r"python -c '(?P<body>.*?)'\)\"$",
        text,
    )
    assert match is not None, "远端 collector 指纹的内联 python 找不到了，本用例失去锚点"
    # ⚠ 不要「反转义」`\"`：那段 python 被 bash 单引号包着，`\"` 原样进文件，
    # 而它在 Python 双引号串里正是合法转义。多此一举地替换会把 SQL 串拆坏。
    body = match.group("body").replace(
        "file:/data/obs.db?mode=ro", f"file:{database.as_posix()}?mode=ro",
    )
    output: list[str] = []
    namespace = {"print": output.append}
    exec(compile(body, "<remote-collector-fingerprint>", "exec"), namespace)
    return json.loads(output[-1])["schema_fingerprint"]


def _evidence_collector_fingerprint(database: Path) -> str:
    """跑 `store_identity_evidence.py::collect_collector` 那份实现。"""
    spec = importlib.util.spec_from_file_location(
        "store_evidence_for_fingerprint",
        REPO_ROOT / "deploy" / "cloud" / "store_identity_evidence.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = module.collect_collector(database, b"k" * 32, 7.0)
    return payload["schema_fingerprint"]


def test_collector_schema_fingerprint_ignores_ddl_text_form(tmp_path: Path):
    """ALTER 迁上来的库与新建库**逻辑相同**，指纹就必须相同。

    2026-08-18 真栈实证：本地与云端的 `llm_calls` 同为 16 列同类型，只因本地的
    `provider` 是 ALTER 追加、云端在 CREATE 的声明位，旧的「哈希 DDL 原文」指纹
    把两者判成不兼容，`plan` 直接 `remote PostgreSQL/Collector compatibility check failed`。
    > 判据：**指纹要量的是内容不是形式**——长期库必经 ALTER、新库必经 CREATE，
    > 拿文本比这两者等于要求一件不可能的事。
    """
    migrated, fresh = tmp_path / "migrated.db", tmp_path / "fresh.db"
    _collector_like(migrated, migrated=True)
    _collector_like(fresh, migrated=False)

    with sqlite3.connect(migrated) as left, sqlite3.connect(fresh) as right:
        # 前提自检：两份 DDL 文本确实不同，否则本用例什么都没验到
        ddl_left = left.execute("SELECT sql FROM sqlite_master WHERE name='llm_calls'").fetchone()[0]
        ddl_right = right.execute("SELECT sql FROM sqlite_master WHERE name='llm_calls'").fetchone()[0]
        assert ddl_left != ddl_right
        assert migration.sqlite_fingerprint(left) == migration.sqlite_fingerprint(right)

    # 真的少一列时必须红（别把「规范化」做成「什么都看不见」）
    with sqlite3.connect(fresh) as right:
        right.execute("ALTER TABLE llm_calls ADD COLUMN extra TEXT DEFAULT ''")
    with sqlite3.connect(migrated) as left, sqlite3.connect(fresh) as right:
        assert migration.sqlite_fingerprint(left) != migration.sqlite_fingerprint(right)


def test_collector_schema_fingerprint_has_three_equivalent_implementations(tmp_path: Path):
    """同一算法活在三处（本地 lib / 远端 sh 内联 / evidence 工具），必须逐字等价。

    跨 ssh 边界无法共用实现，所以用「同一个库跑三份实现比对」把它们钉在一起
    ——改一处不改另两处，迁移就会在 preflight 或 pre-start 比对上失败。
    """
    database = tmp_path / "obs.db"
    _collector_like(database, migrated=True)
    with sqlite3.connect(database) as connection:
        for table in ("turns", "spans", "logs"):
            connection.execute(f"CREATE TABLE {table}(trace_id TEXT PRIMARY KEY)")
        local = migration.sqlite_fingerprint(connection)
    assert local == _remote_collector_fingerprint(database)
    assert local == _evidence_collector_fingerprint(database)


# ── 封存的 WAL 库：只读介质上必须读得开（immutable=1）────────────────────────

def _wal_database(path: Path, rows: int = 3) -> None:
    """造一个真实形态的 collector 库：WAL 模式、checkpoint 过、**不留 sidecar**。"""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE turns(trace_id TEXT PRIMARY KEY)")
        for index in range(rows):
            connection.execute("INSERT INTO turns(trace_id) VALUES (?)", (f"t{index}",))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    # ⚠ 不能用 `PRAGMA journal_mode` 自检：以 `immutable=1` 打开时 SQLite **忽略 WAL**、
    # 把它报成 `delete`（探针第一版就栽在这，容器里当场红——同一族「观察方式改变了观察结果」）。
    # 直接读文件头：第 18/19 字节是 read/write format version，`2` 即 WAL。
    header = path.read_bytes()[18:20]
    assert header == b"\x02\x02", (
        f"探针没造出 WAL 库（header={header!r}），这条用例就什么都没验到"
    )


@pytest.mark.skipif(os.name == "nt", reason="需要 POSIX 目录权限来造只读介质")
@pytest.mark.skipif(
    os.name != "nt" and os.geteuid() == 0,
    reason="root 绕过目录权限，造不出只读介质（CI 以普通用户跑，这条在那里生效）",
)
def test_sealed_wal_database_opens_on_read_only_media(tmp_path: Path):
    """封存的迁移包按 `--mount …,readonly` 挂进来，读它**不能依赖能写 sidecar**。

    2026-08-18 真栈实证：`collector-restore` 与随后的 pre-start 取证都倒在
    `sqlite3.OperationalError: unable to open database file`——shipped 的
    `collector.db` header 是 WAL，`mode=ro` 打开需要建 `-shm`，只读挂载上建不了。
    ⚠ 本机第一次复现**没红**：批次目录里残留着我们自己读路径产生的 `-shm`/`-wal`，
    而云端 `/incoming` 只有 4 个规范文件。
    > 判据：**我们自己的读路径会往「封存输入」旁边写 sidecar，那正是让复现说谎的东西。**
    """
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    database = sealed / "collector.db"
    _wal_database(database)
    database.chmod(0o444)
    sealed.chmod(0o555)                      # 只读介质：连 sidecar 都建不出来
    try:
        with pytest.raises(sqlite3.OperationalError):   # 前提自检：不加 immutable 确实打不开
            sqlite3.connect(
                f"file:{database.as_posix()}?mode=ro", uri=True,
            ).execute("PRAGMA integrity_check").fetchall()

        with sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro&immutable=1", uri=True,
        ) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT count(*) FROM turns").fetchone()[0] == 3
        assert not list(sealed.glob("collector.db-*")), "读封存件时不许留下 sidecar"
    finally:
        sealed.chmod(0o755)


def test_collector_readers_open_sealed_input_immutably():
    """三处读**封存件**的地方都要 `immutable=1`；读**活库**的地方绝不能加。

    活库那一档（apply post-start、远端 inspect-current）collector 正在写，
    `immutable=1` 会略过 WAL 读出偏旧视图 ⇒ 尚在 WAL 里的行看起来像被删了。
    所以这不是「到处加上就对」，而是**按被读对象是否静止**来分。
    """
    replace_source = (REPO_ROOT / "deploy" / "cloud" / "collector_volume_replace.py").read_text(
        encoding="utf-8",
    )
    assert "mode=ro&immutable=1" in replace_source, "封存的 incoming 必须 immutable 打开"

    evidence_source = (REPO_ROOT / "deploy" / "cloud" / "store_identity_evidence.py").read_text(
        encoding="utf-8",
    )
    assert '"--immutable"' in evidence_source and "immutable=args.immutable" in evidence_source
    assert 'immutable else ""' in evidence_source, "必须是显式开关，不是无条件 immutable"

    shell = (REPO_ROOT / "deploy" / "cloud" / "remote-data-migration.sh").read_text(encoding="utf-8")
    body = re.search(
        r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", shell,
    )["body"]
    assert 'if [[ "${stage}" == "pre-start" ]]; then collector_immutable=(--immutable); fi' in body
    assert '"${collector_immutable[@]}"' in body
    # 活库那一路（inspect-current 读运行中的 /data/obs.db）不许被顺手加上
    assert 'sqlite3.connect("file:/data/obs.db?mode=ro", uri=True)' in shell

    local_source = (REPO_ROOT / "scripts" / "cloud_data_migration_lib.py").read_text(encoding="utf-8")
    assert local_source.count("mode=ro&immutable=1") == 3
    assert '"--immutable",' in local_source


# ── 迁移状态写入：幂等重写也必须产出 partial ────────────────────────────────

def _migration_state_writer():
    """抽出 `write_migration_state` 里那段内联 python（整段 .sh 不能直接跑）。"""
    text = (REPO_ROOT / "deploy" / "cloud" / "remote-data-migration.sh").read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^  python3 - \"\$\{partial\}\" \"\$\{directory\}/status\.json\".*?<<'PY' \|\| return \$\?\n"
        r"(?P<body>.*?)^PY$",
        text,
    )
    assert match is not None, "找不到 write_migration_state 的内联 python，本用例失去锚点"
    return match.group("body")


def _run_state_writer(body: str, argv: list[str]) -> int:
    saved = sys.argv
    sys.argv = ["-", *argv]
    try:
        exec(compile(body, "<write-migration-state>", "exec"), {"__name__": "__main__"})
    except SystemExit as exit_code:
        return 0 if exit_code.code in (None, 0) else 1
    finally:
        sys.argv = saved
    return 0


def test_migration_state_write_is_idempotent_and_always_emits_a_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """重写同一个状态**也必须产出 partial**——shell 紧跟着无条件 chmod 它。

    2026-08-18 真栈实证：原实现在「状态已等于目标」时 `raise SystemExit(0)` 却不建
    partial，于是 `chmod: cannot access …: No such file or directory`，整步失败
    ⇒ **`recover` 永远不可能成功**，而它存在的意义正是把已经 ROLLED_BACK 的迁移收尾、
    清掉挡住发版的 fence。
    > 判据同「恒假的就绪闸」：**一条永远不可能成功的路径，和没有这条路径一样糟。**
    """
    # 远端跑在 Linux；Windows 没有 O_NOFOLLOW，补一个无意义的 0 让同一段代码跑起来。
    monkeypatch.setattr(os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    body = _migration_state_writer()
    directory = tmp_path
    machine = directory / "migration-state-machine.json"
    machine.write_text(json.dumps({
        "schema_version": 1,
        "states": {
            "BACKED_UP": {"next": ["REPLACING", "ROLLED_BACK"]},
            "REPLACING": {"next": ["APPLIED", "ROLLBACK_IN_PROGRESS"]},
            "ROLLBACK_IN_PROGRESS": {"next": ["ROLLED_BACK"]},
            "ROLLED_BACK": {"next": []},
            "APPLIED": {"next": []},
        },
    }), encoding="utf-8")
    backup_manifest = directory / "backup-manifest.json"
    backup_manifest.write_text(json.dumps({"files": {"redis.rdb": {"sha256": "0" * 64}}}), encoding="utf-8")
    status = directory / "status.json"
    stamp = "20260818T000000Z"

    def write(state: str, partial_name: str) -> Path:
        partial = directory / partial_name
        code = _run_state_writer(body, [
            str(partial), str(status), state, VALID_ID, stamp, "", str(backup_manifest), str(machine),
        ])
        assert code == 0, f"{state} 写入失败"
        assert partial.is_file(), f"{state} 没有产出 partial —— shell 会在 chmod 上炸"
        partial.replace(status)                      # 复刻 shell 的 mv -T
        return status

    write("BACKED_UP", "status.a.json.partial")
    assert json.loads(status.read_text(encoding="utf-8"))["status"] == "BACKED_UP"
    write("ROLLED_BACK", "status.b.json.partial")
    assert json.loads(status.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"
    # 幂等重写：recover 收尾走的就是这一步
    write("ROLLED_BACK", "status.c.json.partial")
    assert json.loads(status.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"
    # 非法跃迁仍必须被拒
    code = _run_state_writer(body, [
        str(directory / "status.d.json.partial"), str(status), "APPLIED",
        VALID_ID, stamp, "", str(backup_manifest), str(machine),
    ])
    assert code == 1, "ROLLED_BACK → APPLIED 是非法跃迁，必须拒绝"


def test_migration_protocol_keeps_stdout_machine_only():
    """迁移的 stdout 是**机器通道**：客户端把整段 stdout 交给 json.loads。

    2026-08-18 真栈实证：`verify_current_release` 末尾会把证据文件路径打到 stdout
    （release 工作流要的输出），于是一次**完全成功**的 apply 被客户端判成失败——
    服务端 `status.json` 是 APPLIED、fence 已清、独立 verify 通过，客户端却 rc=2。
    后果是最坏的一类：**诱使人去回滚一次好的迁移**。这条只可能在成功的 apply 上出现
    （前两次都倒在更早的步骤），所以从来没人看见过。
    > 判据：**诊断信息走 stderr，stdout 留给协议。**
    """
    shell = (REPO_ROOT / "deploy" / "cloud" / "remote-data-migration.sh").read_text(encoding="utf-8")
    assert "verify_current_release_quiet() {\n  verify_current_release >/dev/null\n}" in shell
    # 迁移侧三个调用点都必须走静音包装（apply / verify / rollback 三条路径都会被解析）
    bare = re.findall(r"(?m)^\s*(?:run_recoverable_step |rollback_run_step .*? )?verify_current_release(?!_quiet)\b.*$", shell)
    assert [line for line in bare if "verify_current_release() {" not in line and ">/dev/null" not in line] == [], (
        f"还有裸调用会把证据路径漏进 stdout：{bare}"
    )
    # release 工作流仍然要那行路径——不能顺手把源头改掉
    verifier = (REPO_ROOT / "deploy" / "cloud" / "verify-release.sh").read_text(encoding="utf-8")
    assert "printf '%s\\n' \"${target}\"" in verifier

    # 客户端确实是「整段 stdout 必须是一个 JSON」，所以多一行就会崩
    lib = (REPO_ROOT / "scripts" / "cloud_data_migration_lib.py").read_text(encoding="utf-8")
    assert "return result.stdout.strip()" in lib
    with pytest.raises(migration.MigrationError):
        migration.parse_action_status(
            '/opt/car-agent/shared/evidence/x.json\n{"migration_id":"%s","status":"APPLIED"}' % VALID_ID,
            VALID_ID, "APPLIED",
        )

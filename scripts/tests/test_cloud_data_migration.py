from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import cloud_data_migration as cli
from scripts import cloud_data_migration_lib as migration


VALID_ID = "20260816T010203Z-aaaaaaa-online"


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
        if "stop" in call:
            self.stopped.update(call[call.index("stop") + 1:])
        if "start" in call:
            self.stopped.difference_update(call[call.index("start") + 1:])
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
        },
        "redis": {
            "version": "7.2.5", "rdb_version": 11, "key_count": 0,
            "prefixes": {}, "types": {}, "persistent": 0, "expiring": 0,
            "min_ttl_ms": 0, "max_ttl_ms": 0, "rdb_sha256": "b" * 64,
        },
        "collector": {
            "user_version": 0, "schema_fingerprint": "e" * 64,
            "tables": {name: 0 for name in migration.COLLECTOR_TABLES},
            "integrity_check": "ok",
        },
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
    assert any("start" in call and "gateway-cloud" in call for call in runner.calls)


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


def test_manifest_contains_only_aggregate_evidence(tmp_path: Path):
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
    evidence = migration.collect_aggregate_evidence(
        tmp_path, services, runner, directory, "d" * 64
    )
    manifest = migration.build_manifest(
        migration.SnapshotEvidence(
            directory=directory,
            migration_id=VALID_ID,
            phase="online",
            source_sha="a" * 40,
            created_at="2026-08-16T01:02:03Z",
            **evidence,
        )
    )
    encoded = json.dumps(migration.manifest_payload(manifest), ensure_ascii=False)
    assert manifest.postgres["tables"]["memory_item"] == 370
    assert manifest.postgres["tables"]["voiceprint"] == 0
    assert manifest.postgres["states"]["reminder_item.status"] == {"pending": 57}
    assert manifest.redis["key_count"] == 3271
    assert manifest.collector["tables"]["turns"] == 3
    assert set(manifest.postgres["tables"]) == set(migration.BUSINESS_TABLES + migration.DERIVED_TABLES)
    for private in ("user text", "session value", "api-token-value"):
        assert private not in encoded
    assert all("SELECT *" not in " ".join(call) for call in runner.calls)


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
    }
    payload["redis"] = {
        "version": "7.2.5", "rdb_version": 11, "key_count": 0, "prefixes": {},
        "types": {}, "persistent": 0, "expiring": 0, "min_ttl_ms": 0,
        "max_ttl_ms": 0, "rdb_sha256": payload["files"]["redis.rdb"]["sha256"],
    }
    payload["collector"] = {
        "user_version": 0, "schema_fingerprint": "e" * 64,
        "tables": {name: 0 for name in migration.COLLECTOR_TABLES}, "integrity_check": "ok",
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

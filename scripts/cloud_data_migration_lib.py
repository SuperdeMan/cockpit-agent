from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence


MIGRATION_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(?:online|final)$"
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "migration_id",
        "phase",
        "source_sha",
        "created_at",
        "files",
        "postgres",
        "redis",
        "collector",
    }
)
SNAPSHOT_FILENAMES = ("postgres.dump", "redis.rdb", "collector.db")
BUSINESS_TABLES = (
    "memory_item",
    "memory_relation",
    "reminder_item",
    "task_ledger",
    "proactive_delivery",
    "scene_item",
    "voiceprint",
)
DERIVED_TABLES = ("agents", "agent_capability_vec")
COLLECTOR_TABLES = ("turns", "spans", "llm_calls", "logs")
POSTGRES_STATE_COLUMNS = MappingProxyType(
    {
        "reminder_item.status": ("reminder_item", "status"),
        "task_ledger.status": ("task_ledger", "status"),
        "proactive_delivery.state": ("proactive_delivery", "state"),
        "scene_item.status": ("scene_item", "status"),
    }
)
SCHEMA_SQL = """
SELECT json_agg(row_to_json(x) ORDER BY table_name, ordinal_position)
FROM (
  SELECT table_name, column_name, ordinal_position, data_type, udt_name,
         is_nullable, column_default
  FROM information_schema.columns
  WHERE table_schema='public'
) x
""".strip()
POSTGRES_AGGREGATE_SQL = "/* cloud-migration aggregate: fixed table counts, states, schema only */"
REDIS_AGGREGATE_LUA = "/* cloud-migration aggregate: prefix/type/ttl counts only */"


class MigrationError(RuntimeError):
    """A redacted, user-facing data migration error."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class BinaryCommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult: ...

    def text(self, argv: Sequence[str], *, cwd: Path) -> str: ...

    def json(self, argv: Sequence[str], *, cwd: Path) -> object: ...

    def stream_to_file(
        self, argv: Sequence[str], target: Path, *, cwd: Path
    ) -> None: ...


@dataclass(frozen=True)
class FileRecord:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class MigrationManifest:
    migration_id: str
    phase: str
    source_sha: str
    created_at: str
    files: Mapping[str, FileRecord]
    postgres: Mapping[str, object]
    redis: Mapping[str, object]
    collector: Mapping[str, object]
    schema_version: int = 1


@dataclass(frozen=True)
class SourceService:
    service: str
    container_id: str
    image: str
    data_mount: str


@dataclass(frozen=True)
class MigrationBundle:
    directory: Path
    manifest: MigrationManifest
    files: tuple[Path, ...]


@dataclass(frozen=True)
class SnapshotEvidence:
    directory: Path
    migration_id: str
    phase: str
    source_sha: str
    created_at: str
    postgres: Mapping[str, object]
    redis: Mapping[str, object]
    collector: Mapping[str, object]


def _exact_keys(value: object, expected: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MigrationError(f"{label} must be an object")
    keys = frozenset(value.keys())
    if keys != expected or not all(isinstance(key, str) for key in keys):
        raise MigrationError(f"unknown {label} keys")
    return value


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MigrationError(f"{label} must be an integer")
    return value


def require_migration_id(value: str) -> str:
    if not isinstance(value, str) or MIGRATION_ID_RE.fullmatch(value) is None:
        raise MigrationError("invalid migration id")
    return value


def new_migration_id(now: datetime, source_sha: str, phase: str) -> str:
    if now.tzinfo is None or phase not in {"online", "final"}:
        raise MigrationError("invalid migration identity inputs")
    if FULL_SHA_RE.fullmatch(source_sha) is None:
        raise MigrationError("source SHA must be a full lowercase commit SHA")
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{source_sha[:7]}-{phase}"


def artifact_directory(root: Path, migration_id: str) -> Path:
    require_migration_id(migration_id)
    base = root.resolve()
    target = (base / migration_id).resolve()
    if target.parent != base:
        raise MigrationError("migration artifact escaped its root")
    return target


def atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("xb") as handle:
        handle.write(encoded)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def restrict_private_tree(path: Path, runner: BinaryCommandRunner) -> None:
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
    if os.name != "nt":
        return
    account = runner.text(["whoami"], cwd=path.parent).strip()
    if not account or any(char in account for char in "\r\n\x00"):
        raise MigrationError("could not resolve the current Windows account")
    grant = f"{account}:(OI)(CI)F" if path.is_dir() else f"{account}:F"
    runner.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", grant], cwd=path.parent
    )


def parse_manifest(payload: object) -> MigrationManifest:
    data = _exact_keys(payload, MANIFEST_KEYS, "manifest")
    schema_version = _strict_int(data["schema_version"], "schema_version", minimum=1)
    migration_id = require_migration_id(data["migration_id"])  # type: ignore[arg-type]
    phase = data["phase"]
    if phase not in {"online", "final"} or not migration_id.endswith(f"-{phase}"):
        raise MigrationError("manifest phase does not match migration id")
    source_sha = data["source_sha"]
    if not isinstance(source_sha, str) or FULL_SHA_RE.fullmatch(source_sha) is None:
        raise MigrationError("invalid source SHA")
    created_at = data["created_at"]
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise MigrationError("invalid created_at")

    raw_files = _exact_keys(data["files"], frozenset(SNAPSHOT_FILENAMES), "file")
    files: dict[str, FileRecord] = {}
    for name in SNAPSHOT_FILENAMES:
        raw = _exact_keys(
            raw_files[name], frozenset({"size_bytes", "sha256"}), "file record"
        )
        size = _strict_int(raw["size_bytes"], "size_bytes", minimum=1)
        checksum = raw["sha256"]
        if not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None:
            raise MigrationError("invalid sha256")
        files[name] = FileRecord(size_bytes=size, sha256=checksum)

    aggregates: dict[str, Mapping[str, object]] = {}
    for name in ("postgres", "redis", "collector"):
        raw = data[name]
        if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
            raise MigrationError(f"{name} evidence must be an object")
        aggregates[name] = MappingProxyType(dict(raw))
    return MigrationManifest(
        migration_id=migration_id,
        phase=phase,
        source_sha=source_sha,
        created_at=created_at,
        files=MappingProxyType(files),
        postgres=aggregates["postgres"],
        redis=aggregates["redis"],
        collector=aggregates["collector"],
        schema_version=schema_version,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def redis_prefix(key: bytes) -> str:
    head = key.split(b":", 1)[0]
    decoded = head.decode("ascii", errors="ignore")
    return decoded if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", decoded) else "other"


def sqlite_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _bounded_count_map(value: object, label: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise MigrationError(f"{label} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) is None:
            raise MigrationError(f"{label} contains an invalid category")
        result[key] = _strict_int(count, f"{label} count")
    return MappingProxyType(result)


def _postgres_evidence(payload: object) -> Mapping[str, object]:
    expected = frozenset(
        {"major", "vector_version", "tables", "states", "columns", "primary_keys", "indexes"}
    )
    data = _exact_keys(payload, expected, "PostgreSQL aggregate")
    if not isinstance(data["major"], str) or re.fullmatch(r"[0-9]{1,3}", data["major"]) is None:
        raise MigrationError("invalid PostgreSQL major version")
    if not isinstance(data["vector_version"], str) or len(data["vector_version"]) > 32:
        raise MigrationError("invalid vector extension version")
    tables = _bounded_count_map(data["tables"], "PostgreSQL tables")
    if set(tables) != set(BUSINESS_TABLES + DERIVED_TABLES):
        raise MigrationError("PostgreSQL aggregate table set is not exact")
    raw_states = data["states"]
    if not isinstance(raw_states, Mapping) or set(raw_states) != set(POSTGRES_STATE_COLUMNS):
        raise MigrationError("PostgreSQL state aggregate set is not exact")
    states = {name: dict(_bounded_count_map(raw_states[name], name)) for name in POSTGRES_STATE_COLUMNS}
    schema_material = {
        "columns": data["columns"],
        "primary_keys": data["primary_keys"],
        "indexes": data["indexes"],
    }
    for value in schema_material.values():
        if not isinstance(value, list):
            raise MigrationError("PostgreSQL schema aggregate is invalid")
    return MappingProxyType(
        {
            "major": data["major"],
            "vector_version": data["vector_version"],
            "tables": dict(tables),
            "states": states,
            "schema_fingerprint": hashlib.sha256(canonical_json_bytes(schema_material)).hexdigest(),
        }
    )


def _redis_evidence(payload: object, rdb_path: Path) -> Mapping[str, object]:
    expected = frozenset(
        {
            "version", "rdb_version", "key_count", "prefixes", "types",
            "persistent", "expiring", "min_ttl_ms", "max_ttl_ms",
        }
    )
    data = _exact_keys(payload, expected, "Redis aggregate")
    if not isinstance(data["version"], str) or len(data["version"]) > 32:
        raise MigrationError("invalid Redis version")
    result: dict[str, object] = {
        "version": data["version"],
        "rdb_version": _strict_int(data["rdb_version"], "RDB version", minimum=1),
        "key_count": _strict_int(data["key_count"], "Redis key count"),
        "prefixes": dict(_bounded_count_map(data["prefixes"], "Redis prefixes")),
        "types": dict(_bounded_count_map(data["types"], "Redis types")),
        "persistent": _strict_int(data["persistent"], "persistent count"),
        "expiring": _strict_int(data["expiring"], "expiring count"),
        "min_ttl_ms": _strict_int(data["min_ttl_ms"], "minimum TTL"),
        "max_ttl_ms": _strict_int(data["max_ttl_ms"], "maximum TTL"),
        "rdb_sha256": sha256_file(rdb_path),
    }
    if result["persistent"] + result["expiring"] != result["key_count"]:
        raise MigrationError("Redis TTL aggregates do not match key count")
    return MappingProxyType(result)


def _collector_evidence(path: Path) -> Mapping[str, object]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise MigrationError("Collector integrity check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        existing = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not set(COLLECTOR_TABLES).issubset(existing):
            raise MigrationError("Collector aggregate table set is incomplete")
        tables = {
            name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in COLLECTOR_TABLES
        }
        fingerprint = sqlite_fingerprint(connection)
    return MappingProxyType(
        {"user_version": version, "schema_fingerprint": fingerprint, "tables": tables,
         "integrity_check": "ok"}
    )


def collect_aggregate_evidence(
    repo: Path,
    services: Mapping[str, SourceService],
    runner: BinaryCommandRunner,
    directory: Path,
) -> dict[str, Mapping[str, object]]:
    postgres_raw = runner.json(
        [
            "docker", "exec", services["postgres"].container_id,
            "psql", "-U", "cockpit", "-d", "cockpit", "-At",
            "--command", POSTGRES_AGGREGATE_SQL,
        ],
        cwd=repo,
    )
    redis_raw = runner.json(
        [
            "docker", "exec", services["redis"].container_id,
            "redis-cli", "--json", "EVAL", REDIS_AGGREGATE_LUA, "0",
        ],
        cwd=repo,
    )
    return {
        "postgres": _postgres_evidence(postgres_raw),
        "redis": _redis_evidence(redis_raw, directory / "redis.rdb"),
        "collector": _collector_evidence(directory / "collector.db"),
    }


def manifest_payload(manifest: MigrationManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "migration_id": manifest.migration_id,
        "phase": manifest.phase,
        "source_sha": manifest.source_sha,
        "created_at": manifest.created_at,
        "files": {
            name: {"size_bytes": record.size_bytes, "sha256": record.sha256}
            for name, record in manifest.files.items()
        },
        "postgres": dict(manifest.postgres),
        "redis": dict(manifest.redis),
        "collector": dict(manifest.collector),
    }


def build_manifest(snapshot: SnapshotEvidence) -> MigrationManifest:
    payload = _manifest_payload(
        snapshot.migration_id,
        snapshot.phase,
        snapshot.source_sha,
        snapshot.created_at,
        snapshot.directory,
        postgres=snapshot.postgres,
        redis=snapshot.redis,
        collector=snapshot.collector,
    )
    return parse_manifest(payload)


def _strict_single_inspect(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise MigrationError("docker inspect returned an unexpected result")
    return payload[0]


def _exact_mount(payload: object, destination: str) -> Mapping[str, object]:
    if not isinstance(payload, list):
        raise MigrationError("container mounts are invalid")
    matches = [
        item for item in payload
        if isinstance(item, Mapping) and item.get("Destination") == destination
    ]
    if len(matches) != 1:
        raise MigrationError("source data mount is unavailable")
    mount = matches[0]
    if mount.get("Type") != "volume" or not isinstance(mount.get("Name"), str):
        raise MigrationError("source data must use an exact named volume")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", mount["Name"]) is None:
        raise MigrationError("source named volume is invalid")
    return mount


def discover_source_services(
    repo: Path, runner: BinaryCommandRunner
) -> Mapping[str, SourceService]:
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    found: dict[str, SourceService] = {}
    for service, destination in {
        "postgres": "/var/lib/postgresql/data",
        "redis": "/data",
        "observability-collector": "/data",
    }.items():
        container_id = runner.text([*compose, "ps", "-q", service], cwd=repo).strip()
        if re.fullmatch(r"[0-9a-f]{12,64}", container_id) is None:
            raise MigrationError(f"active source service is unavailable: {service}")
        inspect = _strict_single_inspect(
            runner.json(["docker", "inspect", container_id], cwd=repo)
        )
        state = inspect.get("State")
        config = inspect.get("Config")
        if (
            not isinstance(state, Mapping)
            or state.get("Running") is not True
            or state.get("Restarting") is True
        ):
            raise MigrationError(f"source service is not stable: {service}")
        if not isinstance(config, Mapping) or not isinstance(config.get("Image"), str):
            raise MigrationError(f"source image is unavailable: {service}")
        mount = _exact_mount(inspect.get("Mounts"), destination)
        found[service] = SourceService(
            service=service,
            container_id=container_id,
            image=config["Image"],
            data_mount=mount["Name"],
        )
    return MappingProxyType(found)


def private_replace(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink() or source.stat().st_size <= 0:
        raise MigrationError(f"snapshot file is invalid: {destination.name}")
    os.chmod(source, 0o600)
    os.replace(source, destination)


def capture_postgres(
    service: SourceService,
    target: Path,
    runner: BinaryCommandRunner,
    *,
    repo: Path,
) -> None:
    runner.stream_to_file(
        [
            "docker", "exec", "-i", service.container_id,
            "pg_dump", "-U", "cockpit", "-d", "cockpit", "-Fc",
        ],
        target,
        cwd=repo,
    )


def capture_redis(
    service: SourceService,
    directory: Path,
    runner: BinaryCommandRunner,
    *,
    repo: Path,
) -> None:
    runner.run(
        [
            "docker", "run", "--rm",
            "--network", f"container:{service.container_id}",
            "--mount", f"type=bind,source={directory.resolve()},target=/snapshot",
            "--entrypoint", "redis-cli", service.image,
            "-h", "127.0.0.1", "--rdb", "/snapshot/redis.rdb.partial",
        ],
        cwd=repo,
    )
    private_replace(directory / "redis.rdb.partial", directory / "redis.rdb")


def capture_collector(
    service: SourceService,
    directory: Path,
    runner: BinaryCommandRunner,
    *,
    repo: Path,
) -> None:
    program = (
        "import sqlite3;"
        "s=sqlite3.connect('file:/data/obs.db?mode=ro',uri=True);"
        "d=sqlite3.connect('/snapshot/collector.db.partial');"
        "s.backup(d);d.execute('PRAGMA wal_checkpoint');d.close();s.close()"
    )
    runner.run(
        [
            "docker", "run", "--rm", "--volumes-from", f"{service.container_id}:ro",
            "--mount", f"type=bind,source={directory.resolve()},target=/snapshot",
            "--entrypoint", "python", service.image, "-c", program,
        ],
        cwd=repo,
    )
    private_replace(directory / "collector.db.partial", directory / "collector.db")


def _verify_snapshots(
    directory: Path,
    services: Mapping[str, SourceService],
    runner: BinaryCommandRunner,
    repo: Path,
) -> None:
    pg_result = runner.text(
        ["docker", "run", "--rm", "--entrypoint", "pg_restore", services["postgres"].image,
         "--list", str(directory / "postgres.dump")],
        cwd=repo,
    )
    if not pg_result.strip():
        raise MigrationError("PostgreSQL snapshot format validation failed")
    redis_result = runner.text(
        ["docker", "run", "--rm", "--entrypoint", "redis-check-rdb", services["redis"].image,
         str(directory / "redis.rdb")],
        cwd=repo,
    )
    if "CRC64 checksum is OK" not in redis_result:
        raise MigrationError("Redis snapshot format validation failed")
    with sqlite3.connect(f"file:{directory / 'collector.db'}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise MigrationError("Collector snapshot integrity validation failed")


def _manifest_payload(
    migration_id: str,
    phase: str,
    source_sha: str,
    created_at: str,
    directory: Path,
    *,
    postgres: Mapping[str, object] | None = None,
    redis: Mapping[str, object] | None = None,
    collector: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "migration_id": migration_id,
        "phase": phase,
        "source_sha": source_sha,
        "created_at": created_at,
        "files": {
            name: {
                "size_bytes": (directory / name).stat().st_size,
                "sha256": sha256_file(directory / name),
            }
            for name in SNAPSHOT_FILENAMES
        },
        "postgres": dict(postgres or {}),
        "redis": dict(redis or {}),
        "collector": dict(collector or {}),
    }


def _quiesce_local_writers(
    repo: Path,
    services: Mapping[str, SourceService],
    runner: BinaryCommandRunner,
) -> None:
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    raw = runner.text([*compose, "config", "--services"], cwd=repo)
    names = tuple(line.strip() for line in raw.splitlines() if line.strip())
    if not names or any(re.fullmatch(r"[a-z0-9-]+", name) is None for name in names):
        raise MigrationError("compose service list is invalid")
    writers = tuple(name for name in names if name not in {"postgres", "redis"})
    if not writers or "observability-collector" not in writers:
        raise MigrationError("collector writer is missing from compose services")
    runner.run([*compose, "stop", *writers], cwd=repo)
    for name in writers:
        source = services.get(name)
        if source is None:
            continue
        inspect = _strict_single_inspect(
            runner.json(["docker", "inspect", source.container_id], cwd=repo)
        )
        state = inspect.get("State")
        if not isinstance(state, Mapping) or state.get("Running") is not False:
            raise MigrationError(f"local writer did not exit: {name}")


def capture_local_snapshot(
    *,
    repo: Path,
    artifact_root: Path,
    phase: str,
    runner: BinaryCommandRunner,
    quiesce_local: bool = False,
    apply: bool = False,
    now: Callable[[], datetime] | None = None,
) -> MigrationBundle:
    if phase == "online" and (quiesce_local or apply):
        raise MigrationError("online snapshot never mutates the local stack")
    if phase == "final" and not (quiesce_local and apply):
        raise MigrationError("final snapshot requires quiesce-local and apply")
    if phase not in {"online", "final"}:
        raise MigrationError("invalid snapshot phase")
    clock = now or (lambda: datetime.now(UTC))
    current = clock()
    source_sha = runner.text(["git", "rev-parse", "HEAD"], cwd=repo).strip()
    migration_id = new_migration_id(current, source_sha, phase)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_directory(artifact_root, migration_id)
    directory.mkdir(mode=0o700)
    restrict_private_tree(directory, runner)
    atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURING"})
    try:
        services = discover_source_services(repo, runner)
        if phase == "final":
            _quiesce_local_writers(repo, services, runner)
        pg_partial = directory / "postgres.dump.partial"
        capture_postgres(services["postgres"], pg_partial, runner, repo=repo)
        private_replace(pg_partial, directory / "postgres.dump")
        capture_redis(services["redis"], directory, runner, repo=repo)
        capture_collector(services["observability-collector"], directory, runner, repo=repo)
        _verify_snapshots(directory, services, runner, repo)
        evidence = collect_aggregate_evidence(repo, services, runner, directory)
        snapshot = SnapshotEvidence(
            directory=directory,
            migration_id=migration_id,
            phase=phase,
            source_sha=source_sha,
            created_at=current.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            **evidence,
        )
        manifest = build_manifest(snapshot)
        payload = manifest_payload(manifest)
        atomic_private_json(directory / "manifest.json", payload)
        atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURED"})
    except Exception:
        atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURE_FAILED"})
        raise
    files = tuple(directory / name for name in (*SNAPSHOT_FILENAMES, "manifest.json", "status.json"))
    return MigrationBundle(directory=directory, manifest=manifest, files=files)

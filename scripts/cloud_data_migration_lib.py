from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence

from scripts.cloud_release_lib import CommandRunner as RemoteCommandRunner
from scripts.cloud_release_lib import SshConfig
from scripts.dev_stack_lib import DevStackError, resolve_target


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
MIN_SNAPSHOT_FREE_BYTES = 1024 * 1024 * 1024
CONTROL_JSON_MAX_BYTES = 1024 * 1024
CONTROL_JSON_MAX_DEPTH = 16
CONTROL_JSON_MAX_ITEMS = 20000
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
POSTGRES_AGGREGATE_SQL = r"""
WITH table_counts(name, count) AS (
  SELECT 'memory_item', count(*) FROM memory_item UNION ALL
  SELECT 'memory_relation', count(*) FROM memory_relation UNION ALL
  SELECT 'reminder_item', count(*) FROM reminder_item UNION ALL
  SELECT 'task_ledger', count(*) FROM task_ledger UNION ALL
  SELECT 'proactive_delivery', count(*) FROM proactive_delivery UNION ALL
  SELECT 'scene_item', count(*) FROM scene_item UNION ALL
  SELECT 'voiceprint', count(*) FROM voiceprint UNION ALL
  SELECT 'agents', count(*) FROM agents UNION ALL
  SELECT 'agent_capability_vec', count(*) FROM agent_capability_vec
), columns_data AS (
  SELECT json_agg(json_build_array(table_name, column_name, ordinal_position,
             data_type, udt_name, is_nullable, column_default)
             ORDER BY table_name, ordinal_position) AS value
  FROM information_schema.columns WHERE table_schema='public'
), primary_key_data AS (
  SELECT json_agg(json_build_array(tc.table_name, tc.constraint_name, kcu.column_name)
             ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position) AS value
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name
  WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY'
), index_data AS (
  SELECT json_agg(json_build_array(tablename, indexname, indexdef) ORDER BY tablename, indexname) AS value
  FROM pg_indexes WHERE schemaname='public'
)
SELECT json_build_object(
  'major', (current_setting('server_version_num')::int / 10000)::text,
  'vector_version', COALESCE((SELECT extversion FROM pg_extension WHERE extname='vector'), ''),
  'tables', (SELECT json_object_agg(name, count) FROM table_counts),
  'states', json_build_object(
    'reminder_item.status', (SELECT COALESCE(json_object_agg(status, count), '{}'::json)
       FROM (SELECT status, count(*) count FROM reminder_item GROUP BY status) x),
    'task_ledger.status', (SELECT COALESCE(json_object_agg(status, count), '{}'::json)
       FROM (SELECT status, count(*) count FROM task_ledger GROUP BY status) x),
    'proactive_delivery.state', (SELECT COALESCE(json_object_agg(state, count), '{}'::json)
       FROM (SELECT state, count(*) count FROM proactive_delivery GROUP BY state) x),
    'scene_item.status', (SELECT COALESCE(json_object_agg(status, count), '{}'::json)
       FROM (SELECT status, count(*) count FROM scene_item GROUP BY status) x)
  ),
  'columns', COALESCE((SELECT value FROM columns_data), '[]'::json),
  'primary_keys', COALESCE((SELECT value FROM primary_key_data), '[]'::json),
  'indexes', COALESCE((SELECT value FROM index_data), '[]'::json)
)
""".strip()
REDIS_AGGREGATE_LUA = r"""
redis.setresp(3)
local cursor = "0"
local prefixes, types = {}, {}
local key_count, persistent, expiring = 0, 0, 0
local min_ttl, max_ttl = nil, 0
repeat
  local page = redis.call("SCAN", cursor, "COUNT", 1000)
  cursor = page[1]
  for _, key in ipairs(page[2]) do
    key_count = key_count + 1
    local prefix = string.match(key, "^([A-Za-z0-9_-]+):") or "other"
    if string.len(prefix) > 32 then prefix = "other" end
    prefixes[prefix] = (prefixes[prefix] or 0) + 1
    local kind = redis.call("TYPE", key)
    if type(kind) == "table" then kind = kind.ok end
    types[kind] = (types[kind] or 0) + 1
    local ttl = redis.call("PTTL", key)
    if ttl < 0 then
      persistent = persistent + 1
    else
      expiring = expiring + 1
      if min_ttl == nil or ttl < min_ttl then min_ttl = ttl end
      if ttl > max_ttl then max_ttl = ttl end
    end
  end
until cursor == "0"
local prefix_items, type_items = {}, {}
for key, value in pairs(prefixes) do table.insert(prefix_items, key); table.insert(prefix_items, value) end
for key, value in pairs(types) do table.insert(type_items, key); table.insert(type_items, value) end
local info = redis.call("INFO", "server")
local version = string.match(info, "redis_version:([^\r\n]+)") or "unknown"
return {map={
  "version", version, "rdb_version", 1, "key_count", key_count,
  "prefixes", {map=prefix_items}, "types", {map=type_items},
  "persistent", persistent, "expiring", expiring,
  "min_ttl_ms", min_ttl or 0, "max_ttl_ms", max_ttl
}}
""".strip()


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

    def run_from_file(
        self, argv: Sequence[str], source: Path, *, cwd: Path
    ) -> CommandResult: ...


class LocalCommandRunner:
    def _execute(
        self, argv: Sequence[str], *, cwd: Path, stdout: object = subprocess.PIPE
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                list(argv), cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout,
                stderr=subprocess.PIPE, check=False,
            )
        except OSError as exc:
            raise MigrationError(f"could not run required local command: {argv[0]}") from exc
        if completed.returncode != 0:
            raise MigrationError(f"required local command failed: {argv[0]}")
        return completed

    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult:
        completed = self._execute(argv, cwd=cwd)
        return CommandResult(
            tuple(argv), completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )

    def text(self, argv: Sequence[str], *, cwd: Path) -> str:
        return self.run(argv, cwd=cwd).stdout

    def json(self, argv: Sequence[str], *, cwd: Path) -> object:
        try:
            return json.loads(self.text(argv, cwd=cwd))
        except (TypeError, json.JSONDecodeError) as exc:
            raise MigrationError(f"local command returned invalid JSON: {argv[0]}") from exc

    def stream_to_file(self, argv: Sequence[str], target: Path, *, cwd: Path) -> None:
        with target.open("xb") as output:
            self._execute(argv, cwd=cwd, stdout=output)

    def run_from_file(self, argv: Sequence[str], source: Path, *, cwd: Path) -> CommandResult:
        with source.open("rb") as input_file:
            try:
                completed = subprocess.run(
                    list(argv), cwd=cwd, stdin=input_file, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=False,
                )
            except OSError as exc:
                raise MigrationError(f"could not run required local command: {argv[0]}") from exc
        if completed.returncode != 0:
            raise MigrationError(f"required local command failed: {argv[0]}")
        return CommandResult(tuple(argv), completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"))


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
class WriterIdentity:
    service: str
    container_id: str
    image_id: str
    volumes: tuple[str, ...]


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


@dataclass(frozen=True)
class MigrationRequest:
    repo: Path
    migration_id: str
    bundle: MigrationBundle
    ssh: SshConfig


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    status: str
    current_release: str
    disk_available_bytes: int
    bundle_size_bytes: int
    remote_stores: Mapping[str, object]


def _exact_keys(value: object, expected: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MigrationError(f"{label} must be an object")
    keys = frozenset(value.keys())
    if keys != expected or not all(isinstance(key, str) for key in keys):
        raise MigrationError(f"unknown {label} keys")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("control JSON contains duplicate keys")
        result[key] = value
    return result


def _validate_json_bounds(value: object, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if depth > CONTROL_JSON_MAX_DEPTH:
        raise MigrationError("control JSON exceeds depth limit")
    count = counter if counter is not None else [0]
    count[0] += 1
    if count[0] > CONTROL_JSON_MAX_ITEMS:
        raise MigrationError("control JSON exceeds item limit")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MigrationError("control JSON key is invalid")
            _validate_json_bounds(item, depth=depth + 1, counter=count)
    elif isinstance(value, list):
        for item in value:
            _validate_json_bounds(item, depth=depth + 1, counter=count)


def parse_control_json(raw: bytes | str, *, max_bytes: int = CONTROL_JSON_MAX_BYTES) -> object:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > max_bytes:
        raise MigrationError("control JSON exceeds byte limit")
    try:
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise MigrationError("control JSON is invalid") from exc
    _validate_json_bounds(value)
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
    candidate = root.absolute()
    for ancestor in (candidate, *candidate.parents):
        if not ancestor.exists() and not ancestor.is_symlink():
            continue
        try:
            info = ancestor.lstat()
        except OSError as exc:
            raise MigrationError("migration artifact ancestor is unsafe") from exc
        is_reparse = bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        is_junction = bool(getattr(ancestor, "is_junction", lambda: False)())
        if ancestor.is_symlink() or is_reparse or is_junction:
            raise MigrationError("migration artifact ancestor is a link or junction")
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
    if schema_version != 1:
        raise MigrationError("invalid schema_version")
    migration_id = require_migration_id(data["migration_id"])  # type: ignore[arg-type]
    phase = data["phase"]
    if phase not in {"online", "final"} or not migration_id.endswith(f"-{phase}"):
        raise MigrationError("manifest phase does not match migration id")
    source_sha = data["source_sha"]
    if not isinstance(source_sha, str) or FULL_SHA_RE.fullmatch(source_sha) is None:
        raise MigrationError("invalid source SHA")
    created_at = data["created_at"]
    if not isinstance(created_at, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created_at) is None:
        raise MigrationError("invalid created_at")
    try:
        parsed_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationError("invalid created_at") from exc
    canonical_created = parsed_created.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if parsed_created.utcoffset() != UTC.utcoffset(parsed_created) or canonical_created != created_at:
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

    postgres = _parse_postgres_manifest(data["postgres"])
    redis = _parse_redis_manifest(data["redis"])
    collector = _parse_collector_manifest(data["collector"])
    return MigrationManifest(
        migration_id=migration_id,
        phase=phase,
        source_sha=source_sha,
        created_at=created_at,
        files=MappingProxyType(files),
        postgres=postgres,
        redis=redis,
        collector=collector,
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


def _require_fingerprint(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise MigrationError(f"invalid {label}")
    return value


def _parse_postgres_manifest(value: object) -> Mapping[str, object]:
    data = _exact_keys(value, frozenset({
        "major", "vector_version", "tables", "states",
        "schema_fingerprint", "archive_fingerprint",
    }), "PostgreSQL evidence")
    major = data["major"]
    vector = data["vector_version"]
    if not isinstance(major, str) or re.fullmatch(r"[0-9]{1,3}", major) is None:
        raise MigrationError("invalid PostgreSQL major")
    if not isinstance(vector, str) or re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", vector) is None:
        raise MigrationError("invalid vector version")
    tables = _bounded_count_map(data["tables"], "PostgreSQL tables")
    if set(tables) != set(BUSINESS_TABLES + DERIVED_TABLES):
        raise MigrationError("PostgreSQL table set is not exact")
    states_raw = data["states"]
    if not isinstance(states_raw, Mapping) or set(states_raw) != set(POSTGRES_STATE_COLUMNS):
        raise MigrationError("PostgreSQL state set is not exact")
    states = {name: dict(_bounded_count_map(states_raw[name], name)) for name in POSTGRES_STATE_COLUMNS}
    return MappingProxyType({
        "major": major, "vector_version": vector, "tables": dict(tables), "states": states,
        "schema_fingerprint": _require_fingerprint(data["schema_fingerprint"], "schema fingerprint"),
        "archive_fingerprint": _require_fingerprint(data["archive_fingerprint"], "archive fingerprint"),
    })


def _parse_redis_manifest(value: object) -> Mapping[str, object]:
    data = _exact_keys(value, frozenset({
        "version", "rdb_version", "key_count", "prefixes", "types", "persistent",
        "expiring", "min_ttl_ms", "max_ttl_ms", "rdb_sha256",
    }), "Redis evidence")
    version = data["version"]
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", version) is None:
        raise MigrationError("invalid Redis version")
    result = {
        "version": version,
        "rdb_version": _strict_int(data["rdb_version"], "RDB version", minimum=1),
        "key_count": _strict_int(data["key_count"], "Redis key count"),
        "prefixes": dict(_bounded_count_map(data["prefixes"], "Redis prefixes")),
        "types": dict(_bounded_count_map(data["types"], "Redis types")),
        "persistent": _strict_int(data["persistent"], "persistent count"),
        "expiring": _strict_int(data["expiring"], "expiring count"),
        "min_ttl_ms": _strict_int(data["min_ttl_ms"], "minimum TTL"),
        "max_ttl_ms": _strict_int(data["max_ttl_ms"], "maximum TTL"),
        "rdb_sha256": _require_fingerprint(data["rdb_sha256"], "RDB sha256"),
    }
    if result["persistent"] + result["expiring"] != result["key_count"]:
        raise MigrationError("Redis TTL counts do not match key count")
    if sum(result["prefixes"].values()) != result["key_count"] or sum(result["types"].values()) != result["key_count"]:
        raise MigrationError("Redis aggregate counts do not match key count")
    if result["min_ttl_ms"] > result["max_ttl_ms"]:
        raise MigrationError("Redis TTL range is invalid")
    return MappingProxyType(result)


def _parse_collector_manifest(value: object) -> Mapping[str, object]:
    data = _exact_keys(value, frozenset({
        "user_version", "schema_fingerprint", "tables", "integrity_check",
    }), "Collector evidence")
    tables = _bounded_count_map(data["tables"], "Collector tables")
    if set(tables) != set(COLLECTOR_TABLES) or data["integrity_check"] != "ok":
        raise MigrationError("Collector evidence is invalid")
    return MappingProxyType({
        "user_version": _strict_int(data["user_version"], "Collector user_version"),
        "schema_fingerprint": _require_fingerprint(data["schema_fingerprint"], "Collector schema fingerprint"),
        "tables": dict(tables), "integrity_check": "ok",
    })


def _postgres_evidence(payload: object, archive_fingerprint: str) -> Mapping[str, object]:
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
            "archive_fingerprint": _require_fingerprint(archive_fingerprint, "archive fingerprint"),
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
    header = rdb_path.read_bytes()[:9]
    if len(header) != 9 or not header.startswith(b"REDIS") or not header[5:].isdigit():
        raise MigrationError("Redis snapshot header is invalid")
    rdb_version = int(header[5:])
    result: dict[str, object] = {
        "version": data["version"],
        "rdb_version": rdb_version,
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
    archive_fingerprint: str,
) -> dict[str, Mapping[str, object]]:
    suffix = require_migration_id(directory.name).replace("-", "")
    pg_container = f"car-agent-migration-{suffix}-pg"
    redis_container = f"car-agent-migration-{suffix}-redis"
    readonly_mount = f"type=bind,source={directory.resolve()},target=/snapshot,readonly"
    runner.run([
        "docker", "run", "--pull", "never", "-d", "--name", pg_container, "--tmpfs",
        "/var/lib/postgresql/data:rw,noexec,nosuid", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
        services["postgres"].image,
    ], cwd=repo)
    try:
        _wait_for_command(
            ["docker", "exec", pg_container, "pg_isready", "-U", "postgres"],
            runner, repo=repo, label="PostgreSQL",
        )
        runner.run_from_file([
            "docker", "exec", "-i", pg_container, "pg_restore", "-U", "postgres", "-d",
            "postgres", "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--exit-on-error",
        ], directory / "postgres.dump", cwd=repo)
        postgres_raw = runner.json([
            "docker", "exec", pg_container, "psql", "-U", "postgres", "-d", "postgres",
            "-At", "--command", POSTGRES_AGGREGATE_SQL,
        ], cwd=repo)
    finally:
        runner.run(["docker", "rm", "-f", pg_container], cwd=repo)
    runner.run([
        "docker", "run", "--pull", "never", "-d", "--name", redis_container, "--mount", readonly_mount,
        "--entrypoint", "redis-server", services["redis"].image,
        "--dir", "/snapshot", "--dbfilename", "redis.rdb", "--appendonly", "no",
        "--protected-mode", "no",
    ], cwd=repo)
    try:
        _wait_for_command(
            ["docker", "exec", redis_container, "redis-cli", "PING"],
            runner, repo=repo, label="Redis",
        )
        redis_raw = runner.json([
            "docker", "exec", redis_container, "redis-cli", "--json", "EVAL",
            REDIS_AGGREGATE_LUA, "0",
        ], cwd=repo)
    finally:
        runner.run(["docker", "rm", "-f", redis_container], cwd=repo)
    return {
        "postgres": _postgres_evidence(postgres_raw, archive_fingerprint),
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


def load_bundle(repo: Path, migration_id: str) -> MigrationBundle:
    require_migration_id(migration_id)
    root = repo / ".artifacts" / "cloud-data-migrations"
    directory = artifact_directory(root, migration_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise MigrationError("migration manifest is unavailable")
    try:
        if manifest_path.stat().st_size > CONTROL_JSON_MAX_BYTES:
            raise MigrationError("migration manifest exceeds byte limit")
        payload = parse_control_json(manifest_path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("migration manifest is unreadable") from exc
    manifest = parse_manifest(payload)
    if manifest.migration_id != migration_id:
        raise MigrationError("migration manifest identity mismatch")
    files = tuple(directory / name for name in (*SNAPSHOT_FILENAMES, "manifest.json"))
    return MigrationBundle(directory=directory, manifest=manifest, files=files)


def validate_bundle(directory: Path) -> MigrationBundle:
    if not directory.is_dir() or directory.is_symlink():
        raise MigrationError("migration bundle directory is invalid")
    bundle = load_bundle(directory.parents[2], directory.name)
    if bundle.directory.resolve() != directory.resolve():
        raise MigrationError("migration bundle path is invalid")
    for name, record in bundle.manifest.files.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != record.size_bytes:
            raise MigrationError(f"migration file is invalid: {name}")
        if sha256_file(path) != record.sha256:
            raise MigrationError(f"migration file checksum mismatch: {name}")
    return bundle


REMOTE_SCRIPT = "/opt/car-agent/shared/bin/remote-data-migration.sh"


def _remote_json_result(result: object) -> Mapping[str, object]:
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > 64 * 1024:
        raise MigrationError("remote migration response is invalid")
    try:
        payload = parse_control_json(stdout, max_bytes=64 * 1024)
    except MigrationError as exc:
        raise MigrationError("remote migration response is invalid") from exc
    expected = frozenset(
        {"current_release", "runtime_project_name", "disk_available_bytes", "stores", "status"}
    )
    return _exact_keys(payload, expected, "remote migration response")


def inspect_remote(request: MigrationRequest, runner: RemoteCommandRunner) -> Mapping[str, object]:
    result = runner.run(
        request.ssh.ssh_argv(f"sudo {REMOTE_SCRIPT} inspect-current"), cwd=request.repo,
        timeout_s=60,
    )
    payload = _remote_json_result(result)
    release = payload["current_release"]
    project = payload["runtime_project_name"]
    if not isinstance(release, str) or re.fullmatch(r"/opt/car-agent/releases/[0-9a-f]{7,40}", release) is None:
        raise MigrationError("remote current release is invalid")
    if not isinstance(project, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) is None:
        raise MigrationError("remote runtime project is invalid")
    _strict_int(payload["disk_available_bytes"], "remote disk availability")
    stores = payload["stores"]
    if not isinstance(stores, Mapping) or set(stores) != {"postgres", "redis", "collector"}:
        raise MigrationError("remote store inspection is incomplete")
    return payload


def make_migration_plan(
    request: MigrationRequest, runner: RemoteCommandRunner
) -> MigrationPlan:
    bundle = validate_bundle(request.bundle.directory)
    remote = inspect_remote(request, runner)
    current_sha = Path(remote["current_release"]).name  # type: ignore[arg-type]
    if current_sha != bundle.manifest.source_sha:
        raise MigrationError("remote source release does not match migration bundle")
    stores = remote["stores"]
    postgres_remote = stores["postgres"]  # type: ignore[index]
    redis_remote = stores["redis"]  # type: ignore[index]
    collector_remote = stores["collector"]  # type: ignore[index]
    if not all(isinstance(item, Mapping) for item in (postgres_remote, redis_remote, collector_remote)):
        raise MigrationError("remote store inspection is invalid")
    postgres = bundle.manifest.postgres
    if (postgres_remote.get("major") != postgres["major"]
            or postgres_remote.get("vector_version") != postgres["vector_version"]
            or postgres_remote.get("schema_fingerprint") != postgres["schema_fingerprint"]):
        raise MigrationError("remote PostgreSQL compatibility check failed")
    redis_version = redis_remote.get("version")
    if not isinstance(redis_version, str) or redis_version.split(".", 1)[0] != str(bundle.manifest.redis["version"]).split(".", 1)[0]:
        raise MigrationError("remote Redis major version mismatch")
    collector = bundle.manifest.collector
    if (collector_remote.get("user_version") != collector["user_version"]
            or collector_remote.get("schema_fingerprint") != collector["schema_fingerprint"]):
        raise MigrationError("remote Collector compatibility check failed")
    bundle_size = sum((bundle.directory / name).stat().st_size for name in SNAPSHOT_FILENAMES)
    available = _strict_int(remote["disk_available_bytes"], "remote disk availability")
    if available < max(bundle_size * 3, 1024 * 1024):
        raise MigrationError("remote disk availability is insufficient")
    return MigrationPlan(
        migration_id=request.migration_id,
        status="ready",
        current_release=remote["current_release"],  # type: ignore[arg-type]
        disk_available_bytes=available,
        bundle_size_bytes=bundle_size,
        remote_stores=MappingProxyType(dict(stores)),  # type: ignore[arg-type]
    )


def upload_bundle(request: MigrationRequest, runner: RemoteCommandRunner) -> str:
    directory = request.bundle.directory
    validate_bundle(directory)
    prepared = runner.run(
        request.ssh.ssh_argv(
            f"sudo {REMOTE_SCRIPT} prepare-upload --migration-id {request.migration_id}"
        ),
        cwd=request.repo, timeout_s=120,
    ).stdout.strip()
    expected = f"/opt/car-agent/shared/imports/{request.migration_id}"
    if prepared != expected:
        raise MigrationError("server returned an unexpected import directory")
    for name in ("manifest.json", "postgres.dump", "redis.rdb", "collector.db"):
        runner.run(request.ssh.scp_argv(directory / name, f"{expected}/{name}"), cwd=request.repo,
                   timeout_s=1800)
    runner.run(
        request.ssh.ssh_argv(
            f"sudo {REMOTE_SCRIPT} seal-upload --migration-id {request.migration_id}"
        ),
        cwd=request.repo, timeout_s=120,
    )
    return expected


def remote_action(
    request: MigrationRequest, action: str, runner: RemoteCommandRunner
) -> str:
    if action not in {"preflight", "apply", "verify", "rollback", "recover"}:
        raise MigrationError("invalid remote migration action")
    result = runner.run(
        request.ssh.ssh_argv(
            f"sudo {REMOTE_SCRIPT} {action} --migration-id {request.migration_id}"
        ),
        cwd=request.repo, timeout_s={"preflight": 300, "verify": 600,
                                    "apply": 3600, "rollback": 3600,
                                    "recover": 3600}[action],
    )
    return result.stdout.strip()


def parse_action_status(raw: str, migration_id: str, expected: str) -> Mapping[str, object]:
    payload = parse_control_json(raw, max_bytes=64 * 1024)
    data = _exact_keys(payload, frozenset({"migration_id", "status"}), "remote action status")
    if data["migration_id"] != migration_id or data["status"] != expected:
        raise MigrationError("remote action did not reach the expected final status")
    return data


def list_local_writers(repo: Path, runner: BinaryCommandRunner) -> tuple[str, ...]:
    try:
        selection = resolve_target(repo.resolve())
    except DevStackError as exc:
        raise MigrationError("local development stack target is invalid") from exc
    if selection.name != "local":
        raise MigrationError("writer inspection requires the local development stack target")
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    raw = runner.text([*compose, "config", "--services"], cwd=repo)
    services = tuple(line.strip() for line in raw.splitlines() if line.strip())
    if not services or any(re.fullmatch(r"[a-z0-9-]+", item) is None for item in services):
        raise MigrationError("compose service list is invalid")
    writers = tuple(item for item in services if item not in {"postgres", "redis"})
    if "observability-collector" not in writers:
        raise MigrationError("collector writer is missing from compose services")
    return writers


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
        image_id = inspect.get("Image")
        if (
            not isinstance(config, Mapping)
            or not isinstance(config.get("Image"), str)
            or not isinstance(image_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        ):
            raise MigrationError(f"source image is unavailable: {service}")
        mount = _exact_mount(inspect.get("Mounts"), destination)
        found[service] = SourceService(
            service=service,
            container_id=container_id,
            image=image_id,
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
            "docker", "run", "--pull", "never", "--rm",
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
            "docker", "run", "--pull", "never", "--rm", "--volumes-from", f"{service.container_id}:ro",
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
) -> str:
    readonly_mount = f"type=bind,source={directory.resolve()},target=/snapshot,readonly"
    pg_result = runner.text(
        ["docker", "run", "--pull", "never", "--rm", "--mount", readonly_mount, "--entrypoint", "pg_restore",
         services["postgres"].image, "--list", "/snapshot/postgres.dump"],
        cwd=repo,
    )
    if not pg_result.strip():
        raise MigrationError("PostgreSQL snapshot format validation failed")
    redis_result = runner.text(
        ["docker", "run", "--pull", "never", "--rm", "--mount", readonly_mount, "--entrypoint", "redis-check-rdb",
         services["redis"].image, "/snapshot/redis.rdb"],
        cwd=repo,
    )
    if "CRC64 checksum is OK" not in redis_result:
        raise MigrationError("Redis snapshot format validation failed")
    with sqlite3.connect(f"file:{directory / 'collector.db'}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise MigrationError("Collector snapshot integrity validation failed")
    normalized = "\n".join(
        line.rstrip() for line in pg_result.splitlines()
        if not line.startswith("; Archive created at")
    ) + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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


def _wait_for_command(
    argv: Sequence[str], runner: BinaryCommandRunner, *, repo: Path, label: str,
    attempts: int = 20,
) -> None:
    last_error: MigrationError | None = None
    for attempt in range(attempts):
        try:
            runner.run(argv, cwd=repo)
            return
        except MigrationError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.25)
    raise MigrationError(f"{label} readiness timed out") from last_error


def _resolve_writer_identities(
    repo: Path,
    runner: BinaryCommandRunner,
) -> tuple[WriterIdentity, ...]:
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    raw = runner.text([*compose, "config", "--services"], cwd=repo)
    names = tuple(line.strip() for line in raw.splitlines() if line.strip())
    if not names or any(re.fullmatch(r"[a-z0-9-]+", name) is None for name in names):
        raise MigrationError("compose service list is invalid")
    writers = tuple(name for name in names if name not in {"postgres", "redis"})
    if not writers or "observability-collector" not in writers:
        raise MigrationError("collector writer is missing from compose services")
    identities: list[WriterIdentity] = []
    for name in writers:
        raw_cid = runner.text([*compose, "ps", "-a", "-q", name], cwd=repo)
        ids = tuple(line.strip() for line in raw_cid.splitlines() if line.strip())
        if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{12,64}", ids[0]) is None:
            raise MigrationError(f"local writer container identity is invalid: {name}")
        inspect = _strict_single_inspect(runner.json(["docker", "inspect", ids[0]], cwd=repo))
        state = inspect.get("State")
        image_id = inspect.get("Image")
        mounts = inspect.get("Mounts")
        if (not isinstance(state, Mapping) or state.get("Running") is not True
                or not isinstance(image_id, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
                or not isinstance(mounts, list)):
            raise MigrationError(f"local writer identity is unstable: {name}")
        volumes = tuple(sorted(
            item["Name"] for item in mounts
            if isinstance(item, Mapping) and item.get("Type") == "volume"
            and isinstance(item.get("Name"), str)
        ))
        identities.append(WriterIdentity(name, ids[0], image_id, volumes))
    return tuple(identities)


def _quiesce_local_writers(
    repo: Path, identities: Sequence[WriterIdentity], runner: BinaryCommandRunner,
) -> None:
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    writers = tuple(item.service for item in identities)
    runner.run([*compose, "stop", *writers], cwd=repo)
    for identity in identities:
        name = identity.service
        raw_cid = runner.text([*compose, "ps", "-a", "-q", name], cwd=repo)
        ids = tuple(line.strip() for line in raw_cid.splitlines() if line.strip())
        if ids != (identity.container_id,):
            raise MigrationError(f"local writer container identity is invalid: {name}")
        inspect = _strict_single_inspect(
            runner.json(["docker", "inspect", ids[0]], cwd=repo)
        )
        state = inspect.get("State")
        if (not isinstance(state, Mapping) or state.get("Running") is not False
                or state.get("Status") != "exited"):
            raise MigrationError(f"local writer did not exit: {name}")


def _restart_local_writers(
    repo: Path, identities: Sequence[WriterIdentity], runner: BinaryCommandRunner,
) -> None:
    container_ids = tuple(item.container_id for item in identities)
    runner.run(["docker", "start", *container_ids], cwd=repo)
    for identity in identities:
        inspect = _strict_single_inspect(
            runner.json(["docker", "inspect", identity.container_id], cwd=repo)
        )
        state = inspect.get("State")
        if not isinstance(state, Mapping) or state.get("Running") is not True:
            raise MigrationError(f"local writer recovery failed: {identity.service}")


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
    repo = repo.resolve()
    try:
        selection = resolve_target(repo)
    except DevStackError as exc:
        raise MigrationError("local development stack target is invalid") from exc
    if selection.name != "local":
        raise MigrationError("snapshot requires the local development stack target")
    clock = now or (lambda: datetime.now(UTC))
    current = clock()
    source_sha = runner.text(["git", "rev-parse", "HEAD"], cwd=repo).strip()
    migration_id = new_migration_id(current, source_sha, phase)
    artifact_root = artifact_root.absolute()
    artifact_directory(artifact_root, migration_id)
    if shutil.disk_usage(repo).free < MIN_SNAPSHOT_FREE_BYTES:
        raise MigrationError("insufficient local snapshot disk space")
    services = discover_source_services(repo, runner)
    writer_identities: tuple[WriterIdentity, ...] = ()
    if phase == "final":
        writer_identities = _resolve_writer_identities(repo, runner)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_directory(artifact_root, migration_id)
    directory.mkdir(mode=0o700)
    restrict_private_tree(directory, runner)
    atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURING"})
    recovery_needed = False
    try:
        if phase == "final":
            recovery_needed = True
            atomic_private_json(directory / "status.json", {
                "migration_id": migration_id, "status": "RECOVERY_NEEDED",
                "writer_container_ids": [item.container_id for item in writer_identities],
            })
            _quiesce_local_writers(repo, writer_identities, runner)
        pg_partial = directory / "postgres.dump.partial"
        capture_postgres(services["postgres"], pg_partial, runner, repo=repo)
        private_replace(pg_partial, directory / "postgres.dump")
        capture_redis(services["redis"], directory, runner, repo=repo)
        capture_collector(services["observability-collector"], directory, runner, repo=repo)
        archive_fingerprint = _verify_snapshots(directory, services, runner, repo)
        evidence = collect_aggregate_evidence(
            repo, services, runner, directory, archive_fingerprint
        )
        snapshot = SnapshotEvidence(
            directory=directory,
            migration_id=migration_id,
            phase=phase,
            source_sha=source_sha,
            created_at=current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            **evidence,
        )
        manifest = build_manifest(snapshot)
        payload = manifest_payload(manifest)
        atomic_private_json(directory / "manifest.json", payload)
        atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURED"})
    except BaseException:
        atomic_private_json(directory / "status.json", {"migration_id": migration_id, "status": "CAPTURE_FAILED"})
        if recovery_needed:
            try:
                _restart_local_writers(repo, writer_identities, runner)
                atomic_private_json(directory / "status.json", {
                    "migration_id": migration_id, "status": "WRITERS_RECOVERED",
                    "writer_container_ids": [item.container_id for item in writer_identities],
                })
            except BaseException as recovery_error:
                atomic_private_json(directory / "status.json", {
                    "migration_id": migration_id, "status": "RECOVERY_FAILED",
                    "writer_container_ids": [item.container_id for item in writer_identities],
                    "recovery_error": type(recovery_error).__name__,
                })
        raise
    files = tuple(directory / name for name in (*SNAPSHOT_FILENAMES, "manifest.json", "status.json"))
    return MigrationBundle(directory=directory, manifest=manifest, files=files)

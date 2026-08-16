from __future__ import annotations

import hashlib
import json
import os
import re
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

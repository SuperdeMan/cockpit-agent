#!/usr/bin/env python3
"""Prepare a Redis volume for import with crash-reconciling renames."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path


class InjectedCrash(RuntimeError):
    """Test-only crash point."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _crash(label: str, selected: str | None) -> None:
    if label == selected:
        raise InjectedCrash(label)


def _safe_entry(path: Path, *, directory: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return expected(metadata.st_mode) and (directory or metadata.st_nlink == 1)


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _privatize(path: Path) -> None:
    entries = [path]
    if path.is_dir():
        entries.extend(path.rglob("*"))
    for entry in entries:
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise ValueError("Redis volume tree contains an unsafe entry")
        if hasattr(os, "chown"):
            os.chown(entry, 0, 0, follow_symlinks=False)
        os.chmod(entry, 0o700 if stat.S_ISDIR(metadata.st_mode) else 0o600)


def _write_marker(path: Path, payload: dict[str, str]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    _fsync_directory(path.parent)


def _remove_safe_tree(path: Path) -> None:
    if not _entry_exists(path):
        return
    if not _safe_entry(path, directory=True):
        raise ValueError("Redis incomplete AOF tree is unsafe")
    _privatize(path)
    shutil.rmtree(path)
    _fsync_directory(path.parent)


def finalize_aof(incoming: Path, data: Path, rollback: Path, expected_manifest_sha: str) -> None:
    source_sha = _digest(incoming)
    partial = data / "appendonlydir.migration.partial"
    target = data / "appendonlydir"
    if not _safe_entry(partial, directory=True) or _entry_exists(target):
        raise ValueError("Redis completed AOF tree is ambiguous")
    os.replace(partial, target)
    _privatize(target)
    _fsync_directory(data)
    _write_marker(rollback / "redis-aof-complete.json", {
        "schema_version": "1", "source_sha256": source_sha,
        "manifest_sha256": expected_manifest_sha, "phase": "complete",
    })


def prepare_redis_volume(
    incoming: Path, data: Path, rollback: Path, *, kill_point: str | None = None,
    expected_manifest_sha: str = "0" * 64,
) -> str:
    if not _safe_entry(incoming, directory=False):
        raise ValueError("Redis source is not a regular file")
    source_sha = _digest(incoming)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    rollback.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(rollback, 0o700)
    marker = rollback / "redis-replace.json"
    if marker.exists():
        if not _safe_entry(marker, directory=False) or marker.stat().st_size > 4096:
            raise ValueError("Redis replacement marker is unsafe")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload != {"schema_version": "1", "source_sha256": source_sha, "phase": payload.get("phase")}:
            raise ValueError("Redis replacement marker identity is invalid")
        if payload["phase"] not in {"backing_up", "prepared"}:
            raise ValueError("Redis replacement marker phase is invalid")
    else:
        payload = {"schema_version": "1", "source_sha256": source_sha, "phase": "backing_up"}
        _write_marker(marker, payload)

    completion = rollback / "redis-aof-complete.json"
    expected_completion = {
        "schema_version": "1", "source_sha256": source_sha,
        "manifest_sha256": expected_manifest_sha, "phase": "complete",
    }
    if _entry_exists(completion):
        if not _safe_entry(completion, directory=False) or completion.stat().st_size > 4096:
            raise ValueError("Redis AOF completion marker is unsafe")
        if json.loads(completion.read_text(encoding="utf-8")) != expected_completion:
            raise ValueError("Redis AOF completion marker identity is invalid")
        if not _safe_entry(data / "appendonlydir", directory=True):
            raise ValueError("Redis completed AOF tree is missing")
        return "resume-aof"

    for name, directory in (("dump.rdb", False), ("appendonlydir", True)):
        current = data / name
        saved = rollback / name
        if saved.exists() and not _safe_entry(saved, directory=directory):
            raise ValueError("Redis rollback entry is unsafe")
        if current.exists() and not _safe_entry(current, directory=directory):
            raise ValueError("Redis data entry is unsafe")
        if saved.exists() and current.exists():
            if (payload["phase"] == "prepared"
                    or (name == "dump.rdb" and _digest(current) == source_sha)):
                continue
            raise ValueError("Redis rename state is ambiguous")
        if current.exists() and not saved.exists():
            _crash(f"before-old-{name}", kill_point)
            os.replace(current, saved)
            _privatize(saved)
            _fsync_directory(data)
            _fsync_directory(rollback)
            _crash(f"after-old-{name}", kill_point)

    _remove_safe_tree(data / "appendonlydir.migration.partial")
    _remove_safe_tree(data / "appendonlydir")
    target = data / "dump.rdb"
    if payload["phase"] == "prepared":
        return "resume-rdb"
    if target.exists():
        if not _safe_entry(target, directory=False) or _digest(target) != source_sha:
            raise ValueError("Redis prepared dump identity is invalid")
    else:
        partial = data / "dump.rdb.migration.partial"
        if partial.exists():
            if not _safe_entry(partial, directory=False) or _digest(partial) != source_sha:
                raise ValueError("Redis partial dump identity is invalid")
        else:
            descriptor = os.open(
                partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with incoming.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        view = memoryview(chunk)
                        while view:
                            written = os.write(descriptor, view)
                            view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _crash("before-new-dump.rdb", kill_point)
        os.replace(partial, target)
        os.chmod(target, 0o600)
        _fsync_directory(data)
        _crash("after-new-dump.rdb", kill_point)
    payload["phase"] = "prepared"
    _write_marker(marker, payload)
    return "prepared"


def main(argv: list[str]) -> int:
    if len(argv) == 6 and argv[1] == "--complete":
        finalize_aof(Path(argv[2]), Path(argv[3]), Path(argv[4]), argv[5])
        return 0
    if len(argv) not in {4, 5}:
        raise SystemExit("usage: redis_volume_prepare.py SOURCE DATA_DIR ROLLBACK_DIR [MANIFEST_SHA]")
    print(prepare_redis_volume(
        Path(argv[1]), Path(argv[2]), Path(argv[3]),
        kill_point=os.environ.get("MIGRATION_KILL_POINT"),
        expected_manifest_sha=argv[4] if len(argv) == 5 else "0" * 64,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

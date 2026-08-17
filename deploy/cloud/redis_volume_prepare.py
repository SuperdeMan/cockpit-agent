#!/usr/bin/env python3
"""Prepare a Redis volume for import with crash-reconciling renames."""

from __future__ import annotations

import hashlib
import json
import os
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


def prepare_redis_volume(
    incoming: Path, data: Path, rollback: Path, *, kill_point: str | None = None,
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

    target = data / "dump.rdb"
    if payload["phase"] == "prepared":
        return "resume-aof" if (data / "appendonlydir").exists() else "resume-rdb"
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
    if len(argv) != 4:
        raise SystemExit("usage: redis_volume_prepare.py SOURCE DATA_DIR ROLLBACK_DIR")
    print(prepare_redis_volume(
        Path(argv[1]), Path(argv[2]), Path(argv[3]),
        kill_point=os.environ.get("MIGRATION_KILL_POINT"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

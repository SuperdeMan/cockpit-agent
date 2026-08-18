#!/usr/bin/env python3
"""Crash-reconciling, streaming Collector database replacement."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import sys
from pathlib import Path


class InjectedCrash(RuntimeError):
    """Test-only crash point; production leaves ``kill_point`` unset."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _regular(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def _crash(label: str, selected: str | None) -> None:
    if selected == label:
        raise InjectedCrash(label)


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_durable(source: Path, target: Path) -> None:
    if not _regular(source):
        raise ValueError("Collector data entry is unsafe")
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    target_fd = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        while chunk := os.read(source_fd, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
    finally:
        os.close(target_fd)
        os.close(source_fd)


def _backup_cross_mount(current: Path, saved: Path) -> None:
    partial = saved.parent / f".{saved.name}.migration.partial"
    if partial.exists():
        if not _regular(partial):
            raise ValueError("Collector rollback partial is unsafe")
        partial.unlink()
    _copy_durable(current, partial)
    os.replace(partial, saved)
    os.chmod(saved, 0o600)
    _fsync_directory(saved.parent)
    if _digest(current) != _digest(saved):
        raise ValueError("Collector rollback copy identity mismatch")
    current.unlink()
    _fsync_directory(current.parent)


def replace_collector_database(
    incoming: Path, data: Path, rollback: Path, *, kill_point: str | None = None,
) -> None:
    if not _regular(incoming):
        raise ValueError("Collector source is not a regular file")
    # `immutable=1` 不是可选项：incoming 是**封存的迁移包**，按 `--mount …,readonly`
    # 挂进来。它的 header 里 `journal_mode=wal`，而以 `mode=ro` 打开 WAL 库需要建
    # `-shm` ⇒ 只读挂载上直接 `unable to open database file`。
    # 2026-08-18 真栈实证：`collector-restore` 就倒在下一行，rc=1、helper 容器 94ms 退出、
    # rollback 桶一个字节没写。⚠ 本机第一次复现**没红**——因为本地批次目录里残留着
    # 前面只读打开时产生的 `-shm`/`-wal`，而云端 `/incoming` 只有 4 个规范文件。
    # > 判据：**我们自己的读路径会往「封存输入」旁边写 sidecar，那正是让复现说谎的东西。**
    # 安全性已单独证过：快照走 `backup()` + `wal_checkpoint`，`-wal` 为 0 字节，
    # 用 `immutable=1` 读到的四表行数与 manifest 逐字一致。
    connection = sqlite3.connect(
        f"file:{incoming.as_posix()}?mode=ro&immutable=1", uri=True,
    )
    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Collector source integrity check failed")
    finally:
        connection.close()
    source_digest = _digest(incoming)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    rollback.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(rollback, 0o700)
    target = data / "obs.db"
    if _regular(target) and _digest(target) == source_digest:
        return

    for name in ("obs.db", "obs.db-wal", "obs.db-shm"):
        current = data / name
        saved = rollback / name
        if saved.exists() and not _regular(saved):
            raise ValueError("Collector rollback entry is unsafe")
        if not current.exists():
            continue
        if saved.exists():
            if _digest(current) != _digest(saved):
                raise ValueError("Collector rename state is ambiguous")
            current.unlink()
            _fsync_directory(data)
            continue
        if current.is_symlink() or not _regular(current):
            raise ValueError("Collector data entry is unsafe")
        _crash(f"before-old-{name}", kill_point)
        _backup_cross_mount(current, saved)
        _crash(f"after-old-{name}", kill_point)

    partial = data / "obs.db.migration.partial"
    if partial.exists():
        if not _regular(partial) or _digest(partial) != source_digest:
            raise ValueError("Collector partial replacement is invalid")
    else:
        descriptor = os.open(
            partial,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
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
        os.chmod(partial, 0o600)
        _fsync_directory(data)

    _crash("before-final-replace", kill_point)
    os.replace(partial, target)
    os.chmod(target, 0o600)
    _fsync_directory(data)
    _crash("after-final-replace", kill_point)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        raise SystemExit("usage: collector_volume_replace.py SOURCE DATA_DIR ROLLBACK_DIR")
    replace_collector_database(
        Path(argv[1]), Path(argv[2]), Path(argv[3]),
        kill_point=os.environ.get("MIGRATION_KILL_POINT"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

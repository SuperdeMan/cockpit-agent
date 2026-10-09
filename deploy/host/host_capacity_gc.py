#!/usr/bin/python3
"""Hourly capacity GC for the cloud host shared by car-agent and drone-agent.

Design: docs/design/2026-09-28-cloud-host-capacity-governance.md §4.4–4.5 (P2, §4.4 revised
2026-10-09); install and ownership: deploy/host/README.md. Installed as
/usr/local/sbin/host-capacity-gc and run only by host-capacity-gc.service. It does exactly two things:

1. 只在磁盘紧张时回收共享的 BuildKit 缓存。任何一轮真删了记录的 prune 都会让 BuildKit 解绑缓存键，
   两个项目的下一次构建因此大面积冷启动，所以空间充足时完全不调用 prune。每轮依次判断：
   - 可用（statvfs f_bavail，与 car-agent 构建闸 `df --output=avail` 同口径）不低于
     capacity.target_free_gib：not_needed，并清除退避状态；
   - 上一次回收后仍未达到目标线、且可用还没比那时再降 RETRY_DROP_BYTES：deferred（退避），写 warning；
   - 要回收但任一项目持锁：skipped；
   - 否则 `docker buildx prune --all --min-free-space <字节> --reserved-space <字节>`，一次清到
     build_cache.prune_to_free_gib，缓存至少留 build_cache.reserved_space_gib；仍未达标就记下退避状态。
   三个值来自 car-agent 已批准的保留策略，经其 retention.py（策略唯一的校验者）读取。
   每把锁只做一次非阻塞 flock 探测、立即释放，prune 期间不持锁：两个项目取锁都不等待，
   持锁会让对方的部署直接失败，而不是推迟。
2. Deletes apport core dumps older than seven days (regular `core.*` files, not recursive).

Every run prints one JSON record, which journald keeps (`journalctl -u host-capacity-gc -o cat`).
The build-history API is never called (2026-09-27 it panicked the shared daemon).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

GIB = 1024**3
POLICY = Path("/opt/car-agent/shared/retention-policy.json")
RETENTION_MODULE = Path("/opt/car-agent/shared/bin/retention.py")
#: BuildKit 的 --min-free-space 对它的根目录做 statfs；本机与 containerd、/opt/car-agent 同一文件系统。
BUILDKIT_ROOT = Path("/var/lib/docker/buildkit")
#: 退避状态；目录由 host-capacity-gc.service 的 StateDirectory 建立。
STATE = Path("/var/lib/host-capacity-gc/state.json")
#: 上一次回收没达到目标线时，可用要再降这么多才重试；空间没继续下降时重试，只会削掉保底之上
#: 新长出的缓存、让下一次构建冷启动，腾不出多少空间。
RETRY_DROP_BYTES = 5 * GIB
#: Lock files of both projects; drone-agent's is created by its own tooling and never removed.
LOCKS: Mapping[str, Path] = {
    "car-agent": Path("/opt/car-agent/shared/locks/release.lock"),
    "drone-agent": Path("/home/ubuntu/drone-agent/stack.lock"),
}
COREDUMP_DIR = Path("/var/lib/apport/coredump")
COREDUMP_PREFIX = "core."
COREDUMP_MAX_AGE = timedelta(days=7)
BUILDER = "default"
DU_TIMEOUT_S = 300.0
PRUNE_TIMEOUT_S = 1500.0

Runner = Callable[[Sequence[str], float], str]
LockProbe = Callable[[Path], str]
#: 返回（可用字节 f_bavail，root 保留块字节 f_bfree - f_bavail）。
DiskProbe = Callable[[Path], tuple[int, int]]


class GcError(RuntimeError):
    """A condition under which this round must not prune."""


class Thresholds(NamedTuple):
    """回收的三条线（GiB），都来自已批准的保留策略。"""

    trigger_gib: int
    prune_to_gib: int
    reserved_gib: int


def read_thresholds(policy: Path, retention_module: Path) -> Thresholds:
    """capacity.target_free_gib 与 build_cache 两项，经 car-agent 的 retention.py 校验后读出。"""
    name = "car_agent_retention_for_host_gc"
    try:
        spec = importlib.util.spec_from_file_location(name, retention_module)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve string annotations through sys.modules
        spec.loader.exec_module(module)
    except (OSError, ImportError, AttributeError, SyntaxError) as exc:
        sys.modules.pop(name, None)
        raise GcError("car-agent retention module is unavailable") from exc
    try:
        loaded = module.load_policy(policy)
    except module.RetentionError as exc:
        raise GcError(str(exc)) from exc
    try:
        return Thresholds(
            trigger_gib=loaded["capacity"]["target_free_gib"],
            prune_to_gib=loaded["build_cache"]["prune_to_free_gib"],
            reserved_gib=loaded["build_cache"]["reserved_space_gib"],
        )
    except KeyError as exc:
        # 主机上的策略与 retention.py 随基础设施锚安装，本脚本单独安装；两边新旧错配时本轮不回收
        raise GcError("retention policy lacks the build cache thresholds this script needs") from exc


def disk_space(path: Path) -> tuple[int, int]:
    """（可用字节，root 保留块字节）；可用取 f_bavail，与 df --output=avail 和构建闸同口径。"""
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize, (info.f_bfree - info.f_bavail) * info.f_frsize


def read_backoff(path: Path) -> int | None:
    """上一次未达标回收后的可用字节；没有状态时为 None，状态损坏时本轮不回收。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise GcError("backoff state is unreadable") from exc
    after = payload.get("available_after") if isinstance(payload, dict) else None
    if type(after) is not int or after < 0:
        raise GcError("backoff state is invalid")
    return after


def write_backoff(path: Path, payload: Mapping[str, object]) -> None:
    """同目录临时文件 + rename，读到的永远是完整状态。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def clear_backoff(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def probe_lock(path: Path) -> str:
    """'free', 'busy', 'missing' or 'invalid'; holds the lock only for the probe itself."""
    import fcntl  # Linux only; imported here so the module loads anywhere for tests

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "invalid"
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "free"
    finally:
        os.close(fd)


def expire_coredumps(directory: Path, now: datetime, apply: bool) -> dict:
    if directory.is_symlink() or not directory.is_dir():
        return {"status": "absent", "deleted": [], "kept": 0, "ignored": 0}
    deleted: list[dict] = []
    kept = ignored = 0
    for entry in sorted(os.scandir(directory), key=lambda item: item.name):
        info = entry.stat(follow_symlinks=False)
        if not (stat.S_ISREG(info.st_mode) and entry.name.startswith(COREDUMP_PREFIX)):
            ignored += 1
            continue
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
        if now - modified <= COREDUMP_MAX_AGE:
            kept += 1
            continue
        if apply:
            os.unlink(entry.path)  # unlink never follows a symlink swapped in after the stat
        deleted.append({
            "name": entry.name,
            "bytes": info.st_size,
            "mtime_utc": modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return {
        "status": "applied" if apply else "dry-run",
        "deleted": deleted,
        "kept": kept,
        "ignored": ignored,
    }


def _total(output: str) -> str | None:
    return next(
        (line.split(":", 1)[1].strip() for line in reversed(output.splitlines()) if line.startswith("Total:")),
        None,
    )


def reclaim_build_cache(
    limits: Thresholds,
    locks: Mapping[str, str],
    *,
    runner: Runner,
    disk: DiskProbe,
    state: Path,
    apply: bool,
    now: datetime,
    warnings: list[str],
) -> dict:
    """按模块说明的顺序决定 not_needed / deferred / skipped / dry-run / pruned。"""
    available, reserved_blocks = disk(BUILDKIT_ROOT)
    du = ["docker", "buildx", "du", "--builder", BUILDER]
    result: dict[str, object] = {
        "available_before": available,
        "trigger_free_gib": limits.trigger_gib,
        "prune_to_free_gib": limits.prune_to_gib,
        "reserved_space_gib": limits.reserved_gib,
        "total_before": _total(runner(du, DU_TIMEOUT_S)),
    }
    if available >= limits.trigger_gib * GIB:
        if apply and clear_backoff(state):
            result["backoff"] = "cleared"
        return {**result, "status": "not_needed"}
    last_after = read_backoff(state)
    if last_after is not None and available >= last_after - RETRY_DROP_BYTES:
        warnings.append(
            f"build cache prune deferred: the last prune left {last_after} bytes available, below the "
            f"{limits.prune_to_gib} GiB target; free space is taken by data outside the build cache"
        )
        return {**result, "status": "deferred", "retry_below": last_after - RETRY_DROP_BYTES}
    busy = sorted(name for name, lock_state in locks.items() if lock_state == "busy")
    if busy:
        return {**result, "status": "skipped", "busy": busy}
    # BuildKit 的 --min-free-space 比较的是 Bfree（含 root 保留块），目标线按 Bavail 定，所以把保留块加回去；
    # 两个参数都按字节传：docker 把 "60GB" 这类写法按 1024 进制解析，容易和 du 的 1000 进制混淆。
    min_free = limits.prune_to_gib * GIB + reserved_blocks
    result["min_free_space"] = min_free
    if not apply:
        return {**result, "status": "dry-run"}
    prune = ["docker", "buildx", "prune", "--builder", BUILDER, "--force", "--all"]
    prune += ["--min-free-space", str(min_free), "--reserved-space", str(limits.reserved_gib * GIB)]
    pruned = runner(prune, PRUNE_TIMEOUT_S)
    result["reclaimed"] = _total(pruned)
    result["total_after"] = _total(runner(du, DU_TIMEOUT_S))
    after, _ = disk(BUILDKIT_ROOT)
    result["available_after"] = after
    if after < limits.prune_to_gib * GIB:
        write_backoff(state, {"pruned_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "available_after": after})
        result["backoff"] = "set"
        warnings.append(
            f"build cache prune fell short: {after} bytes available after pruning, below the "
            f"{limits.prune_to_gib} GiB target; the next prune waits until available drops "
            f"below {after - RETRY_DROP_BYTES} bytes"
        )
    elif clear_backoff(state):
        result["backoff"] = "cleared"
    return {**result, "status": "pruned"}


def _run(argv: Sequence[str], timeout: float) -> str:
    try:
        completed = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise GcError("command timed out: " + " ".join(argv[:3])) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or [""]
        raise GcError(f"command failed: {' '.join(argv[:3])}: {detail[0][:300]}")
    return completed.stdout


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = _run,
    probe: LockProbe = probe_lock,
    disk: DiskProbe = disk_space,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="host capacity GC: build cache reclaim and old core dumps")
    parser.add_argument("--dry-run", action="store_true", help="report only; prune, delete and record nothing")
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--retention-module", type=Path, default=RETENTION_MODULE)
    parser.add_argument("--coredump-dir", type=Path, default=COREDUMP_DIR)
    parser.add_argument("--state", type=Path, default=STATE)
    args = parser.parse_args(argv)
    apply = not args.dry_run
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    warnings: list[str] = []
    record: dict[str, object] = {
        "schema_version": 2,
        "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "apply" if apply else "dry-run",
        "errors": errors,
        "warnings": warnings,
    }
    try:
        record["coredumps"] = expire_coredumps(args.coredump_dir, now, apply)
    except OSError as exc:
        record["coredumps"] = {"status": "error"}
        errors.append(f"coredumps: {type(exc).__name__}: {exc}")
    locks = {name: probe(path) for name, path in LOCKS.items()}
    record["locks"] = locks
    try:
        limits = read_thresholds(args.policy, args.retention_module)
        unusable = sorted(name for name, state in locks.items() if state in {"missing", "invalid"})
        if unusable:
            raise GcError("lock file missing or invalid: " + ", ".join(unusable))
        record["build_cache"] = reclaim_build_cache(
            limits, locks, runner=runner, disk=disk, state=args.state, apply=apply, now=now, warnings=warnings,
        )
    except GcError as exc:
        record["build_cache"] = {"status": "error"}
        errors.append(f"build_cache: {exc}")
    except OSError as exc:
        record["build_cache"] = {"status": "error"}
        errors.append(f"build_cache: {type(exc).__name__}: {exc}")
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/python3
"""Hourly capacity GC for the cloud host shared by car-agent and drone-agent.

Design: docs/design/2026-09-28-cloud-host-capacity-governance.md §4.4–4.5 (P2); install
and ownership: deploy/host/README.md. Installed as /usr/local/sbin/host-capacity-gc and
run only by host-capacity-gc.service. It does exactly two things:

1. Caps the shared BuildKit cache with `docker buildx prune --all --max-used-space <cap>`.
   <cap> is build_cache.max_used_space of car-agent's approved retention policy, read through
   car-agent's retention.py, the policy's only validator. A round is skipped while either
   project holds its lock. Each lock is probed with one non-blocking flock that is released at
   once, and the prune holds no lock: both projects take their locks without waiting, so a
   lock held through the prune would fail their deploys instead of delaying them.
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

POLICY = Path("/opt/car-agent/shared/retention-policy.json")
RETENTION_MODULE = Path("/opt/car-agent/shared/bin/retention.py")
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


class GcError(RuntimeError):
    """A condition under which this round must not prune."""


def read_cap(policy: Path, retention_module: Path) -> str:
    """build_cache.max_used_space, validated by car-agent's retention.py."""
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
        return module.load_policy(policy)["build_cache"]["max_used_space"]
    except module.RetentionError as exc:
        raise GcError(str(exc)) from exc


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


def cap_build_cache(cap: str, runner: Runner, apply: bool) -> dict:
    du = ["docker", "buildx", "du", "--builder", BUILDER]
    result: dict[str, object] = {"cap": cap, "total_before": _total(runner(du, DU_TIMEOUT_S))}
    if not apply:
        return {**result, "status": "dry-run"}
    pruned = runner(
        ["docker", "buildx", "prune", "--builder", BUILDER, "--force", "--all", "--max-used-space", cap],
        PRUNE_TIMEOUT_S,
    )
    result["reclaimed"] = _total(pruned)
    result["total_after"] = _total(runner(du, DU_TIMEOUT_S))
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
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="host capacity GC: build cache cap and old core dumps")
    parser.add_argument("--dry-run", action="store_true", help="report only; prune and delete nothing")
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--retention-module", type=Path, default=RETENTION_MODULE)
    parser.add_argument("--coredump-dir", type=Path, default=COREDUMP_DIR)
    args = parser.parse_args(argv)
    apply = not args.dry_run
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    record: dict[str, object] = {
        "schema_version": 1,
        "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "apply" if apply else "dry-run",
        "errors": errors,
    }
    try:
        record["coredumps"] = expire_coredumps(args.coredump_dir, now, apply)
    except OSError as exc:
        record["coredumps"] = {"status": "error"}
        errors.append(f"coredumps: {type(exc).__name__}: {exc}")
    locks = {name: probe(path) for name, path in LOCKS.items()}
    record["locks"] = locks
    try:
        cap = read_cap(args.policy, args.retention_module)
        unusable = sorted(name for name, state in locks.items() if state in {"missing", "invalid"})
        if unusable:
            raise GcError("lock file missing or invalid: " + ", ".join(unusable))
        busy = sorted(name for name, state in locks.items() if state == "busy")
        if busy:
            record["build_cache"] = {"status": "skipped", "cap": cap, "busy": busy}
        else:
            record["build_cache"] = cap_build_cache(cap, runner, apply)
    except GcError as exc:
        record["build_cache"] = {"status": "error"}
        errors.append(f"build_cache: {exc}")
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

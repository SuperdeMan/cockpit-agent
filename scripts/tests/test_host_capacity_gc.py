"""Host capacity GC: build cache cap and old core dumps (design 2026-09-28 §4.4–4.5, P2)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "deploy" / "host"
POLICY = ROOT / "deploy" / "cloud" / "retention-policy.json"
RETENTION = ROOT / "deploy" / "cloud" / "retention.py"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
DU = ["docker", "buildx", "du", "--builder", "default"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gc = _load("host_capacity_gc", HOST / "host_capacity_gc.py")


class _Runner:
    def __init__(self, fail_prune: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.totals = ["21.5GB", "19.9GB"]
        self.fail_prune = fail_prune

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if list(argv) == DU:
            return f"ID\tRECLAIMABLE\tSIZE\tLAST ACCESSED\nShared:\t8GB\nTotal:\t{self.totals.pop(0)}\n"
        if list(argv[:3]) == ["docker", "buildx", "prune"]:
            if self.fail_prune:
                raise gc.GcError("command failed: docker buildx prune: unknown flag")
            return "ID\tRECLAIMABLE\tSIZE\tLAST ACCESSED\nTotal:\t1.6GB\n"
        raise AssertionError(argv)


def _run(tmp_path: Path, capsys, *args: str, locks=None, runner=None, policy: Path = POLICY):
    states = locks or {}
    runner = runner or _Runner()
    code = gc.main(
        ["--policy", str(policy), "--retention-module", str(RETENTION),
         "--coredump-dir", str(tmp_path / "coredump"), *args],
        runner=runner,
        probe=lambda path: states.get(path, "free"),
        now=NOW,
    )
    return code, json.loads(capsys.readouterr().out), runner


def _age(path: Path, days: float) -> None:
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_cap_is_read_from_the_approved_policy_through_retention():
    expected = json.loads(POLICY.read_text(encoding="utf-8"))["build_cache"]["max_used_space"]
    assert gc.read_cap(POLICY, RETENTION) == expected


def test_prune_uses_the_policy_cap_when_both_locks_are_free(tmp_path: Path, capsys):
    code, record, runner = _run(tmp_path, capsys)

    assert code == 0 and record["errors"] == []
    assert record["locks"] == {"car-agent": "free", "drone-agent": "free"}
    assert runner.calls == [
        DU,
        ["docker", "buildx", "prune", "--builder", "default", "--force", "--all", "--max-used-space", "20GB"],
        DU,
    ]
    assert record["build_cache"] == {
        "status": "pruned", "cap": "20GB", "total_before": "21.5GB", "reclaimed": "1.6GB", "total_after": "19.9GB",
    }


@pytest.mark.parametrize("holder", ["car-agent", "drone-agent"])
def test_a_held_lock_skips_the_round_without_touching_the_cache(tmp_path: Path, capsys, holder: str):
    code, record, runner = _run(tmp_path, capsys, locks={gc.LOCKS[holder]: "busy"})

    assert code == 0 and runner.calls == []
    assert record["build_cache"] == {"status": "skipped", "cap": "20GB", "busy": [holder]}


@pytest.mark.parametrize("state", ["missing", "invalid"])
def test_an_unusable_lock_file_fails_the_round(tmp_path: Path, capsys, state: str):
    code, record, runner = _run(tmp_path, capsys, locks={gc.LOCKS["drone-agent"]: state})

    assert code == 1 and runner.calls == []
    assert record["build_cache"] == {"status": "error"}
    assert record["errors"] == ["build_cache: lock file missing or invalid: drone-agent"]


def test_an_invalid_policy_prunes_nothing(tmp_path: Path, capsys):
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["build_cache"]["max_used_space"] = "20 GiB"
    broken = tmp_path / "retention-policy.json"
    broken.write_text(json.dumps(policy), encoding="utf-8")

    code, record, runner = _run(tmp_path, capsys, policy=broken)

    assert code == 1 and runner.calls == []
    assert record["errors"] == ["build_cache: retention policy build cache section is invalid"]


def test_a_missing_retention_module_prunes_nothing(tmp_path: Path):
    with pytest.raises(gc.GcError, match="retention module is unavailable"):
        gc.read_cap(POLICY, tmp_path / "retention.py")


def test_a_docker_failure_fails_the_round(tmp_path: Path, capsys):
    code, record, _ = _run(tmp_path, capsys, runner=_Runner(fail_prune=True))

    assert code == 1 and record["build_cache"] == {"status": "error"}
    assert record["errors"] == ["build_cache: command failed: docker buildx prune: unknown flag"]


def test_only_old_core_dumps_are_deleted(tmp_path: Path):
    directory = tmp_path / "coredump"
    directory.mkdir()
    for name, days in (("core.old", 8), ("core.recent", 6), ("notes.txt", 30)):
        (directory / name).write_bytes(b"x" * 3)
        _age(directory / name, days)
    (directory / "core.dir").mkdir()
    _age(directory / "core.dir", 30)

    result = gc.expire_coredumps(directory, NOW, apply=True)

    assert sorted(path.name for path in directory.iterdir()) == ["core.dir", "core.recent", "notes.txt"]
    assert result == {
        "status": "applied",
        "deleted": [{"name": "core.old", "bytes": 3, "mtime_utc": "2026-09-20T12:00:00Z"}],
        "kept": 1,
        "ignored": 2,
    }


def test_dry_run_prunes_and_deletes_nothing(tmp_path: Path, capsys):
    (tmp_path / "coredump").mkdir()
    old = tmp_path / "coredump" / "core.old"
    old.write_bytes(b"x")
    _age(old, 8)

    code, record, runner = _run(tmp_path, capsys, "--dry-run")

    assert code == 0 and old.exists()
    assert runner.calls == [DU]
    assert record["mode"] == "dry-run"
    assert record["build_cache"] == {"status": "dry-run", "cap": "20GB", "total_before": "21.5GB"}
    assert [item["name"] for item in record["coredumps"]["deleted"]] == ["core.old"]


def test_an_absent_coredump_directory_is_not_an_error(tmp_path: Path):
    assert gc.expire_coredumps(tmp_path / "missing", NOW, apply=True)["status"] == "absent"


def test_lock_paths_match_both_projects():
    retention = _load("car_agent_retention_for_gc_test", RETENTION)
    assert gc.LOCKS["car-agent"] == retention.ROOT / "shared" / "locks" / "release.lock"
    lock_script = (ROOT / "deploy" / "cloud" / "transaction-lock.sh").read_text(encoding="utf-8")
    assert 'readonly TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"' in lock_script
    # drone-agent confirmed 2026-09-28: fcntl.flock(LOCK_EX | LOCK_NB) on this persistent file.
    assert gc.LOCKS["drone-agent"] == Path("/home/ubuntu/drone-agent/stack.lock")


def test_source_only_reads_and_prunes_the_build_cache():
    source = (HOST / "host_capacity_gc.py").read_text(encoding="utf-8")
    commands = re.findall(r'\["docker", "buildx", "(\w+)"', source)
    assert sorted(set(commands)) == ["du", "prune"]
    for forbidden in ('"history"', '"rm"', '"rmi"', '"image"', '"system"', '"volume"', '"--filter"', "rmtree"):
        assert forbidden not in source


def test_units_and_readme_describe_the_same_installation():
    service = (HOST / "host-capacity-gc.service").read_text(encoding="utf-8")
    timer = (HOST / "host-capacity-gc.timer").read_text(encoding="utf-8")
    journald = (HOST / "journald-host-capacity.conf").read_text(encoding="utf-8")
    readme = (HOST / "README.md").read_text(encoding="utf-8")

    assert "ExecStart=/usr/bin/python3 -I /usr/local/sbin/host-capacity-gc\n" in service
    assert "Type=oneshot\n" in service and "Requisite=docker.service\n" in service
    assert "OnCalendar=hourly\n" in timer and "Unit=host-capacity-gc.service\n" in timer
    assert "[Journal]\nSystemMaxUse=1G\n" in journald
    rows = dict(re.findall(r"^\| `deploy/host/([^`]+)` \| `([^`]+)` \|", readme, re.M))
    assert rows == {
        "host_capacity_gc.py": "/usr/local/sbin/host-capacity-gc",
        "host-capacity-gc.service": "/etc/systemd/system/host-capacity-gc.service",
        "host-capacity-gc.timer": "/etc/systemd/system/host-capacity-gc.timer",
        "journald-host-capacity.conf": "/etc/systemd/journald.conf.d/60-host-capacity.conf",
    }
    assert sorted(rows) == sorted(path.name for path in HOST.iterdir() if path.is_file() and path.name != "README.md")


@pytest.mark.skipif(sys.platform == "win32", reason="flock(2) and O_NOFOLLOW are Linux-only")
def test_probe_detects_a_real_flock_and_releases_it(tmp_path: Path):
    import fcntl

    lock = tmp_path / "stack.lock"
    lock.touch()
    with open(lock, "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert gc.probe_lock(lock) == "busy"
    assert gc.probe_lock(lock) == "free"
    with open(lock, "a") as holder:  # the probe released it
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert gc.probe_lock(tmp_path / "absent.lock") == "missing"
    assert not (tmp_path / "absent.lock").exists()
    (tmp_path / "link.lock").symlink_to(lock)
    assert gc.probe_lock(tmp_path / "link.lock") == "invalid"
    assert gc.probe_lock(tmp_path) == "invalid"

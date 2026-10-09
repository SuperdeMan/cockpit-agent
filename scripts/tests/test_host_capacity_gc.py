"""Host capacity GC: build cache reclaim and old core dumps (design 2026-09-28 §4.4–4.5, P2; §4.4 revised 2026-10-09)."""

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
GIB = 1024**3
RESERVED_BLOCKS = 5 * GIB  # root 保留块（f_bfree - f_bavail），本机实测约 4.9 GiB


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
        self.totals = ["34.9GB", "6.5GB"]
        self.fail_prune = fail_prune

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if list(argv) == DU:
            return f"ID\tRECLAIMABLE\tSIZE\tLAST ACCESSED\nShared:\t8GB\nTotal:\t{self.totals.pop(0)}\n"
        if list(argv[:3]) == ["docker", "buildx", "prune"]:
            if self.fail_prune:
                raise gc.GcError("command failed: docker buildx prune: unknown flag")
            return "ID\tRECLAIMABLE\tSIZE\tLAST ACCESSED\nTotal:\t28.4GB\n"
        raise AssertionError(argv)


class _Disk:
    """依次返回各次测量的可用 GiB；保留块固定为 RESERVED_BLOCKS。"""

    def __init__(self, *available_gib: float) -> None:
        self.readings = [int(value * GIB) for value in available_gib]
        self.paths: list[Path] = []

    def __call__(self, path: Path):
        self.paths.append(path)
        return self.readings.pop(0), RESERVED_BLOCKS


def _prune(min_free_gib: int = 60, reserved_gib: int = 10) -> list[str]:
    return [
        "docker", "buildx", "prune", "--builder", "default", "--force", "--all",
        "--min-free-space", str(min_free_gib * GIB + RESERVED_BLOCKS), "--reserved-space", str(reserved_gib * GIB),
    ]


def _run(tmp_path: Path, capsys, *args: str, locks=None, runner=None, disk=None, policy: Path = POLICY):
    states = locks or {}
    runner = runner or _Runner()
    code = gc.main(
        ["--policy", str(policy), "--retention-module", str(RETENTION),
         "--coredump-dir", str(tmp_path / "coredump"), "--state", str(tmp_path / "state" / "state.json"), *args],
        runner=runner,
        probe=lambda path: states.get(path, "free"),
        disk=disk or _Disk(50),
        now=NOW,
    )
    return code, json.loads(capsys.readouterr().out), runner


def _state(tmp_path: Path) -> Path:
    return tmp_path / "state" / "state.json"


def _age(path: Path, days: float) -> None:
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_thresholds_are_read_from_the_approved_policy_through_retention():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert gc.read_thresholds(POLICY, RETENTION) == (
        policy["capacity"]["target_free_gib"],
        policy["build_cache"]["prune_to_free_gib"],
        policy["build_cache"]["reserved_space_gib"],
    )


def test_no_prune_while_available_is_at_or_above_the_trigger(tmp_path: Path, capsys):
    disk = _Disk(45)
    code, record, runner = _run(tmp_path, capsys, disk=disk)

    assert code == 0 and record["errors"] == [] and record["warnings"] == []
    assert record["locks"] == {"car-agent": "free", "drone-agent": "free"}
    assert runner.calls == [DU]  # 只读，不调用 prune（prune 一删记录就会解绑两个项目的缓存键）
    assert disk.paths == [gc.BUILDKIT_ROOT]
    assert record["build_cache"] == {
        "status": "not_needed", "available_before": 45 * GIB, "total_before": "34.9GB",
        "trigger_free_gib": 45, "prune_to_free_gib": 60, "reserved_space_gib": 10,
    }


def test_below_the_trigger_prunes_to_the_target_in_bfree_terms(tmp_path: Path, capsys):
    code, record, runner = _run(tmp_path, capsys, disk=_Disk(36.5, 61))

    assert code == 0 and record["errors"] == [] and record["warnings"] == []
    # BuildKit 的 --min-free-space 比的是 Bfree：目标线 60 GiB（Bavail）要加上保留块；两个参数都按字节传
    assert runner.calls == [DU, _prune(), DU]
    assert record["build_cache"] == {
        "status": "pruned", "available_before": int(36.5 * GIB), "available_after": 61 * GIB,
        "min_free_space": 60 * GIB + RESERVED_BLOCKS, "total_before": "34.9GB", "reclaimed": "28.4GB",
        "total_after": "6.5GB", "trigger_free_gib": 45, "prune_to_free_gib": 60, "reserved_space_gib": 10,
    }
    assert not _state(tmp_path).exists()


def test_a_prune_that_falls_short_backs_off_until_available_drops_another_step(tmp_path: Path, capsys):
    code, record, _ = _run(tmp_path, capsys, disk=_Disk(40, 42))
    assert code == 0 and record["build_cache"]["status"] == "pruned"
    assert record["build_cache"]["backoff"] == "set"
    assert json.loads(_state(tmp_path).read_text(encoding="utf-8")) == {
        "available_after": 42 * GIB, "pruned_utc": "2026-09-28T12:00:00Z",
    }
    assert len(record["warnings"]) == 1 and "fell short" in record["warnings"][0]

    # 仍低于触发线，但还没比上次回收后再降 5 GiB：不 prune，退避并留 warning，unit 不算失败
    code, record, runner = _run(tmp_path, capsys, disk=_Disk(37))
    assert code == 0 and runner.calls == [DU]
    assert record["build_cache"]["status"] == "deferred"
    assert record["build_cache"]["retry_below"] == 37 * GIB
    assert len(record["warnings"]) == 1 and "deferred" in record["warnings"][0]

    # 再降一档才重试
    code, record, runner = _run(tmp_path, capsys, disk=_Disk(36.9, 41))
    assert code == 0 and runner.calls == [DU, _prune(), DU]
    assert record["build_cache"]["status"] == "pruned"


def test_back_above_the_trigger_clears_the_backoff(tmp_path: Path, capsys):
    _state(tmp_path).parent.mkdir()
    _state(tmp_path).write_text(json.dumps({"available_after": 42 * GIB}), encoding="utf-8")

    code, record, runner = _run(tmp_path, capsys, disk=_Disk(46))

    assert code == 0 and runner.calls == [DU]
    assert record["build_cache"]["status"] == "not_needed" and record["build_cache"]["backoff"] == "cleared"
    assert not _state(tmp_path).exists()


def test_a_corrupt_backoff_state_prunes_nothing(tmp_path: Path, capsys):
    _state(tmp_path).parent.mkdir()
    _state(tmp_path).write_text("{", encoding="utf-8")

    code, record, runner = _run(tmp_path, capsys, disk=_Disk(40))

    assert code == 1 and runner.calls == [DU]
    assert record["build_cache"] == {"status": "error"}
    assert record["errors"] == ["build_cache: backoff state is unreadable"]


@pytest.mark.parametrize("holder", ["car-agent", "drone-agent"])
def test_a_held_lock_skips_a_round_that_would_prune(tmp_path: Path, capsys, holder: str):
    code, record, runner = _run(tmp_path, capsys, locks={gc.LOCKS[holder]: "busy"}, disk=_Disk(40))

    assert code == 0 and runner.calls == [DU]
    assert record["build_cache"]["status"] == "skipped" and record["build_cache"]["busy"] == [holder]


@pytest.mark.parametrize("state", ["missing", "invalid"])
def test_an_unusable_lock_file_fails_the_round(tmp_path: Path, capsys, state: str):
    code, record, runner = _run(tmp_path, capsys, locks={gc.LOCKS["drone-agent"]: state})

    assert code == 1 and runner.calls == []
    assert record["build_cache"] == {"status": "error"}
    assert record["errors"] == ["build_cache: lock file missing or invalid: drone-agent"]


def test_an_invalid_policy_prunes_nothing(tmp_path: Path, capsys):
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["build_cache"]["prune_to_free_gib"] = 50  # 与触发线只差 5 GiB：回收后的第一次冷构建就会连环触发
    broken = tmp_path / "retention-policy.json"
    broken.write_text(json.dumps(policy), encoding="utf-8")

    code, record, runner = _run(tmp_path, capsys, policy=broken, disk=_Disk(40))

    assert code == 1 and runner.calls == []
    assert record["errors"] == ["build_cache: retention policy build cache target leaves too little headroom"]


def test_a_missing_retention_module_prunes_nothing(tmp_path: Path):
    with pytest.raises(gc.GcError, match="retention module is unavailable"):
        gc.read_thresholds(POLICY, tmp_path / "retention.py")


def test_a_host_policy_older_than_this_script_prunes_nothing(tmp_path: Path):
    # 锚还没换：主机上的旧 retention.py 接受旧策略，但旧策略里没有本脚本要的键
    old_module = tmp_path / "retention.py"
    old_module.write_text(
        "class RetentionError(Exception):\n    pass\n\n"
        "def load_policy(path):\n    return {'capacity': {'target_free_gib': 45}, 'build_cache': {'max_used_space': '20GB'}}\n",
        encoding="utf-8",
    )
    with pytest.raises(gc.GcError, match="lacks the build cache thresholds"):
        gc.read_thresholds(POLICY, old_module)


def test_a_docker_failure_fails_the_round(tmp_path: Path, capsys):
    code, record, _ = _run(tmp_path, capsys, runner=_Runner(fail_prune=True), disk=_Disk(40))

    assert code == 1 and record["build_cache"] == {"status": "error"}
    assert record["errors"] == ["build_cache: command failed: docker buildx prune: unknown flag"]
    assert not _state(tmp_path).exists()


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


def test_dry_run_prunes_deletes_and_records_nothing(tmp_path: Path, capsys):
    (tmp_path / "coredump").mkdir()
    old = tmp_path / "coredump" / "core.old"
    old.write_bytes(b"x")
    _age(old, 8)

    code, record, runner = _run(tmp_path, capsys, "--dry-run", disk=_Disk(40))

    assert code == 0 and old.exists()
    assert runner.calls == [DU]
    assert record["mode"] == "dry-run"
    assert record["build_cache"]["status"] == "dry-run"
    assert record["build_cache"]["min_free_space"] == 60 * GIB + RESERVED_BLOCKS
    assert not _state(tmp_path).exists()
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
    # 退避状态目录由 systemd 建立，与脚本里的 STATE 是同一处
    assert f"StateDirectory={gc.STATE.parent.name}\n" in service and gc.STATE.parent.parent == Path("/var/lib")
    assert "StateDirectoryMode=0700\n" in service
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

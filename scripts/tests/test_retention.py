"""deploy/cloud/retention.py: the only deletion point for release artifacts and backups.

Design: docs/design/2026-09-28-cloud-host-capacity-governance.md §4.2–4.3.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "deploy" / "cloud" / "retention-policy.json"


def _load():
    spec = importlib.util.spec_from_file_location("car_agent_retention", ROOT / "deploy" / "cloud" / "retention.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ret = _load()
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
C, R2, R3, R4, R5 = ("c" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)


def _policy(**releases) -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["releases"].update(releases)
    return ret.validate_policy(policy)


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime(ret.TIMESTAMP)


def _facts(**overrides) -> "ret.ReleaseFacts":
    base = dict(
        now=NOW,
        current=C,
        release_dirs={C: NOW, R2: NOW - timedelta(days=3), R3: NOW - timedelta(days=2), R4: NOW - timedelta(days=1)},
        staging_dirs={},
        build_dirs={},
        upload_dirs={},
        states={
            R2: ((_ts(72), "VERIFIED"),),
            R3: ((_ts(48), "VERIFIED"),),
            R4: ((_ts(24), "VERIFIED"),),
            C: ((_ts(1), "VERIFIED"),),
        },
        tag_ids={
            f"car-agent-release/hmi:{sha}": f"sha256:{sha[:4]}" for sha in (C, R2, R3, R4)
        },
        used_image_ids=frozenset({f"sha256:{C[:4]}"}),
        compose_dirs=frozenset(),
    )
    base.update(overrides)
    return ret.ReleaseFacts(**base)


# ------------------------------------------------------------------ policy


def test_repository_policy_is_valid_and_is_the_capacity_source():
    policy = ret.load_policy(POLICY_PATH)
    from scripts import cloud_capacity

    assert policy["releases"]["keep_activated"] == 3
    assert cloud_capacity.CAPACITY_WARN_FREE_BYTES == policy["capacity"]["warn_free_gib"] * 1024**3


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["releases"].update(keep_activated=1),  # would leave no rollback target
        lambda p: p["releases"].update(keep_activated=True),
        lambda p: p["releases"].update(pinned=["not-a-sha"]),
        lambda p: p["backups"].update(min_complete_sets=0),
        lambda p: p["capacity"].update(target_free_gib=10),
        lambda p: p["build_cache"].update(max_used_space="20 gigs"),
        lambda p: p.update(extra=1),
    ],
)
def test_invalid_policy_is_rejected(mutate):
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    mutate(policy)
    with pytest.raises(ret.RetentionError):
        ret.validate_policy(policy)


# ------------------------------------------------------------------ release plan


def test_keeps_current_plus_the_most_recently_activated_and_retires_the_rest():
    plan = ret.plan_releases(_facts(), _policy())

    assert {item["sha"] for item in plan["keep"]} == {C, R4, R3}
    (retired,) = plan["retire"]
    assert retired["sha"] == R2 and retired["reason"] == "outside-retention-window"
    assert retired["tags"] == [f"car-agent-release/hmi:{R2}"]
    assert retired["release_dir"] == R2


def test_rollback_keeps_the_newer_releases_it_came_from():
    facts = _facts(
        current=R3,
        release_dirs={R2: NOW, R3: NOW, R4: NOW, R5: NOW},
        states={
            R2: ((_ts(72), "VERIFIED"),),
            R4: ((_ts(24), "VERIFIED"),),
            R5: ((_ts(2), "VERIFIED"),),
            R3: ((_ts(48), "VERIFIED"), (_ts(1), "ROLLED_BACK")),
        },
        tag_ids={},
        used_image_ids=frozenset(),
    )
    plan = ret.plan_releases(facts, _policy())

    assert {item["sha"] for item in plan["keep"]} == {R3, R5, R4}
    assert [item["sha"] for item in plan["retire"]] == [R2]


def test_images_in_use_pins_and_compose_directories_are_never_retired():
    facts = _facts(
        used_image_ids=frozenset({f"sha256:{C[:4]}", f"sha256:{R2[:4]}"}),
        release_dirs={C: NOW, R2: NOW, R3: NOW, R4: NOW, "4c1f479": NOW},
        compose_dirs=frozenset({"4c1f479"}),
    )
    plan = ret.plan_releases(facts, _policy())
    keep = {item["sha"]: item["reasons"] for item in plan["keep"]}

    assert "image-in-use" in keep[R2]
    assert keep["4c1f479"] == ["compose-working-dir"]
    assert plan["retire"] == []

    pinned = ret.plan_releases(_facts(), _policy(pinned=[R2[:7]]))
    assert "pinned" in {item["sha"]: item["reasons"] for item in pinned["keep"]}[R2]


def test_failed_attempts_wait_out_their_ttl_and_unknown_releases_are_only_reported():
    old, fresh, legacy = "a" * 40, "b" * 40, "d" * 40
    facts = _facts(
        release_dirs={C: NOW, old: NOW - timedelta(hours=100), legacy: NOW - timedelta(days=40)},
        build_dirs={old: NOW - timedelta(hours=100), fresh: NOW - timedelta(hours=10)},
        states={C: ((_ts(1), "VERIFIED"),), old: ((_ts(100), "VERIFY_FAILED_ROLLED_BACK"),)},
        tag_ids={},
    )
    plan = ret.plan_releases(facts, _policy())

    assert [(item["sha"], item["reason"]) for item in plan["retire"]] == [(old, "failed-past-ttl")]
    assert plan["retire"][0]["build_dir"] == old
    assert sorted((item["sha"], item["reason"]) for item in plan["report"]) == [
        (fresh, "failed-within-ttl"),
        (legacy, "no-activation-evidence"),
    ]


def test_uploads_that_were_never_built_expire_like_failed_attempts():
    # 2026-09-28: each infrastructure approval left an upload directory that was only ever reported.
    stale, fresh = "a" * 40, "b" * 40
    stale_upload, fresh_upload = f"{stale}-{'1' * 32}", f"{fresh}-{'2' * 32}"
    facts = _facts(
        release_dirs={C: NOW},
        upload_dirs={stale_upload: NOW - timedelta(hours=73), fresh_upload: NOW - timedelta(hours=17)},
        states={C: ((_ts(1), "VERIFIED"),)},
        tag_ids={},
    )
    plan = ret.plan_releases(facts, _policy())

    assert [(item["sha"], item["reason"], item["upload_dirs"]) for item in plan["retire"]] == [
        (stale, "failed-past-ttl", [stale_upload]),
    ]
    assert plan["retire"][0]["release_dir"] is None and plan["retire"][0]["build_dir"] is None
    assert [(item["sha"], item["reason"]) for item in plan["report"]] == [(fresh, "failed-within-ttl")]


def test_activated_releases_drop_their_build_workspace_upload_and_matching_alias_only():
    upload = f"{C}-{'e' * 32}"
    facts = _facts(
        build_dirs={C: NOW},
        upload_dirs={upload: NOW},
        staging_dirs={f".staging-{C}-123": NOW},
        tag_ids={
            f"car-agent-release/hmi:{C}": "sha256:same",
            f"car-agent-release-{C}-hmi:latest": "sha256:same",
            f"car-agent-release-{C}-dashboard:latest": "sha256:orphan",  # its only tag: keep
        },
        used_image_ids=frozenset({"sha256:same"}),
    )
    plan = ret.plan_releases(facts, _policy())

    (entry,) = plan["finalize"]
    assert entry == {
        "sha": C,
        "alias_tags": [f"car-agent-release-{C}-hmi:latest"],
        "build_dir": C,
        "upload_dirs": [upload],
        "staging_dirs": [f".staging-{C}-123"],
    }


# ------------------------------------------------------------------ execution


class FakeDocker:
    def __init__(self, root: Path, tags: dict[str, str], used: set[str], compose_dirs: set[str] = frozenset()):
        self.root, self.tags, self.used, self.compose_dirs = root, dict(tags), set(used), set(compose_dirs)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(tuple(argv))
        if argv[:3] == ["docker", "image", "ls"]:
            return "\n".join(f"{ref} {iid}" for ref, iid in self.tags.items())
        if argv[:3] == ["docker", "ps", "-aq"]:
            return "container-1\n"
        if argv[:2] == ["docker", "inspect"]:
            rows = [f"{iid}|" for iid in self.used]
            rows += [f"sha256:none|{self.root / 'releases'}/{name}" for name in self.compose_dirs]
            return "\n".join(rows)
        if argv[:3] == ["docker", "image", "rm"]:
            failed = False
            for ref in argv[3:]:
                if self.tags.get(ref) in self.used:
                    failed = True
                else:
                    self.tags.pop(ref, None)
            if failed:
                raise ret.RetentionError("conflict")
            return ""
        raise AssertionError(f"unexpected command {argv}")


class ChurningDocker(FakeDocker):
    """A co-tenant removes a container between `docker ps` and `docker inspect`."""

    def __init__(self, *args, failures: int, vanished: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.failures = failures
        self.vanished = vanished
        self.listing = ["container-1", "gone-soon"]

    def __call__(self, argv):
        argv = list(argv)
        if argv[:3] == ["docker", "ps", "-aq"]:
            self.calls.append(tuple(argv))
            return "\n".join(self.listing) + "\n"
        if argv[:2] == ["docker", "inspect"]:
            self.calls.append(tuple(argv))
            if "gone-soon" in argv[4:] and self.failures > 0:
                self.failures -= 1
                if self.vanished and self.failures == 0:
                    self.listing = ["container-1"]
                raise ret.RetentionError("No such object: gone-soon")
            return "\n".join(f"{iid}|" for iid in self.used)
        return super().__call__(argv)


def test_container_facts_survive_a_container_vanishing_between_ps_and_inspect(tmp_path: Path):
    docker = ChurningDocker(tmp_path, {}, {"sha256:cur"}, failures=1)
    used, _ = ret._container_facts(docker, tmp_path)
    assert used == {"sha256:cur"}


def test_container_facts_fall_back_to_one_by_one_and_skip_only_vanished_containers(tmp_path: Path):
    docker = ChurningDocker(tmp_path, {}, {"sha256:cur"}, failures=ret.INSPECT_ATTEMPTS + 1)
    used, _ = ret._container_facts(docker, tmp_path)
    assert used == {"sha256:cur"}

    stuck = ChurningDocker(tmp_path, {}, {"sha256:cur"}, failures=99, vanished=False)
    with pytest.raises(ret.RetentionError):
        ret._container_facts(stuck, tmp_path)


def _tree(root: Path, *dirs: str) -> None:
    for relative in dirs:
        (root / relative).mkdir(parents=True, exist_ok=True)


def test_apply_removes_only_planned_objects_and_keeps_build_evidence(tmp_path: Path):
    _tree(tmp_path, f"releases/{C}", f"releases/{R2}", f"builds/{R2}/src", f"builds/{R2}/upload",
          f"incoming/releases/{R2}-{'f' * 32}", f"shared/evidence/releases/{R2}")
    (tmp_path / f"builds/{R2}/image-inventory.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"builds/{R2}/resume-hmi.log").write_text("log", encoding="utf-8")
    (tmp_path / f"builds/{R2}/transport.tar").write_bytes(b"x" * 10)
    (tmp_path / f"builds/{R2}/upload/manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"builds/{R2}/upload/source.tar").write_bytes(b"y" * 10)
    docker = FakeDocker(tmp_path, {f"car-agent-release/hmi:{R2}": "sha256:old",
                                   f"car-agent-release/hmi:{C}": "sha256:cur"}, {"sha256:cur"})
    plan = {
        "current": C,
        "retire": [{
            "sha": R2, "reason": "outside-retention-window", "tags": [f"car-agent-release/hmi:{R2}"],
            "release_dir": R2, "build_dir": R2, "upload_dirs": [f"{R2}-{'f' * 32}"], "staging_dirs": [],
        }],
        "finalize": [],
    }

    result = ret.apply_release_plan(plan, tmp_path, docker)

    assert result["removed_tags"] == [f"car-agent-release/hmi:{R2}"]
    assert f"car-agent-release/hmi:{C}" in docker.tags
    assert not (tmp_path / "releases" / R2).exists() and (tmp_path / "releases" / C).exists()
    assert not (tmp_path / "builds" / R2).exists()
    assert not (tmp_path / "incoming" / "releases" / f"{R2}-{'f' * 32}").exists()
    kept = tmp_path / "shared" / "evidence" / "releases" / R2 / "build"
    assert sorted(p.relative_to(kept).as_posix() for p in kept.rglob("*") if p.is_file()) == [
        "image-inventory.json", "resume-hmi.log", "upload/manifest.json",
    ]
    assert result["skipped"] == []


def test_apply_revalidates_current_in_use_and_compose_directories(tmp_path: Path):
    _tree(tmp_path, f"releases/{C}", f"releases/{R2}", "releases/4c1f479")
    docker = FakeDocker(
        tmp_path,
        {f"car-agent-release/hmi:{R2}": "sha256:now-used"},
        {"sha256:now-used"},
        compose_dirs={"4c1f479"},
    )
    plan = {
        "current": C,
        "retire": [
            {"sha": R2, "reason": "x", "tags": [f"car-agent-release/hmi:{R2}"], "release_dir": R2,
             "build_dir": None, "upload_dirs": [], "staging_dirs": []},
            # a tampered plan must still not touch the current release or a compose working dir
            {"sha": C, "reason": "x", "tags": [], "release_dir": C, "build_dir": None,
             "upload_dirs": [], "staging_dirs": []},
            {"sha": "4c1f479", "reason": "x", "tags": [], "release_dir": "4c1f479", "build_dir": None,
             "upload_dirs": [], "staging_dirs": []},
            {"sha": "x", "reason": "x", "tags": [], "release_dir": "../shared", "build_dir": None,
             "upload_dirs": [], "staging_dirs": []},
        ],
        "finalize": [],
    }

    result = ret.apply_release_plan(plan, tmp_path, docker)

    assert result["removed_tags"] == []
    assert [f"car-agent-release/hmi:{R2}", "image in use"] in result["skipped"]
    assert (tmp_path / "releases" / C).exists()
    assert (tmp_path / "releases" / "4c1f479").exists()
    assert not (tmp_path / "releases" / R2).exists()
    reasons = {Path(item[0]).name: item[1] for item in result["skipped"]}
    assert reasons[C] == "current release"
    assert reasons["4c1f479"] == "compose working directory of a container"
    assert reasons["shared"] == "name does not match"


def test_symlinked_directories_are_never_followed(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    _tree(tmp_path, f"releases/{C}")
    try:
        os.symlink(outside, tmp_path / "releases" / R2, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available on this host")
    plan = {"current": C, "retire": [{"sha": R2, "reason": "x", "tags": [], "release_dir": R2,
                                      "build_dir": None, "upload_dirs": [], "staging_dirs": []}],
            "finalize": []}

    result = ret.apply_release_plan(plan, tmp_path, FakeDocker(tmp_path, {}, set()))

    assert (outside / "keep.txt").exists()
    assert result["removed_dirs"] == []


# ------------------------------------------------------------------ backups


def _backup_facts(set_hours: list[float], *, incomplete: tuple[float, ...] = (), partial=()) -> "ret.BackupFacts":
    files = []
    for hours in set_hours:
        ts = _ts(hours)
        for kind, ext in ret.BACKUP_KINDS.items():
            if hours in incomplete and kind == "observability":
                continue
            files.append((kind, f"{ts}.{ext}", NOW - timedelta(hours=hours)))
    for kind, name, hours in partial:
        files.append((kind, name, NOW - timedelta(hours=hours)))
    manifests = tuple(f"{_ts(h)}.backup-manifest.json" for h in set_hours)
    return ret.BackupFacts(now=NOW, files=tuple(files), manifests=manifests)


def test_backup_gfs_keeps_recent_daily_and_weekly_sets():
    hours = [h for h in range(1, 24 * 70, 6)]  # a set every 6 h for ~70 days
    plan = ret.plan_backups(_backup_facts(hours), _policy())
    keep = {datetime.strptime(ts, ret.TIMESTAMP).replace(tzinfo=timezone.utc) for ts in plan["keep_sets"]}

    recent = {h for h in hours if h <= 48}
    assert {_ts(h) for h in recent} <= set(plan["keep_sets"])
    days = {moment.date() for moment in keep if NOW - moment > timedelta(hours=48)}
    assert all(sum(1 for m in keep if m.date() == day) == 1 for day in days if (NOW.date() - day).days > 2)
    assert max(NOW - moment for moment in keep) <= timedelta(weeks=8, days=1)
    deleted = {name.split(".")[0] for _, name in plan["delete_files"]}
    assert not deleted & set(plan["keep_sets"])
    assert len(plan["keep_sets"]) < len(hours)


def test_backup_rotation_never_drops_below_the_minimum_or_the_newest_set():
    plan = ret.plan_backups(_backup_facts([500, 900, 1300], incomplete=(500,)), _policy())

    # the newest set is kept although incomplete; the minimum counts complete sets only
    assert set(plan["keep_sets"]) == {_ts(500), _ts(900), _ts(1300)}
    assert plan["delete_files"] == []


def test_stale_partials_are_removed_and_unknown_files_are_only_reported():
    facts = _backup_facts(
        [1],
        partial=(("postgres", f"{_ts(30)}.dump.partial", 30), ("redis", f"{_ts(2)}.rdb.partial", 2),
                 ("redis", "notes.txt", 400)),
    )
    plan = ret.plan_backups(facts, _policy())

    assert plan["delete_files"] == [["postgres", f"{_ts(30)}.dump.partial"]]
    assert plan["unknown_files"] == ["redis/notes.txt"]


def test_apply_backup_plan_deletes_only_valid_names_under_their_roots(tmp_path: Path):
    base = tmp_path / "shared" / "backups"
    for kind in ret.BACKUP_KINDS:
        (base / kind).mkdir(parents=True)
    old = _ts(900)
    (base / "postgres" / f"{old}.dump").write_bytes(b"1")
    (base / f"{old}.backup-manifest.json").write_text("{}", encoding="utf-8")
    (base / "postgres" / "keep.me").write_bytes(b"1")
    plan = {"delete_files": [["postgres", f"{old}.dump"], ["manifest", f"{old}.backup-manifest.json"],
                             ["postgres", "keep.me"], ["redis", f"{old}.dump"]]}

    result = ret.apply_backup_plan(plan, tmp_path)

    assert not (base / "postgres" / f"{old}.dump").exists()
    assert not (base / f"{old}.backup-manifest.json").exists()
    assert (base / "postgres" / "keep.me").exists()
    assert len(result["removed_files"]) == 2 and len(result["skipped"]) == 2


# ------------------------------------------------------------------ entry point


def test_lock_descriptor_must_be_the_release_lock(tmp_path: Path):
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("needs /proc (Linux host)")
    lock = tmp_path / "shared" / "locks" / "release.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("", encoding="utf-8")
    other = tmp_path / "other"
    other.write_text("", encoding="utf-8")
    with open(lock, "r") as good, open(other, "r") as bad:
        ret.validate_lock_fd(tmp_path, good.fileno())
        with pytest.raises(ret.RetentionError):
            ret.validate_lock_fd(tmp_path, bad.fileno())
    with pytest.raises(ret.RetentionError):
        ret.validate_lock_fd(tmp_path, 987654)


def test_main_refuses_to_run_without_the_transaction_lock(tmp_path: Path, capsys):
    code = ret.main(["releases", "--mode", "apply", "--lock-fd", "987654", "--root", str(tmp_path)],
                    runner=FakeDocker(tmp_path, {}, set()))
    record = json.loads(capsys.readouterr().out)

    assert code == 2 and record["status"] == "error"
    assert not (tmp_path / "shared" / "evidence").exists()


def test_main_backups_apply_writes_evidence(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(ret, "validate_lock_fd", lambda root, fd: None)
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "retention-policy.json").write_text(POLICY_PATH.read_text(encoding="utf-8"),
                                                              encoding="utf-8")
    for kind in ret.BACKUP_KINDS:
        (tmp_path / "shared" / "backups" / kind).mkdir(parents=True)

    code = ret.main(["backups", "--mode", "apply", "--reason", "backup", "--lock-fd", "3", "--root", str(tmp_path)])
    record = json.loads(capsys.readouterr().out)

    assert code == 0 and record["status"] == "applied"
    evidence = Path(record["evidence"])
    assert evidence.parent == tmp_path / "shared" / "evidence" / "retention"
    assert json.loads(evidence.read_text(encoding="utf-8"))["kind"] == "backups"

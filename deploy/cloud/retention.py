#!/usr/bin/env python3
"""Policy-driven retention for release artifacts and backups on the cloud host.

Design: docs/design/2026-09-28-cloud-host-capacity-governance.md §4.2–4.3.
Policy (single source): /opt/car-agent/shared/retention-policy.json, installed from
deploy/cloud/retention-policy.json under the infrastructure approval anchor.

This module is the only place on the host where release artifacts and backups are
deleted. Callers (remote-release.sh, backup.sh) already hold the transaction lock and
prove it with --lock-fd. Every object is re-validated against a fixed root, a fixed
name pattern and "not a symlink" right before removal; images are removed by tag,
never with -f, and never while a container uses them; every applied run writes an
evidence record. Planning is pure so it can be tested without a host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/opt/car-agent")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SELECTOR = re.compile(r"^[0-9a-f]{7,40}$")
STATE_FILE = re.compile(r"^state-(\d{8}T\d{6}Z)-([A-Z_]+)\.json$")
UPLOAD_DIR = re.compile(r"^([0-9a-f]{40})-[0-9a-f]{32}$")
STAGING_DIR = re.compile(r"^\.staging-([0-9a-f]{40})-\d+$")
RELEASE_TAG = re.compile(r"^car-agent-release/([a-z0-9][a-z0-9-]*):([0-9a-f]{7,40})$")
ALIAS_TAG = re.compile(r"^car-agent-release-([0-9a-f]{40})-([a-z0-9][a-z0-9-]*):latest$")
BACKUP_FILE = re.compile(r"^(\d{8}T\d{6}Z)\.(dump|rdb|sql\.gz)(\.partial)?$")
MANIFEST_FILE = re.compile(r"^(\d{8}T\d{6}Z)\.backup-manifest\.json$")
TIMESTAMP = "%Y%m%dT%H%M%SZ"
ACTIVATED = frozenset({"VERIFIED", "ROLLED_BACK"})
FAILED = frozenset({"VERIFY_FAILED_ROLLED_BACK", "ROLLBACK_FAILED"})
#: Build workspace files kept as evidence when the workspace is dropped: every small top-level
#: file (image inventories, resume/diagnostic logs) plus the upload manifest and checksums.
#: Only the bulky source copies (src/, transport.tar, upload/source.tar) are discarded.
BUILD_EVIDENCE_EXTRA = ("upload/manifest.json", "upload/checksums.sha256")
BUILD_EVIDENCE_MAX_BYTES = 4 * 1024 * 1024
BUILD_BULK = frozenset({"transport.tar"})
BACKUP_KINDS = {"postgres": "dump", "redis": "rdb", "observability": "sql.gz"}
PARTIAL_TTL = timedelta(hours=24)
TAG_BATCH = 40

Runner = Callable[[Sequence[str]], str]


class RetentionError(RuntimeError):
    """A condition under which retention must not delete anything."""


# ---------------------------------------------------------------- policy


def _int(value: object, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise RetentionError("retention policy has an invalid number")
    return value


def validate_policy(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "releases", "backups", "build_cache", "capacity",
    } or payload["schema_version"] != 1:
        raise RetentionError("retention policy has an invalid shape")
    releases, backups = payload["releases"], payload["backups"]
    cache, capacity = payload["build_cache"], payload["capacity"]
    if not isinstance(releases, dict) or set(releases) != {"keep_activated", "pinned", "failed_ttl_hours"}:
        raise RetentionError("retention policy releases section is invalid")
    _int(releases["keep_activated"], 2)  # always keep at least one rollback target
    _int(releases["failed_ttl_hours"], 1)
    pinned = releases["pinned"]
    if not isinstance(pinned, list) or any(not isinstance(p, str) or not SELECTOR.fullmatch(p) for p in pinned):
        raise RetentionError("retention policy pinned releases are invalid")
    if not isinstance(backups, dict) or set(backups) != {
        "keep_all_hours", "daily_days", "weekly_weeks", "min_complete_sets",
    }:
        raise RetentionError("retention policy backups section is invalid")
    _int(backups["keep_all_hours"], 1)
    _int(backups["daily_days"], 0)
    _int(backups["weekly_weeks"], 0)
    _int(backups["min_complete_sets"], 1)
    if (
        not isinstance(cache, dict)
        or set(cache) != {"max_used_space"}
        or not isinstance(cache["max_used_space"], str)
        or not re.fullmatch(r"[1-9][0-9]*(MB|GB)", cache["max_used_space"])
    ):
        raise RetentionError("retention policy build cache section is invalid")
    if not isinstance(capacity, dict) or set(capacity) != {"warn_free_gib", "target_free_gib"}:
        raise RetentionError("retention policy capacity section is invalid")
    if _int(capacity["target_free_gib"], 1) < _int(capacity["warn_free_gib"], 1):
        raise RetentionError("retention policy capacity target is below the warning line")
    return payload


def load_policy(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetentionError("retention policy is unreadable") from exc
    return validate_policy(payload)


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.lstat().st_mtime, tz=timezone.utc)


def _real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


# ---------------------------------------------------------------- releases


@dataclass(frozen=True)
class ReleaseFacts:
    now: datetime
    current: str
    release_dirs: Mapping[str, datetime]
    staging_dirs: Mapping[str, datetime]
    build_dirs: Mapping[str, datetime]
    upload_dirs: Mapping[str, datetime]
    states: Mapping[str, tuple[tuple[str, str], ...]]
    tag_ids: Mapping[str, str]
    used_image_ids: frozenset[str]
    compose_dirs: frozenset[str]


def _release_of_tag(ref: str) -> str | None:
    match = RELEASE_TAG.fullmatch(ref) or ALIAS_TAG.fullmatch(ref)
    if match is None:
        return None
    return match.group(2) if match.re is RELEASE_TAG else match.group(1)


def _image_tags(runner: Runner) -> dict[str, str]:
    tags: dict[str, str] = {}
    out = runner(["docker", "image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"])
    for line in out.splitlines():
        ref, _, image_id = line.strip().partition(" ")
        if _release_of_tag(ref) is not None and image_id:
            tags[ref] = image_id
    return tags


CONTAINER_TEMPLATE = '{{.Image}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
INSPECT_ATTEMPTS = 3


def _inspect_containers(runner: Runner) -> str:
    """`docker inspect` over every container, tolerating co-tenant churn.

    On the shared host another project creates and removes containers at any time; a
    container that vanishes between `ps` and `inspect` fails the whole batch (2026-09-28).
    Retry with a fresh listing, then fall back to one-by-one and skip only containers
    that are really gone: a vanished container cannot hold an image or a directory.
    """
    ids: list[str] = []
    for _ in range(INSPECT_ATTEMPTS):
        ids = runner(["docker", "ps", "-aq", "--no-trunc"]).split()
        if not ids:
            return ""
        try:
            return runner(["docker", "inspect", "--format", CONTAINER_TEMPLATE, *ids])
        except RetentionError:
            continue
    lines = []
    for container in ids:
        try:
            lines.append(runner(["docker", "inspect", "--format", CONTAINER_TEMPLATE, container]))
        except RetentionError:
            if container in runner(["docker", "ps", "-aq", "--no-trunc"]).split():
                raise
    return "\n".join(lines)


def _container_facts(runner: Runner, root: Path) -> tuple[frozenset[str], frozenset[str]]:
    used: set[str] = set()
    compose_dirs: set[str] = set()
    prefix = str(root / "releases") + "/"
    for line in _inspect_containers(runner).splitlines():
        image_id, _, working_dir = line.strip().partition("|")
        if image_id:
            used.add(image_id)
        if working_dir.startswith(prefix):
            name = working_dir[len(prefix):]
            if SELECTOR.fullmatch(name):
                compose_dirs.add(name)
    return frozenset(used), frozenset(compose_dirs)


def gather_release_facts(root: Path, runner: Runner, now: datetime) -> ReleaseFacts:
    current_path = os.path.realpath(root / "current")
    releases_root = os.path.realpath(root / "releases") + os.sep
    match = re.fullmatch(re.escape(releases_root) + r"([0-9a-f]{7,40})", current_path)
    if match is None:
        raise RetentionError("current release path is invalid")
    release_dirs: dict[str, datetime] = {}
    staging_dirs: dict[str, datetime] = {}
    for entry in (root / "releases").iterdir():
        if SELECTOR.fullmatch(entry.name) and _real_dir(entry):
            release_dirs[entry.name] = _mtime(entry)
        elif STAGING_DIR.fullmatch(entry.name) and _real_dir(entry):
            staging_dirs[entry.name] = _mtime(entry)
    build_dirs = {
        entry.name: _mtime(entry)
        for entry in (root / "builds").iterdir()
        if FULL_SHA.fullmatch(entry.name) and _real_dir(entry)
    } if (root / "builds").is_dir() else {}
    uploads = root / "incoming" / "releases"
    upload_dirs = {
        entry.name: _mtime(entry)
        for entry in uploads.iterdir()
        if UPLOAD_DIR.fullmatch(entry.name) and _real_dir(entry)
    } if uploads.is_dir() else {}
    states: dict[str, tuple[tuple[str, str], ...]] = {}
    evidence = root / "shared" / "evidence" / "releases"
    if evidence.is_dir():
        for entry in evidence.iterdir():
            if not (SELECTOR.fullmatch(entry.name) and _real_dir(entry)):
                continue
            rows = []
            for item in entry.iterdir():
                state = STATE_FILE.fullmatch(item.name)
                if state:
                    rows.append((state.group(1), state.group(2)))
            if rows:
                states[entry.name] = tuple(sorted(rows))
    used, compose_dirs = _container_facts(runner, root)
    return ReleaseFacts(
        now=now,
        current=match.group(1),
        release_dirs=release_dirs,
        staging_dirs=staging_dirs,
        build_dirs=build_dirs,
        upload_dirs=upload_dirs,
        states=states,
        tag_ids=_image_tags(runner),
        used_image_ids=used,
        compose_dirs=compose_dirs,
    )


def plan_releases(facts: ReleaseFacts, policy: Mapping) -> dict:
    """Pure: decide what to keep, retire, finalize and only report."""
    rules = policy["releases"]
    activation: dict[str, str] = {}
    failed: set[str] = set()
    for sha, rows in facts.states.items():
        ok = [ts for ts, state in rows if state in ACTIVATED]
        if ok:
            activation[sha] = max(ok)
        if any(state in FAILED for _, state in rows):
            failed.add(sha)

    tags_by_sha: dict[str, list[str]] = defaultdict(list)
    for ref in facts.tag_ids:
        tags_by_sha[_release_of_tag(ref) or ""].append(ref)
    tags_by_sha.pop("", None)
    uploads_by_sha: dict[str, list[str]] = defaultdict(list)
    for name in facts.upload_dirs:
        uploads_by_sha[UPLOAD_DIR.fullmatch(name).group(1)].append(name)
    staging_by_sha: dict[str, list[str]] = defaultdict(list)
    for name in facts.staging_dirs:
        staging_by_sha[STAGING_DIR.fullmatch(name).group(1)].append(name)

    known = (
        set(facts.release_dirs) | set(facts.build_dirs) | set(uploads_by_sha)
        | set(tags_by_sha) | set(staging_by_sha)
    )
    reasons: dict[str, list[str]] = defaultdict(list)
    reasons[facts.current].append("current")
    ranked = sorted(activation, key=lambda sha: (activation[sha], sha), reverse=True)
    for sha in ranked[: rules["keep_activated"]]:
        reasons[sha].append("recently-activated")
    for pin in rules["pinned"]:
        for sha in known:
            if sha == pin or (len(pin) >= 7 and FULL_SHA.fullmatch(sha) and sha.startswith(pin)):
                reasons[sha].append("pinned")
    for name in facts.compose_dirs:
        reasons[name].append("compose-working-dir")
    for sha, refs in tags_by_sha.items():
        if any(facts.tag_ids[ref] in facts.used_image_ids for ref in refs):
            reasons[sha].append("image-in-use")

    ttl = timedelta(hours=rules["failed_ttl_hours"])

    def newest(sha: str) -> datetime:
        stamps = [facts.release_dirs.get(sha), facts.build_dirs.get(sha)]
        stamps += [facts.upload_dirs[name] for name in uploads_by_sha.get(sha, ())]
        stamps += [facts.staging_dirs[name] for name in staging_by_sha.get(sha, ())]
        return max(stamp for stamp in stamps if stamp is not None) if any(stamps) else facts.now

    def objects(sha: str) -> dict:
        return {
            "sha": sha,
            "tags": sorted(tags_by_sha.get(sha, ())),
            "release_dir": sha if sha in facts.release_dirs else None,
            "build_dir": sha if sha in facts.build_dirs else None,
            "upload_dirs": sorted(uploads_by_sha.get(sha, ())),
            "staging_dirs": sorted(staging_by_sha.get(sha, ())),
        }

    retire, report = [], []
    for sha in sorted(known - set(reasons)):
        if sha in activation:
            retire.append({**objects(sha), "reason": "outside-retention-window"})
            continue
        # An upload that was never built is an abandoned attempt too: a deploy uploads and builds in one
        # invocation, so one left behind met a busy lock or a failure; infrastructure approvals leave one each.
        failed_attempt = (
            sha in failed
            or (sha in facts.build_dirs and sha not in facts.release_dirs)
            or sha in staging_by_sha
            or (sha in uploads_by_sha and sha not in facts.release_dirs)
        )
        if failed_attempt and facts.now - newest(sha) > ttl:
            retire.append({**objects(sha), "reason": "failed-past-ttl"})
        elif failed_attempt:
            report.append({"sha": sha, "reason": "failed-within-ttl"})
        else:
            report.append({"sha": sha, "reason": "no-activation-evidence"})

    finalize = []
    for sha in sorted(set(reasons) & set(activation)):
        aliases = [
            ref for ref in tags_by_sha.get(sha, ())
            if ALIAS_TAG.fullmatch(ref)
            and facts.tag_ids[ref] == facts.tag_ids.get(
                f"car-agent-release/{ALIAS_TAG.fullmatch(ref).group(2)}:{sha}"
            )
        ]
        entry = {
            "sha": sha,
            "alias_tags": sorted(aliases),
            "build_dir": sha if sha in facts.build_dirs else None,
            "upload_dirs": sorted(uploads_by_sha.get(sha, ())),
            "staging_dirs": sorted(staging_by_sha.get(sha, ())),
        }
        if entry["alias_tags"] or entry["build_dir"] or entry["upload_dirs"] or entry["staging_dirs"]:
            finalize.append(entry)
    return {
        "current": facts.current,
        "keep": [{"sha": sha, "reasons": sorted(set(why))} for sha, why in sorted(reasons.items())],
        "retire": retire,
        "finalize": finalize,
        "report": report,
    }


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


class _Executor:
    """Performs one plan; every object is re-validated right before it is touched."""

    def __init__(self, root: Path, runner: Runner, current: str) -> None:
        self.root = root
        self.runner = runner
        self.current_dir = os.path.realpath(root / "releases" / current)
        self.removed_tags: list[str] = []
        self.removed_dirs: list[str] = []
        self.moved_evidence: list[str] = []
        self.skipped: list[list[str]] = []

    def _checked_dir(self, parent: Path, name: str, pattern: re.Pattern[str]) -> Path | None:
        path = parent / name
        if not pattern.fullmatch(name):
            self.skipped.append([str(path), "name does not match"])
            return None
        if not os.path.lexists(path):
            return None
        if not _real_dir(path) or os.path.realpath(path.parent) != os.path.realpath(parent):
            self.skipped.append([str(path), "not a plain directory under its root"])
            return None
        if os.path.realpath(path) == self.current_dir:
            self.skipped.append([str(path), "current release"])
            return None
        return path

    def remove_dir(self, parent: Path, name: str, pattern: re.Pattern[str]) -> None:
        path = self._checked_dir(parent, name, pattern)
        if path is not None:
            shutil.rmtree(path)
            self.removed_dirs.append(str(path))

    def finalize_build(self, sha: str) -> None:
        """Move build evidence next to the release state records, then drop the workspace."""
        path = self._checked_dir(self.root / "builds", sha, FULL_SHA)
        if path is None:
            return
        target = self.root / "shared" / "evidence" / "releases" / sha / "build"
        relatives = [
            entry.name for entry in sorted(path.iterdir())
            if entry.name not in BUILD_BULK and entry.is_file() and not entry.is_symlink()
        ] + list(BUILD_EVIDENCE_EXTRA)
        for relative in relatives:
            source = path / relative
            if not source.is_file() or source.is_symlink():
                continue
            if source.stat().st_size > BUILD_EVIDENCE_MAX_BYTES:
                self.skipped.append([str(source), "evidence file too large to keep; left in place"])
                return
            destination = target / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                if _digest(destination) != _digest(source):
                    self.skipped.append([str(path), "evidence already recorded with other content"])
                    return
                continue
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
            if _digest(destination) != _digest(source):
                self.skipped.append([str(path), "evidence copy mismatch"])
                return
            self.moved_evidence.append(str(destination))
        shutil.rmtree(path)
        self.removed_dirs.append(str(path))

    def remove_tags(self, refs: Iterable[str]) -> None:
        wanted = [ref for ref in refs if _release_of_tag(ref) is not None]
        if not wanted:
            return
        present = _image_tags(self.runner)
        used, _ = _container_facts(self.runner, self.root)
        safe = []
        for ref in wanted:
            if ref not in present:
                continue
            if present[ref] in used:
                self.skipped.append([ref, "image in use"])
                continue
            safe.append(ref)
        for start in range(0, len(safe), TAG_BATCH):
            batch = safe[start:start + TAG_BATCH]
            try:
                self.runner(["docker", "image", "rm", *batch])
            except RetentionError:
                pass  # partial success is normal; the survivors are reported below
        remaining = _image_tags(self.runner)
        for ref in safe:
            if ref in remaining:
                self.skipped.append([ref, "tag removal failed"])
            else:
                self.removed_tags.append(ref)

    def result(self) -> dict:
        return {
            "removed_tags": self.removed_tags,
            "removed_dirs": self.removed_dirs,
            "moved_evidence": self.moved_evidence,
            "skipped": self.skipped,
        }


def apply_release_plan(plan: Mapping, root: Path, runner: Runner) -> dict:
    executor = _Executor(root, runner, plan["current"])
    executor.remove_tags(
        [ref for entry in plan["retire"] for ref in entry["tags"]]
        + [ref for entry in plan["finalize"] for ref in entry["alias_tags"]]
    )
    _, compose_dirs = _container_facts(runner, root)  # re-read: a container may have been recreated
    for entry in plan["retire"]:
        if entry["release_dir"] in compose_dirs:
            executor.skipped.append([entry["release_dir"], "compose working directory of a container"])
        elif entry["release_dir"]:
            executor.remove_dir(root / "releases", entry["release_dir"], SELECTOR)
    for entry in [*plan["retire"], *plan["finalize"]]:
        if entry["build_dir"]:
            executor.finalize_build(entry["build_dir"])
        for name in entry["upload_dirs"]:
            executor.remove_dir(root / "incoming" / "releases", name, UPLOAD_DIR)
        for name in entry["staging_dirs"]:
            executor.remove_dir(root / "releases", name, STAGING_DIR)
    return executor.result()


# ---------------------------------------------------------------- backups


@dataclass(frozen=True)
class BackupFacts:
    now: datetime
    #: (kind directory, file name, mtime) for postgres / redis / observability
    files: tuple[tuple[str, str, datetime], ...]
    manifests: tuple[str, ...]


def gather_backup_facts(root: Path, now: datetime) -> BackupFacts:
    base = root / "shared" / "backups"
    files = []
    for kind in BACKUP_KINDS:
        directory = base / kind
        if not _real_dir(directory):
            continue
        for entry in directory.iterdir():
            if entry.is_file() and not entry.is_symlink():
                files.append((kind, entry.name, _mtime(entry)))
    manifests = tuple(
        entry.name for entry in base.iterdir()
        if MANIFEST_FILE.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink()
    ) if _real_dir(base) else ()
    return BackupFacts(now=now, files=tuple(files), manifests=manifests)


def plan_backups(facts: BackupFacts, policy: Mapping) -> dict:
    """Pure GFS selection: recent sets, then newest per day and per week, never below the minimum."""
    rules = policy["backups"]
    sets: dict[str, dict[str, str]] = defaultdict(dict)
    partials: list[tuple[str, str]] = []
    unknown: list[str] = []
    for kind, name, mtime in facts.files:
        match = BACKUP_FILE.fullmatch(name)
        if match is None or match.group(2) != BACKUP_KINDS[kind]:
            unknown.append(f"{kind}/{name}")
            continue
        if match.group(3):
            if facts.now - mtime > PARTIAL_TTL:
                partials.append((kind, name))
            continue
        sets[match.group(1)][kind] = name
    for name in facts.manifests:
        sets[MANIFEST_FILE.fullmatch(name).group(1)]["manifest"] = name

    complete = sorted(ts for ts, parts in sets.items() if all(kind in parts for kind in BACKUP_KINDS))
    keep: set[str] = set()
    if sets:
        keep.add(max(sets))
    recent = timedelta(hours=rules["keep_all_hours"])
    keep |= {ts for ts in sets if facts.now - _timestamp(ts) <= recent}
    day_floor = (facts.now - timedelta(days=rules["daily_days"])).date()
    newest_per_day: dict[object, str] = {}
    week_floor = facts.now - timedelta(weeks=rules["weekly_weeks"])
    newest_per_week: dict[object, str] = {}
    for ts in complete:
        moment = _timestamp(ts)
        if rules["daily_days"] and moment.date() >= day_floor:
            newest_per_day[moment.date()] = max(newest_per_day.get(moment.date(), ts), ts)
        if rules["weekly_weeks"] and moment >= week_floor:
            week = moment.isocalendar()[:2]
            newest_per_week[week] = max(newest_per_week.get(week, ts), ts)
    keep |= set(newest_per_day.values()) | set(newest_per_week.values())
    keep |= set(complete[-rules["min_complete_sets"]:])
    delete = []
    for ts in sorted(set(sets) - keep):
        for kind, name in sorted(sets[ts].items()):
            delete.append([kind, name])
    delete += [[kind, name] for kind, name in sorted(partials)]
    return {
        "sets": len(sets),
        "complete_sets": len(complete),
        "keep_sets": sorted(keep),
        "delete_files": delete,
        "unknown_files": sorted(unknown),
    }


def apply_backup_plan(plan: Mapping, root: Path) -> dict:
    base = root / "shared" / "backups"
    removed, skipped = [], []
    for kind, name in plan["delete_files"]:
        if kind == "manifest":
            parent, valid = base, MANIFEST_FILE.fullmatch(name) is not None
        else:
            match = BACKUP_FILE.fullmatch(name)
            valid = kind in BACKUP_KINDS and match is not None and match.group(2) == BACKUP_KINDS[kind]
            parent = base / kind
        path = parent / name
        if not valid:
            skipped.append([str(path), "name does not match"])
            continue
        if not os.path.lexists(path):
            continue
        if not _real_dir(parent) or not stat.S_ISREG(os.lstat(path).st_mode):
            skipped.append([str(path), "not a plain file under its root"])
            continue
        os.unlink(path)
        removed.append(str(path))
    return {"removed_files": removed, "skipped": skipped}


# ---------------------------------------------------------------- entry point


def _run(argv: Sequence[str]) -> str:
    completed = subprocess.run(list(argv), capture_output=True, text=True, timeout=900)
    if completed.returncode != 0:
        raise RetentionError("command failed: " + " ".join(argv[:3]))
    return completed.stdout


def validate_lock_fd(root: Path, descriptor: int) -> None:
    """The caller must hold the transaction lock; prove it by the inherited descriptor."""
    expected = os.path.realpath(root / "shared" / "locks" / "release.lock")
    try:
        actual = os.path.realpath(os.readlink(f"/proc/self/fd/{descriptor}"))
    except OSError as exc:
        raise RetentionError("transaction lock descriptor is not inherited") from exc
    if actual != expected:
        raise RetentionError("transaction lock descriptor is not the release lock")


def _free_bytes(root: Path) -> int:
    return shutil.disk_usage(root).free


def _write_evidence(root: Path, record: Mapping, kind: str) -> Path:
    directory = root / "shared" / "evidence" / "retention"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"{record['started_utc'].replace(':', '').replace('-', '')}-{kind}.json"
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    os.chmod(target, 0o600)
    return target


def main(argv: Sequence[str] | None = None, *, runner: Runner = _run) -> int:
    parser = argparse.ArgumentParser(description="car-agent retention (releases / backups)")
    parser.add_argument("kind", choices=("releases", "backups"))
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    parser.add_argument("--reason", choices=("deploy", "rollback", "backup", "manual"), default="manual")
    parser.add_argument("--lock-fd", type=int, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    record: dict[str, object] = {
        "schema_version": 1,
        "kind": args.kind,
        "mode": args.mode,
        "reason": args.reason,
        "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        validate_lock_fd(args.root, args.lock_fd)
        policy = load_policy(args.policy or args.root / "shared" / "retention-policy.json")
        record["policy"] = policy[args.kind]
        record["free_bytes_before"] = _free_bytes(args.root)
        if args.kind == "releases":
            plan = plan_releases(gather_release_facts(args.root, runner, now), policy)
            record["plan"] = plan
            if args.mode == "apply":
                record["result"] = apply_release_plan(plan, args.root, runner)
        else:
            plan = plan_backups(gather_backup_facts(args.root, now), policy)
            record["plan"] = plan
            if args.mode == "apply":
                record["result"] = apply_backup_plan(plan, args.root)
        record["free_bytes_after"] = _free_bytes(args.root)
        record["status"] = "applied" if args.mode == "apply" else "planned"
        if args.mode == "apply":
            record["evidence"] = str(_write_evidence(args.root, record, args.kind))
    except RetentionError as exc:
        record.update(status="error", error=str(exc))
        print(json.dumps(record, sort_keys=True))
        return 2
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

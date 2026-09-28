"""Operator entry for the host retention policy (design 2026-09-28 §4.2–4.3).

Runs `remote-release.sh retention --dry-run|--apply` under the remote transaction lock and
condenses the two records that deploy/cloud/retention.py prints (releases, backups). The full
object lists stay in the remote evidence files; dry-run is the default.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from scripts.cloud_release_lib import CommandRunner, ReleaseError, ReleaseRequest

REMOTE_RETENTION = "sudo -n /opt/car-agent/shared/bin/remote-release.sh retention {mode}"
RETENTION_TIMEOUT_S = 1800.0


class RetentionReplyError(ReleaseError):
    def __init__(self, message: str) -> None:
        super().__init__(message, category="runtime")


def parse_retention_records(stdout: str) -> dict[str, Mapping[str, Any]]:
    records: dict[str, Mapping[str, Any]] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise RetentionReplyError("retention reply is not JSON") from exc
        kind = record.get("kind") if isinstance(record, dict) else None
        if kind not in {"releases", "backups"} or kind in records:
            raise RetentionReplyError("retention reply has an unexpected record")
        records[kind] = record
    if not records:
        raise RetentionReplyError("retention reply has no records")
    # A kind that printed nothing (crashed or never ran) is reported, not hidden behind a bare failure.
    for kind in ("releases", "backups"):
        records.setdefault(kind, {"kind": kind, "status": "missing"})
    return records


def summarize_retention(records: Mapping[str, Mapping[str, Any]]) -> dict[str, object]:
    releases, backups = records["releases"], records["backups"]
    release_plan = releases.get("plan") or {}
    backup_plan = backups.get("plan") or {}
    release_result = releases.get("result") or {}
    backup_result = backups.get("result") or {}
    return {
        "releases": {
            "status": releases.get("status"),
            "error": releases.get("error"),
            "current": release_plan.get("current"),
            "keep": release_plan.get("keep", []),
            "retire": [
                {
                    "sha": item["sha"],
                    "reason": item["reason"],
                    "tags": len(item["tags"]),
                    "dirs": sum(bool(item[key]) for key in ("release_dir", "build_dir"))
                    + len(item["upload_dirs"]) + len(item["staging_dirs"]),
                }
                for item in release_plan.get("retire", [])
            ],
            "finalize": [item["sha"] for item in release_plan.get("finalize", [])],
            "report": release_plan.get("report", []),
            "removed_tags": len(release_result.get("removed_tags", [])),
            "removed_dirs": len(release_result.get("removed_dirs", [])),
            "skipped": release_result.get("skipped", []),
            "free_bytes_before": releases.get("free_bytes_before"),
            "free_bytes_after": releases.get("free_bytes_after"),
            "evidence": releases.get("evidence"),
        },
        "backups": {
            "status": backups.get("status"),
            "error": backups.get("error"),
            "sets": backup_plan.get("sets"),
            "complete_sets": backup_plan.get("complete_sets"),
            "keep_sets": len(backup_plan.get("keep_sets", [])),
            "delete_files": len(backup_plan.get("delete_files", [])),
            "unknown_files": backup_plan.get("unknown_files", []),
            "removed_files": len(backup_result.get("removed_files", [])),
            "skipped": backup_result.get("skipped", []),
            "evidence": backups.get("evidence"),
        },
    }


def run_retention(
    request: ReleaseRequest, runner: CommandRunner, *, apply: bool
) -> tuple[bool, dict[str, object]]:
    mode = "--apply" if apply else "--dry-run"
    result = runner.run(
        request.ssh.ssh_argv(REMOTE_RETENTION.format(mode=mode)),
        cwd=request.repo,
        check=False,
        timeout_s=RETENTION_TIMEOUT_S,
    )
    records = parse_retention_records(result.stdout)
    expected = "applied" if apply else "planned"
    ok = result.returncode == 0 and all(item.get("status") == expected for item in records.values())
    return ok, summarize_retention(records)

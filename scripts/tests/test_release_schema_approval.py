"""One-time database_schema approval in the release gate (CA2-08 prerequisite).

Without a digest the gate keeps its hard block. A digest releases exactly the
reviewed schema transition and nothing else; it is never persisted remotely.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import cloud_release
from scripts.cloud_release_lib import (
    CloudReleaseResult,
    ControlledChange,
    ReleaseError,
    ReleasePlan,
    RemoteState,
    SshConfig,
    compute_database_schema_digest,
    database_schema_paths,
    make_release_plan,
)
from scripts.dev_stack_lib import cloud_release_argv

DEPLOYED = "4c1f479513c8b13564803ba43555a470aacbf640"
TARGET = "c" * 40
SQL = "agents/_sdk/ledger_schema.sql"
SQL_DIFF = "+ALTER TABLE task_ledger ADD COLUMN IF NOT EXISTS operation JSONB NOT NULL DEFAULT '{}';\n"


def plan(paths, diffs, *, target=None, approved=None):
    return make_release_plan(deployed_sha=DEPLOYED, target_sha=TARGET, changed_paths=paths,
                             diff_by_path=diffs, target_database_schema_digest=target,
                             approved_database_schema_digest=approved)


def test_schema_paths_cover_sql_and_production_ddl_only():
    diffs = {SQL: SQL_DIFF, "memory/pg_store.py": "+CREATE INDEX x ON memory_item (a)\n",
             "agents/_sdk/tests/test_x.py": "+ALTER TABLE t ADD COLUMN c INT\n",
             "agents/_sdk/ledger.py": "+UPDATE task_ledger SET status='done'\n"}
    assert database_schema_paths(list(diffs), diffs) == (SQL, "memory/pg_store.py")


def test_unapproved_schema_change_stays_hard_blocked():
    rejected = plan([SQL], {SQL: SQL_DIFF})
    assert rejected.status == "plan_rejected"
    assert rejected.blocking_changes == (ControlledChange(SQL, "database_schema"),)
    ddl = plan(["memory/pg_store.py"], {"memory/pg_store.py": "+ALTER TABLE m ADD COLUMN c INT\n"})
    assert ddl.status == "plan_rejected"
    assert ddl.blocking_changes == (ControlledChange("memory/pg_store.py", "database_schema"),)
    stale = plan([SQL], {SQL: SQL_DIFF}, target="e" * 64, approved="d" * 64)
    assert stale.status == "plan_rejected" and stale.blocking_changes


def test_exact_digest_releases_the_schema_transition():
    approved = plan([SQL, "memory/pg_store.py", "agents/x.py"],
                    {SQL: SQL_DIFF, "memory/pg_store.py": "+ALTER TABLE m ADD COLUMN c INT\n",
                     "agents/x.py": "+safe code\n"},
                    target="d" * 64, approved="d" * 64)
    assert approved.status == "ready" and approved.blocking_changes == ()
    assert approved.target_database_schema_digest == approved.approved_database_schema_digest == "d" * 64


@pytest.mark.parametrize(("other", "category", "status"), [
    (".env.example", "runtime_config_contract", "plan_rejected"),
    (".env.local", "secret_material", "plan_rejected"),
    (".github/workflows/ci.yml", "ci_cd", "plan_rejected"),
    ("deploy/cloud/remote-release.sh", "infrastructure", "bootstrap_required"),
])
def test_schema_digest_never_releases_another_category(other, category, status):
    result = plan([SQL, other], {SQL: SQL_DIFF, other: "+controlled\n"}, target="d" * 64, approved="d" * 64)
    assert result.status == status
    assert result.blocking_changes == (ControlledChange(other, category),)


@pytest.mark.parametrize("digest", ["", "ABC", "g" * 64, "A" * 64])
def test_malformed_schema_digests_are_configuration_errors(digest):
    for kwargs in ({"target": "d" * 64, "approved": digest}, {"target": digest, "approved": "d" * 64}):
        with pytest.raises(ReleaseError) as caught:
            plan([SQL], {SQL: SQL_DIFF}, **kwargs)
        assert caught.value.category == "configuration"


def test_an_unused_schema_approval_is_rejected():
    with pytest.raises(ReleaseError) as caught:
        plan(["agents/x.py"], {"agents/x.py": "+code\n"}, target=None, approved="d" * 64)
    assert caught.value.category == "configuration"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            _git(repo, "rm", "-q", rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Schema Approval Test")
    _git(path, "config", "user.email", "schema-approval@example.invalid")
    _git(path, "config", "core.autocrlf", "false")
    return path


def test_digest_binds_the_exact_transition(repo: Path):
    base = _commit(repo, {SQL: "-- ledger\n", "app.py": "x = 1\n"}, "base")
    target = _commit(repo, {SQL: "-- ledger\n" + SQL_DIFF[1:]}, "add column")
    digest = compute_database_schema_digest(repo, base, target, [SQL])
    assert digest and digest == compute_database_schema_digest(repo, base, target, [SQL])
    unrelated = _commit(repo, {"app.py": "x = 2\n"}, "unrelated")
    assert compute_database_schema_digest(repo, base, unrelated, [SQL]) == digest
    edited = _commit(repo, {SQL: "-- ledger\n-- edited after review\n"}, "edit")
    assert compute_database_schema_digest(repo, base, edited, [SQL]) != digest
    # Same target content from another deployed baseline is a different transition.
    assert (compute_database_schema_digest(repo, base, edited, [SQL])
            != compute_database_schema_digest(repo, target, edited, [SQL]))
    assert compute_database_schema_digest(repo, base, target, []) is None


def test_digest_records_added_and_deleted_schema_files(repo: Path):
    base = _commit(repo, {"app.py": "x = 1\n"}, "base")
    added = _commit(repo, {"new/schema.sql": "-- new\n"}, "add")
    removed = _commit(repo, {"new/schema.sql": None}, "remove")
    first = compute_database_schema_digest(repo, base, added, ["new/schema.sql"])
    second = compute_database_schema_digest(repo, added, removed, ["new/schema.sql"])
    assert first and second and first != second


def test_digest_needs_full_shas(repo: Path):
    with pytest.raises(ReleaseError):
        compute_database_schema_digest(repo, "abc", TARGET, [SQL])


def test_flag_exists_for_plan_and_deploy_only(tmp_path: Path):
    parser = cloud_release.build_parser()
    approval = "d" * 64
    for argv in (["plan", "--approve-database-schema-sha256", approval],
                 ["deploy", "--approve-database-schema-sha256", approval]):
        args = parser.parse_args(argv)
        assert args.approve_database_schema_sha256 == approval
    identity = tmp_path / "identity"
    identity.write_text("test-only identity", encoding="utf-8")
    request = cloud_release._request(tmp_path, args, SshConfig("demo.example", "ubuntu", identity))
    assert request.approved_database_schema_digest == approval
    assert parser.parse_args(["plan"]).approve_database_schema_sha256 is None
    for argv in (["verify", "--approve-database-schema-sha256", approval],
                 ["rollback", "--to", "a" * 7, "--approve-database-schema-sha256", approval]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_payload_audits_both_schema_digests():
    result = CloudReleaseResult(
        status="dry_run",
        plan=ReleasePlan(DEPLOYED, TARGET, (SQL,), (), "ready",
                         target_database_schema_digest="d" * 64,
                         approved_database_schema_digest="d" * 64),
        artifact=None,
        remote_state=RemoteState(
            current_release=DEPLOYED, current_path=f"/opt/car-agent/releases/{DEPLOYED}",
            runtime_project_name="4c1f479", running_release_tags=(DEPLOYED,),
            approved_infrastructure_digest=None, disk_available_bytes=100 * 1024**3,
            memory_available_bytes=5 * 1024**3, release_lock_available=True,
            runtime_project_ready=True, shared_scripts_ready=True, shared_models_ready=True),
    )
    payload = cloud_release._result_payload(result)
    assert payload["target_database_schema_sha256"] == payload["approved_database_schema_sha256"] == "d" * 64


def test_dev_stack_forwards_the_exact_schema_approval_only_for_deploy(tmp_path: Path):
    digest = "d" * 64
    argv = cloud_release_argv(tmp_path, "deploy", "HEAD", apply=True, approved_database_schema_digest=digest)
    assert argv[-3:] == ["--approve-database-schema-sha256", digest, "--apply"]
    assert "--approve-database-schema-sha256" not in cloud_release_argv(tmp_path, "deploy", "HEAD", apply=False)
    assert "--approve-database-schema-sha256" not in cloud_release_argv(tmp_path, "verify", "HEAD", apply=False)

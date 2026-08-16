from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.cloud_release_lib import (
    ControlledChange,
    ReleasePlan,
    ReleaseError,
    SshConfig,
    SubprocessRunner,
    classify_changed_path,
    compute_infrastructure_digest,
    diff_contains_schema_change,
    git_changes,
    make_release_plan,
    require_clean_main_commit,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Cloud Release Test")
    git(repo, "config", "user.email", "cloud-release@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo, git(repo, "rev-parse", "HEAD")


def test_require_clean_main_commit_accepts_full_main_sha(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    assert require_clean_main_commit(repo, "HEAD") == sha


def test_require_clean_main_commit_rejects_dirty_tree(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="worktree is not clean"):
        require_clean_main_commit(repo, "HEAD")


def test_require_clean_main_commit_rejects_untracked_file(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="worktree is not clean"):
        require_clean_main_commit(repo, "HEAD")


def test_require_clean_main_commit_rejects_unreachable_commit(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    git(repo, "switch", "-c", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    git(repo, "commit", "-m", "feature")
    with pytest.raises(ReleaseError, match="not reachable from main"):
        require_clean_main_commit(repo, "HEAD")


def test_runner_redacts_secret_values(tmp_path: Path):
    runner = SubprocessRunner(redactions={"secret-value"})
    result = runner.run(
        [sys.executable, "-c", "print('secret-value')"],
        cwd=tmp_path,
    )
    assert result.stdout == "[REDACTED]\r\n" or result.stdout == "[REDACTED]\n"


def test_runner_failure_does_not_echo_full_argv(tmp_path: Path):
    runner = SubprocessRunner(redactions={"secret-value"})
    with pytest.raises(ReleaseError) as caught:
        runner.run(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(7)",
                "secret-value",
            ],
            cwd=tmp_path,
        )
    message = str(caught.value)
    assert "[REDACTED]" in message
    assert "secret-value" not in message
    assert "-c" not in message


def test_ssh_config_builds_strict_batch_argv(tmp_path: Path):
    identity = tmp_path / "agent.pem"
    config = SshConfig(
        host="server.example.invalid",
        user="ubuntu",
        identity=identity,
        kex_algorithms="curve25519-sha256",
    )
    assert config.ssh_argv("true") == [
        "ssh",
        "-i",
        str(identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "KexAlgorithms=curve25519-sha256",
        "ubuntu@server.example.invalid",
        "true",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "-oProxyCommand=bad"),
        ("host", "server name"),
        ("user", "-root"),
        ("user", "ubuntu;id"),
        ("kex_algorithms", "curve25519-sha256 ProxyCommand=bad"),
    ],
)
def test_ssh_config_rejects_unsafe_connection_fields(
    tmp_path: Path,
    field: str,
    value: str,
):
    kwargs = {
        "host": "server.example.invalid",
        "user": "ubuntu",
        "identity": tmp_path / "agent.pem",
        "kex_algorithms": "curve25519-sha256",
    }
    kwargs[field] = value
    with pytest.raises(ReleaseError, match="invalid SSH"):
        SshConfig(**kwargs)


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("deploy/cloud/backup.sh", "infrastructure"),
        ("deploy/cloud/README.md", "application"),
        ("compose.yaml", "infrastructure"),
        ("deploy/docker-compose.yaml", "infrastructure"),
        (".env.example", "runtime_config_contract"),
        (".env.local", "secret_material"),
        ("memory/schema.sql", "database_schema"),
        ("registry/postgres_schema.sql", "database_schema"),
        ("proactive/schema.sql", "database_schema"),
        (".github/workflows/ci.yml", "ci_cd"),
        ("certs/server.key", "secret_material"),
        ("orchestrator/cloud/engine.py", "application"),
    ],
)
def test_classify_changed_path(path: str, category: str):
    assert classify_changed_path(path) == category


def test_classify_diff_rejects_ddl_added_inside_python():
    diff = "+    conn.execute('ALTER TABLE turns ADD COLUMN secret TEXT')\n"
    assert diff_contains_schema_change("observability/collector/db.py", diff)


def test_classify_diff_ignores_removed_or_comment_only_ddl():
    diff = "-ALTER TABLE old_table DROP COLUMN old_value\n+# ALTER TABLE example\n"
    assert not diff_contains_schema_change("observability/collector/db.py", diff)


def test_classify_diff_ignores_ddl_fixture_in_tests():
    diff = "+CREATE TABLE fixture_only(id TEXT)\n"
    assert not diff_contains_schema_change(
        "scripts/tests/test_manifest.py",
        diff,
    )


def test_make_release_plan_blocks_controlled_changes():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="a" * 40,
        changed_paths=[
            "gateway/edge/main.go",
            "deploy/cloud/compose.cloud.yaml",
        ],
        diff_by_path={
            "gateway/edge/main.go": "",
            "deploy/cloud/compose.cloud.yaml": "",
        },
    )
    assert plan.status == "bootstrap_required"
    assert plan.blocking_changes == (
        ControlledChange(
            "deploy/cloud/compose.cloud.yaml",
            "infrastructure",
        ),
    )


def test_make_release_plan_accepts_application_only_change():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="b" * 40,
        changed_paths=["gateway/edge/main.go"],
        diff_by_path={
            "gateway/edge/main.go": "+safe application code\n",
        },
    )
    assert plan.status == "ready"
    assert plan.blocking_changes == ()


def test_make_release_plan_accepts_exactly_approved_infrastructure():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=["deploy/cloud/remote-release.sh"],
        diff_by_path={
            "deploy/cloud/remote-release.sh": "+safe reviewed script\n",
        },
        target_infrastructure_digest="d" * 64,
        approved_infrastructure_digest="d" * 64,
    )
    assert plan.status == "ready"
    assert plan.blocking_changes == ()


def test_make_release_plan_rejects_stale_infrastructure_approval():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=["deploy/cloud/remote-release.sh"],
        diff_by_path={
            "deploy/cloud/remote-release.sh": "+unapproved revision\n",
        },
        target_infrastructure_digest="e" * 64,
        approved_infrastructure_digest="d" * 64,
    )
    assert plan.status == "bootstrap_required"
    assert plan.blocking_changes[0].category == "infrastructure"


def test_make_release_plan_rejects_production_ddl():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=["memory/pg_store.py"],
        diff_by_path={
            "memory/pg_store.py": "+ALTER TABLE memory_item ADD COLUMN value TEXT\n",
        },
    )
    assert plan.status == "plan_rejected"
    assert plan.blocking_changes == (
        ControlledChange("memory/pg_store.py", "database_schema"),
    )


def test_git_changes_returns_a_diff_per_path(tmp_path: Path):
    repo, base = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    git(repo, "add", "tracked.txt", "second.txt")
    git(repo, "commit", "-m", "second")
    target = git(repo, "rev-parse", "HEAD")

    paths, diffs = git_changes(repo, base, target)

    assert paths == ["second.txt", "tracked.txt"]
    assert set(diffs) == set(paths)
    assert "+second" in diffs["second.txt"]
    assert "+two" in diffs["tracked.txt"]


def test_compute_infrastructure_digest_reads_committed_bytes_only(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    cloud = repo / "deploy" / "cloud"
    cloud.mkdir(parents=True)
    (cloud / "remote-release.sh").write_text("one\n", encoding="utf-8")
    (cloud / "README.md").write_text("ignored one\n", encoding="utf-8")
    git(repo, "add", "deploy/cloud")
    git(repo, "commit", "-m", "cloud")
    sha = git(repo, "rev-parse", "HEAD")
    first = compute_infrastructure_digest(repo, sha)

    (cloud / "remote-release.sh").write_text("dirty\n", encoding="utf-8")
    (cloud / "README.md").write_text("ignored two\n", encoding="utf-8")

    assert compute_infrastructure_digest(repo, sha) == first
    assert len(first) == 64


def test_release_plan_positional_contract_is_stable():
    plan = ReleasePlan("a" * 40, "b" * 40, (), (), "ready")
    assert plan.status == "ready"

from __future__ import annotations

import json
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import cloud_release
from scripts import cloud_release_lib
from scripts.cloud_release_lib import (
    ControlledChange,
    CommandResult,
    CloudReleaseResult,
    ReleaseRequest,
    ReleaseArtifact,
    ReleasePlan,
    ReleaseError,
    RemoteState,
    MODEL_BOOTSTRAP_FILES,
    REMOTE_PREFLIGHT_COMMAND,
    REMOTE_PREFLIGHT_SOURCE,
    SshConfig,
    SubprocessRunner,
    build_release_artifact,
    classify_changed_path,
    compute_ci_cd_digest,
    compute_infrastructure_digest,
    diff_contains_schema_change,
    execute_deploy,
    git_changes,
    make_bootstrap_report,
    make_release_plan,
    parse_remote_state,
    require_clean_main_commit,
    validate_archive_member_names,
    validate_text_payload,
)


ROOT = Path(__file__).resolve().parents[2]


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


def test_require_clean_main_commit_rejects_invalid_revision_as_configuration(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    with pytest.raises(ReleaseError) as caught:
        require_clean_main_commit(repo, "not-a-revision")
    assert caught.value.category == "configuration"


def test_resolve_commit_rejects_invalid_revision_as_configuration(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    with pytest.raises(ReleaseError) as caught:
        cloud_release_lib._resolve_commit(repo, "not-a-revision")
    assert caught.value.category == "configuration"

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


def test_runner_timeout_terminates_grandchild_process_tree(tmp_path: Path):
    marker = tmp_path / "grandchild-survived.txt"
    child = (
        "import time; from pathlib import Path; time.sleep(0.8); "
        f"Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    )
    with pytest.raises(ReleaseError, match="timed out"):
        SubprocessRunner().run(
            [sys.executable, "-c", parent], cwd=tmp_path, timeout_s=0.2,
        )
    time.sleep(1.0)
    assert not marker.exists()


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
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=240",
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


def test_make_release_plan_ci_cd_change_requires_approval():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=[".github/workflows/ci.yml"],
        diff_by_path={".github/workflows/ci.yml": "+reviewed workflow\n"},
    )

    assert plan.status == "plan_rejected"
    assert plan.blocking_changes == (
        ControlledChange(".github/workflows/ci.yml", "ci_cd"),
    )
    assert plan.target_ci_cd_digest is None
    assert plan.approved_ci_cd_digest is None


def test_make_release_plan_exact_ci_cd_digest_removes_ci_cd_blocker():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=[".github/workflows/ci.yml"],
        diff_by_path={".github/workflows/ci.yml": "+reviewed workflow\n"},
        target_ci_cd_digest="d" * 64,
        approved_ci_cd_digest="d" * 64,
    )

    assert plan.status == "ready"
    assert plan.blocking_changes == ()
    assert plan.target_ci_cd_digest == "d" * 64
    assert plan.approved_ci_cd_digest == "d" * 64


@pytest.mark.parametrize(
    ("other_path", "other_category", "expected_status"),
    [
        (".env.example", "runtime_config_contract", "plan_rejected"),
        ("memory/schema.sql", "database_schema", "plan_rejected"),
        (".env.local", "secret_material", "plan_rejected"),
        (
            "deploy/cloud/remote-release.sh",
            "infrastructure",
            "bootstrap_required",
        ),
    ],
)
def test_make_release_plan_ci_cd_digest_removes_only_ci_cd_blocker(
    other_path: str,
    other_category: str,
    expected_status: str,
):
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=[".github/workflows/ci.yml", other_path],
        diff_by_path={
            ".github/workflows/ci.yml": "+reviewed workflow\n",
            other_path: "+controlled change\n",
        },
        target_ci_cd_digest="d" * 64,
        approved_ci_cd_digest="d" * 64,
    )

    assert plan.status == expected_status
    assert plan.blocking_changes == (
        ControlledChange(other_path, other_category),
    )


def test_make_release_plan_stale_ci_cd_approval_keeps_blocker():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=[".github/workflows/ci.yml"],
        diff_by_path={".github/workflows/ci.yml": "+unapproved revision\n"},
        target_ci_cd_digest="e" * 64,
        approved_ci_cd_digest="d" * 64,
    )

    assert plan.status == "plan_rejected"
    assert plan.blocking_changes == (
        ControlledChange(".github/workflows/ci.yml", "ci_cd"),
    )


@pytest.mark.parametrize("approved_digest", ["", "ABC", "g" * 64, "A" * 64])
def test_make_release_plan_rejects_invalid_ci_cd_approval(approved_digest: str):
    with pytest.raises(ReleaseError) as caught:
        make_release_plan(
            deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
            target_sha="c" * 40,
            changed_paths=[".github/workflows/ci.yml"],
            diff_by_path={".github/workflows/ci.yml": "+reviewed workflow\n"},
            target_ci_cd_digest="d" * 64,
            approved_ci_cd_digest=approved_digest,
        )

    assert caught.value.category == "configuration"


def test_make_release_plan_rejects_invalid_ci_cd_target_digest():
    with pytest.raises(ReleaseError) as caught:
        make_release_plan(
            deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
            target_sha="c" * 40,
            changed_paths=[".github/workflows/ci.yml"],
            diff_by_path={".github/workflows/ci.yml": "+reviewed workflow\n"},
            target_ci_cd_digest="A" * 64,
            approved_ci_cd_digest="d" * 64,
        )

    assert caught.value.category == "configuration"


def test_make_release_plan_rejects_unused_ci_cd_approval():
    with pytest.raises(ReleaseError) as caught:
        make_release_plan(
            deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
            target_sha="c" * 40,
            changed_paths=["gateway/edge/main.go"],
            diff_by_path={"gateway/edge/main.go": "+safe application code\n"},
            target_ci_cd_digest="d" * 64,
            approved_ci_cd_digest="d" * 64,
        )

    assert caught.value.category == "configuration"


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


def test_ci_cd_digest_uses_target_commit_workflow_tree(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (workflows / "mobile.yml").write_text("name: mobile\n", encoding="utf-8")
    git(repo, "add", ".github/workflows")
    git(repo, "commit", "-m", "workflows")
    sha = git(repo, "rev-parse", "HEAD")
    first = compute_ci_cd_digest(repo, sha)

    (workflows / "ci.yml").write_text("name: dirty\n", encoding="utf-8")
    assert compute_ci_cd_digest(repo, sha) == first
    assert first is not None
    assert re.fullmatch(r"[0-9a-f]{64}", first)

    git(repo, "add", ".github/workflows/ci.yml")
    git(repo, "commit", "-m", "change ci")
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) != first


def test_ci_cd_digest_tracks_workflow_add_delete_and_rename(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    ci = workflows / "ci.yml"
    ci.write_text("name: ci\n", encoding="utf-8")
    git(repo, "add", ".github/workflows/ci.yml")
    git(repo, "commit", "-m", "ci")
    single_sha = git(repo, "rev-parse", "HEAD")
    single = compute_ci_cd_digest(repo, single_sha)

    mobile = workflows / "mobile.yml"
    mobile.write_text("name: mobile\n", encoding="utf-8")
    git(repo, "add", ".github/workflows/mobile.yml")
    git(repo, "commit", "-m", "mobile")
    with_mobile = compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD"))
    assert with_mobile != single

    git(repo, "mv", ".github/workflows/mobile.yml", ".github/workflows/android.yml")
    git(repo, "commit", "-m", "rename mobile")
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) != with_mobile

    git(repo, "rm", ".github/workflows/android.yml")
    git(repo, "commit", "-m", "remove android")
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) == single


def test_ci_cd_digest_returns_none_without_committed_workflows(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    assert compute_ci_cd_digest(repo, sha) is None


def test_ci_cd_digest_rejects_non_regular_tree_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, sha = make_repo(tmp_path)

    monkeypatch.setattr(
        cloud_release_lib,
        "_git",
        lambda *_args, **_kwargs: CommandResult(
            (), 0, "120000 blob " + "a" * 40 + "\t.github/workflows/ci.yml\0", ""
        ),
    )

    with pytest.raises(ReleaseError) as caught:
        compute_ci_cd_digest(repo, sha)
    assert caught.value.category == "safety"


@pytest.mark.parametrize(
    "entry",
    [
        "100644 blob " + "a" * 40 + "\t.github\\workflows\\ci.yml\0",
        "100644 blob " + "a" * 40 + "\t.github/workflows/ci\x01.yml\0",
        "100644 blob " + "a" * 40 + "\t.github/workflows/ci\u0085.yml\0",
        "100644 blob " + "a" * 40 + "\tother/ci.yml\0",
        "100644 blob " + "a" * 40 + "\t.github/workflows/../ci.yml\0",
        "100644 blob " + "a" * 40 + "\t.github/workflows/ci.yml\0"
        "100755 blob " + "b" * 40 + "\t.github/workflows/ci.yml\0",
        "100600 blob " + "a" * 40 + "\t.github/workflows/ci.yml\0",
        "100644 tree " + "a" * 40 + "\t.github/workflows/ci.yml\0",
        "160000 commit " + "a" * 40 + "\t.github/workflows/x.yml\0",
        "100644 blob " + "a" * 40 + "\t.github/workflows/ci.yml",
        "100644 blob " + "a" * 40 + ".github/workflows/ci.yml\0",
        "100644 blob\t.github/workflows/ci.yml\0",
        "100644 blob not-hex\t.github/workflows/ci.yml\0",
        "100644 blob " + "a" * 39 + "\t.github/workflows/ci.yml\0",
    ],
)
def test_ci_cd_digest_rejects_unsafe_tree_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
):
    repo, sha = make_repo(tmp_path)

    monkeypatch.setattr(
        cloud_release_lib,
        "_git",
        lambda *_args, **_kwargs: CommandResult((), 0, entry, ""),
    )

    with pytest.raises(ReleaseError) as caught:
        compute_ci_cd_digest(repo, sha)
    assert caught.value.category == "safety"


def test_ci_cd_digest_rejects_invalid_utf8_tree_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, sha = make_repo(tmp_path)

    monkeypatch.setattr(
        cloud_release_lib.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                b"100644 blob " + b"a" * 40 + b"\t.github/workflows/ci\xff.yml\0"
            ),
            stderr=b"",
        ),
    )

    with pytest.raises(ReleaseError) as caught:
        compute_ci_cd_digest(repo, sha)
    assert caught.value.category == "safety"


def test_release_plan_positional_contract_is_stable():
    plan = ReleasePlan("a" * 40, "b" * 40, (), (), "ready")
    assert plan.status == "ready"


def test_build_release_artifact_contains_only_committed_source(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    committed = subprocess.run(
        ["git", "show", f"{sha}:tracked.txt"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    (repo / "tracked.txt").write_text("dirty working copy\n", encoding="utf-8")
    output_root = tmp_path / "artifacts"

    artifact = build_release_artifact(
        repo=repo,
        output_root=output_root,
        plan=ReleasePlan(sha, sha, (), (), "ready"),
        services_digest="1" * 64,
        models_digest="2" * 64,
    )

    assert isinstance(artifact, ReleaseArtifact)
    assert artifact.directory == output_root / sha
    assert artifact.source_tar.is_file()
    assert artifact.manifest.is_file()
    assert artifact.checksums.is_file()
    assert artifact.transport_tar.is_file()
    with tarfile.open(artifact.source_tar) as archive:
        assert archive.getnames() == ["tracked.txt"]
        extracted = archive.extractfile("tracked.txt")
        assert extracted is not None
        assert extracted.read() == committed
    with tarfile.open(artifact.transport_tar) as archive:
        assert archive.getnames() == [
            "source.tar",
            "manifest.json",
            "checksums.sha256",
        ]


def test_build_release_artifact_generates_proto_from_committed_source(
    tmp_path: Path,
):
    repo, _ = make_repo(tmp_path)
    (repo / "buf.gen.yaml").write_text("version: v1\n", encoding="utf-8")
    proto = repo / "proto"
    proto.mkdir()
    (proto / "buf.yaml").write_text("version: v1\n", encoding="utf-8")
    (proto / "agent.proto").write_text(
        'syntax = "proto3";\n',
        encoding="utf-8",
    )
    git(repo, "add", "buf.gen.yaml", "proto")
    git(repo, "commit", "-m", "proto")
    sha = git(repo, "rev-parse", "HEAD")

    class CodegenRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], Path]] = []

        def run(
            self,
            argv,
            *,
            cwd,
            env=None,
            stdin=None,
            check=True,
        ):
            self.calls.append((tuple(argv), cwd))
            generated = cwd / "gen" / "python" / "cockpit" / "agent" / "v1"
            generated.mkdir(parents=True)
            (generated / "agent_pb2.py").write_bytes(b"# generated\n")
            (generated / "agent_pb2_grpc.py").write_bytes(
                b"# generated grpc\n"
            )
            return CommandResult(tuple(argv), 0, "", "")

    runner = CodegenRunner()
    artifact = build_release_artifact(
        repo=repo,
        output_root=tmp_path / "artifacts",
        plan=ReleasePlan(sha, sha, (), (), "ready"),
        services_digest="1" * 64,
        models_digest="2" * 64,
        codegen_runner=runner,
        codegen_executable="buf",
    )

    assert len(runner.calls) == 1
    assert runner.calls[0][0] == ("buf", "generate", "proto")
    assert runner.calls[0][1] != repo
    assert not (repo / "gen").exists()
    with tarfile.open(artifact.source_tar) as archive:
        names = archive.getnames()
        assert "gen/python/cockpit/agent/v1/agent_pb2.py" in names
        assert "gen/python/cockpit/agent/v1/agent_pb2_grpc.py" in names
        generated = archive.extractfile(
            "gen/python/cockpit/agent/v1/agent_pb2.py"
        )
        assert generated is not None
        assert generated.read() == b"# generated\n"


def test_build_release_artifact_rejects_missing_python_codegen(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    (repo / "buf.gen.yaml").write_text("version: v1\n", encoding="utf-8")
    proto = repo / "proto"
    proto.mkdir()
    (proto / "buf.yaml").write_text("version: v1\n", encoding="utf-8")
    git(repo, "add", "buf.gen.yaml", "proto")
    git(repo, "commit", "-m", "proto")
    sha = git(repo, "rev-parse", "HEAD")

    class EmptyCodegenRunner:
        def run(
            self,
            argv,
            *,
            cwd,
            env=None,
            stdin=None,
            check=True,
        ):
            return CommandResult(tuple(argv), 0, "", "")

    with pytest.raises(ReleaseError, match="Python protobuf output"):
        build_release_artifact(
            repo=repo,
            output_root=tmp_path / "artifacts",
            plan=ReleasePlan(sha, sha, (), (), "ready"),
            services_digest="1" * 64,
            models_digest="2" * 64,
            codegen_runner=EmptyCodegenRunner(),
            codegen_executable="buf",
        )


def test_build_release_artifact_reuses_identical_existing_artifact(
    tmp_path: Path,
):
    repo, sha = make_repo(tmp_path)
    kwargs = {
        "repo": repo,
        "output_root": tmp_path / "artifacts",
        "plan": ReleasePlan(sha, sha, (), (), "ready"),
        "services_digest": "1" * 64,
        "models_digest": "2" * 64,
    }

    first = build_release_artifact(**kwargs)
    second = build_release_artifact(**kwargs)

    assert second == first


def test_existing_artifact_rejects_changed_ci_cd_approval(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    first_plan = ReleasePlan(
        sha,
        sha,
        (),
        (),
        "ready",
        target_ci_cd_digest="a" * 64,
        approved_ci_cd_digest="a" * 64,
    )
    kwargs = {
        "repo": repo,
        "output_root": tmp_path / "artifacts",
        "services_digest": "1" * 64,
        "models_digest": "2" * 64,
    }
    build_release_artifact(plan=first_plan, **kwargs)

    changed_approval = ReleasePlan(
        sha,
        sha,
        (),
        (),
        "ready",
        target_ci_cd_digest="a" * 64,
        approved_ci_cd_digest="b" * 64,
    )
    with pytest.raises(ReleaseError, match="artifact exists but does not match"):
        build_release_artifact(plan=changed_approval, **kwargs)


def test_existing_mismatched_artifact_is_never_overwritten(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    directory = tmp_path / "artifacts" / sha
    directory.mkdir(parents=True)
    manifest = directory / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(ReleaseError, match="artifact exists but does not match"):
        build_release_artifact(
            repo=repo,
            output_root=tmp_path / "artifacts",
            plan=ReleasePlan(sha, sha, (), (), "ready"),
            services_digest="1" * 64,
            models_digest="2" * 64,
        )

    assert manifest.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "tamper",
    ("manifest_sha", "source_checksum", "transport_traversal"),
)
def test_existing_artifact_rejects_precise_integrity_counterexamples(
    tmp_path: Path,
    tamper: str,
):
    repo, sha = make_repo(tmp_path)
    kwargs = {
        "repo": repo,
        "output_root": tmp_path / "artifacts",
        "plan": ReleasePlan(sha, sha, (), (), "ready"),
        "services_digest": "1" * 64,
        "models_digest": "2" * 64,
    }
    artifact = build_release_artifact(**kwargs)
    if tamper == "manifest_sha":
        payload = json.loads(artifact.manifest.read_text(encoding="utf-8"))
        payload["target_sha"] = "f" * 40
        artifact.manifest.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "source_checksum":
        lines = artifact.checksums.read_text(encoding="utf-8").splitlines()
        lines[0] = "0" * 64 + "  source.tar"
        artifact.checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        with tarfile.open(artifact.transport_tar, mode="w:") as archive:
            info = tarfile.TarInfo("../outside")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(ReleaseError, match="artifact exists but does not match"):
        build_release_artifact(**kwargs)


@pytest.mark.parametrize(
    "member",
    [
        ".env",
        "secrets/client.pem",
        "certs/server.key",
        ".artifacts/cloud.env",
        "../outside.txt",
        "/absolute.txt",
    ],
)
def test_archive_secret_path_scanner_rejects_sensitive_members(member: str):
    with pytest.raises(ReleaseError, match="forbidden archive member"):
        validate_archive_member_names([member])


def test_archive_secret_path_scanner_allows_env_example():
    validate_archive_member_names([".env.example", "deploy/cloud/README.md"])


def test_text_secret_scanner_rejects_private_key():
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        + "A" * 64
        + "\n-----END PRIVATE KEY-----\n"
    )
    with pytest.raises(ReleaseError, match="private key material"):
        validate_text_payload(private_key)


def test_secret_scanners_mark_sensitive_sources_as_safety():
    for member in ("keys/production.pem", ".env", "../outside.txt"):
        with pytest.raises(ReleaseError) as path_error:
            validate_archive_member_names([member])
        assert path_error.value.category == "safety"
    private_key = "-----BEGIN PRIVATE KEY-----\n" + "A" * 64 + "\n-----END PRIVATE KEY-----\n"
    with pytest.raises(ReleaseError) as body_error:
        validate_text_payload(private_key)
    assert body_error.value.category == "safety"


def test_cloud_release_cli_help_and_configuration_error_subprocess_contract():
    script = ROOT / "scripts" / "cloud_release.py"
    assert script.stat().st_size > 2
    assert "def main(" in script.read_text(encoding="utf-8")
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert help_result.returncode == 0
    assert all(action in help_result.stdout for action in ("plan", "deploy", "verify", "rollback"))
    missing = subprocess.run(
        [sys.executable, str(script), "deploy"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert missing.returncode == 2
    assert json.loads(missing.stdout) == {
        "status": "error",
        "error_category": "configuration",
    }
    assert "operation failed" in missing.stderr


def test_cloud_release_ci_cd_approval_flag_is_plan_deploy_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    approval = "d" * 64
    monkeypatch.setenv("CAR_AGENT_APPROVED_CI_CD_SHA256", "e" * 64)
    parser = cloud_release.build_parser()

    plan_args = parser.parse_args(
        ["plan", "--sha", "a" * 40, "--approve-ci-cd-sha256", approval]
    )
    deploy_args = parser.parse_args(
        ["deploy", "--approve-ci-cd-sha256", approval]
    )
    assert plan_args.approve_ci_cd_sha256 == approval
    assert deploy_args.approve_ci_cd_sha256 == approval

    identity = tmp_path / "identity"
    identity.write_text("test-only identity", encoding="utf-8")
    request = cloud_release._request(
        tmp_path,
        plan_args,
        SshConfig("demo.example", "ubuntu", identity),
    )
    assert request.approved_ci_cd_digest == approval

    no_flag_args = parser.parse_args(["plan"])
    assert no_flag_args.approve_ci_cd_sha256 is None
    for argv in (
        ["verify", "--approve-ci-cd-sha256", approval],
        ["rollback", "--to", "a" * 7, "--approve-ci-cd-sha256", approval],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_cloud_release_result_payload_audits_ci_cd_digests():
    target_digest = "c" * 64
    approved_digest = "d" * 64
    result = CloudReleaseResult(
        status="plan_rejected",
        plan=ReleasePlan(
            "a" * 40,
            "b" * 40,
            (".github/workflows/ci.yml",),
            (ControlledChange(".github/workflows/ci.yml", "ci_cd"),),
            "plan_rejected",
            target_ci_cd_digest=target_digest,
            approved_ci_cd_digest=approved_digest,
        ),
        artifact=None,
        remote_state=RemoteState(
            current_release="a" * 40,
            current_path=f"/opt/car-agent/releases/{'a' * 40}",
            runtime_project_name="4c1f479",
            approved_infrastructure_digest=None,
            disk_available_bytes=100 * 1024**3,
            memory_available_bytes=5 * 1024**3,
            release_lock_available=True,
            runtime_project_ready=True,
            shared_scripts_ready=True,
            shared_models_ready=True,
        ),
    )

    payload = cloud_release._result_payload(result)

    assert payload["target_ci_cd_sha256"] == target_digest
    assert payload["approved_ci_cd_sha256"] == approved_digest


@pytest.mark.parametrize("command", ("verify", "rollback"))
@pytest.mark.parametrize("identity_kind", ("missing", "directory"))
def test_cloud_release_connection_actions_reject_unusable_identity_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    identity_kind: str,
):
    calls: list[object] = []

    class NoSshRunner:
        def run(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("SSH must not run with an invalid identity")

    identity = tmp_path / "identity"
    argv = ["--host", "dev.example"]
    if identity_kind == "directory":
        identity.mkdir()
        argv.extend(["--identity", str(identity)])
    if command == "rollback":
        argv.extend(["rollback", "--to", "a" * 7, "--apply"])
    else:
        argv.append("verify")

    monkeypatch.setattr(cloud_release, "SubprocessRunner", NoSshRunner)
    assert cloud_release.main(argv) == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "error_category": "configuration",
    }


def test_cloud_release_verify_emits_the_verified_full_release_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    identity = tmp_path / "identity"
    identity.write_text("test-only identity", encoding="utf-8")

    class FakeVerifyRunner:
        def run(self, argv, **kwargs):
            return CommandResult(tuple(argv), 0, "", "")

    monkeypatch.setattr(cloud_release, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cloud_release, "SubprocessRunner", FakeVerifyRunner)
    monkeypatch.setattr(
        cloud_release,
        "discover_remote_state",
        lambda request, runner: SimpleNamespace(current_release="abc1234"),
        raising=False,
    )
    monkeypatch.setattr(
        cloud_release,
        "_resolve_commit",
        lambda repo, revision: "a" * 40,
        raising=False,
    )

    assert cloud_release.main([
        "--host", "demo.example", "--identity", str(identity), "verify",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "verified",
        "release_sha": "a" * 40,
    }


def test_cloud_release_rollback_dry_run_does_not_require_connection(
    capsys: pytest.CaptureFixture[str],
):
    assert cloud_release.main(["rollback", "--to", "a" * 7]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"

def test_cloud_release_emit_enforces_the_child_json_size_limit():
    with pytest.raises(ReleaseError) as caught:
        cloud_release._emit({"status": "dry_run", "padding": "x" * (64 * 1024)})
    assert caught.value.category == "runtime"

def test_cloud_release_main_emits_configuration_category_for_invalid_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    repo, _ = make_repo(tmp_path)
    identity = repo / "identity"
    identity.write_text("not-a-real-key", encoding="utf-8")
    monkeypatch.setattr(cloud_release, "REPO_ROOT", repo)
    monkeypatch.setattr(
        cloud_release,
        "execute_deploy",
        lambda request, **kwargs: cloud_release_lib._resolve_commit(
            request.repo, request.revision
        ),
    )
    assert cloud_release.main(
        [
            "--host", "dev.example", "--identity", str(identity),
            "deploy", "--sha", "not-a-revision",
        ]
    ) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "error_category": "configuration",
    }

def test_text_secret_scanner_allows_lowercase_program_variables():
    validate_text_payload(
        "token = secrets.token_urlsafe(24)\n"
        "secret = decode_secret(raw_secret)\n"
        "password = request.get('password')\n"
        "api_key = configured_api_key\n",
        source_path="service.py",
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "TOKEN=ordinary-sensitive-value",
        "PASSWORD='ordinary-sensitive-value'",
        'API_KEY="ordinary-sensitive-value"',
        "ACCESS_TOKEN=ordinary-sensitive-value",
        "SECRET=ordinary-sensitive-value",
    ],
)
def test_text_secret_scanner_rejects_uppercase_credential_assignments(
    assignment: str,
):
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(assignment + "\n")


@pytest.mark.parametrize(
    "assignment",
    [
        'token = "prod-secret-1234567890"',
        "api_key = 'sk-prod-12345678901234567890'",
        'password = "prod-password-1234567890"',
    ],
)
def test_text_secret_scanner_rejects_lowercase_python_literal_credentials(
    assignment: str,
):
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(assignment + "\n", source_path="service.py")


def test_text_secret_scanner_rejects_lowercase_unquoted_env_literal():
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(
            "password=prod-secret-1234567890\n",
            source_path=".env.example",
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "TOKEN=prod-contest-secret-1234567890",
        "API_KEY=sk-test-livevalue-1234567890",
        "SECRET=production-mockery-1234567890",
    ],
)
def test_text_secret_scanner_rejects_placeholder_substring_bypasses(
    assignment: str,
):
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(assignment + "\n")


@pytest.mark.parametrize(
    "assignment",
    [
        "TOKEN=${UPSTREAM_ACCESS_TOKEN}",
        "PASSWORD=changeme",
        "SECRET=your_secret_value",
    ],
)
def test_text_secret_scanner_allows_strict_placeholders(assignment: str):
    validate_text_payload(assignment + "\n")


def test_text_secret_scanner_allows_explicit_synthetic_test_fixture():
    validate_text_payload(
        'token = "synthetic-sensitive-value"  # release-secret-fixture\n',
        source_path="tests/test_service.py",
    )


@pytest.mark.parametrize(
    "source_path",
    [
        "service.py",
        "app/test_credentials.py",
    ],
)
def test_text_secret_scanner_rejects_fixture_marker_in_production_source(
    source_path: str,
):
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(
            'token = "prod-sensitive-value"  # release-secret-fixture\n',
            source_path=source_path,
        )


def test_text_secret_scanner_rejects_fixture_marker_in_non_python_text():
    with pytest.raises(ReleaseError, match="credential-like assignment"):
        validate_text_payload(
            "TOKEN=prod-sensitive-value # release-secret-fixture\n",
            source_path=".env.example",
        )


def test_build_release_artifact_rejects_committed_env(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    (repo / ".env").write_text("TOKEN=real-secret-value\n", encoding="utf-8")
    git(repo, "add", "-f", ".env")
    git(repo, "commit", "-m", "bad env")
    sha = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ReleaseError, match="forbidden archive member"):
        build_release_artifact(
            repo=repo,
            output_root=tmp_path / "artifacts",
            plan=ReleasePlan(sha, sha, (), (), "ready"),
            services_digest="1" * 64,
            models_digest="2" * 64,
        )


class FakeRunner:
    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        return self.responses.pop(0)


def command_result(stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(("fake",), returncode, stdout, "")


def make_deploy_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "deploy-repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Cloud Release Test")
    git(repo, "config", "user.email", "cloud-release@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    cloud = repo / "deploy" / "cloud"
    cloud.mkdir(parents=True)
    (cloud / "release-services.json").write_text(
        '{"schema_version":1,"services":[]}\n',
        encoding="utf-8",
    )
    (cloud / "runtime-models.json").write_text(
        '{"schema_version":1,"models":[]}\n',
        encoding="utf-8",
    )
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    git(repo, "add", "deploy/cloud", "app.py")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("print('two')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "application")
    target = git(repo, "rev-parse", "HEAD")
    return repo, base, target


def remote_state_payload(
    current_release: str,
    *,
    approved_digest: str,
) -> str:
    return json.dumps(
        {
            "current_release": current_release,
            "current_path": f"/opt/car-agent/releases/{current_release}",
            "runtime_project_name": "4c1f479",
            "approved_infrastructure_digest": approved_digest,
            "disk_available_bytes": 100 * 1024**3,
            "memory_available_bytes": 5 * 1024**3,
            "release_lock_available": True,
            "runtime_project_ready": True,
            "shared_scripts_ready": True,
            "shared_models_ready": True,
        },
        separators=(",", ":"),
    ) + "\n"


def make_release_request(
    tmp_path: Path,
    *,
    with_ci_cd_change: bool = False,
) -> tuple[ReleaseRequest, str]:
    repo, base, target = make_deploy_repo(tmp_path)
    if with_ci_cd_change:
        workflows = repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "name: ci\non: push\njobs: {}\n",
            encoding="utf-8",
        )
        git(repo, "add", ".github/workflows/ci.yml")
        git(repo, "commit", "-m", "ci workflow")
        target = git(repo, "rev-parse", "HEAD")
    identity = tmp_path / "agent.pem"
    identity.write_text("not-a-real-key\n", encoding="utf-8")
    request = ReleaseRequest(
        repo=repo,
        revision=target,
        artifact_root=tmp_path / "artifacts",
        ssh=SshConfig(
            host="server.example.invalid",
            user="ubuntu",
            identity=identity,
            kex_algorithms="curve25519-sha256",
        ),
    )
    return request, base


def test_parse_remote_state_accepts_strict_valid_json():
    digest = "a" * 64
    state = parse_remote_state(remote_state_payload("4c1f479", approved_digest=digest))
    assert state == RemoteState(
        current_release="4c1f479",
        current_path="/opt/car-agent/releases/4c1f479",
        runtime_project_name="4c1f479",
        approved_infrastructure_digest=digest,
        disk_available_bytes=100 * 1024**3,
        memory_available_bytes=5 * 1024**3,
        release_lock_available=True,
        runtime_project_ready=True,
        shared_scripts_ready=True,
        shared_models_ready=True,
    )


def test_preflight_reports_exact_bootstrap_candidates():
    state = RemoteState(
        current_release="4c1f479",
        current_path="/opt/car-agent/releases/4c1f479",
        disk_available_bytes=109_521_666_048,
        memory_available_bytes=5_798_205_849,
        release_lock_available=True,
        runtime_project_name="4c1f479",
        runtime_project_ready=False,
        approved_infrastructure_digest=None,
        shared_scripts_ready=False,
        shared_models_ready=False,
    )

    report = make_bootstrap_report(state)

    assert report.status == "bootstrap_required"
    assert report.candidates == (
        "/opt/car-agent/shared/runtime-project-name",
        "/opt/car-agent/shared/release-infrastructure.json",
        "/opt/car-agent/shared/bin/transaction-lock.sh",
        "/opt/car-agent/shared/bin/remote-e2e-lock.sh",
        "/opt/car-agent/shared/bin/remote-data-migration.sh",
        "/opt/car-agent/shared/bin/backup.sh",
        "/opt/car-agent/shared/bin/remote-release.sh",
        "/opt/car-agent/shared/bin/remote-build.sh",
            "/opt/car-agent/shared/bin/activate-release.sh",
            "/opt/car-agent/shared/bin/verify-release.sh",
            "/opt/car-agent/shared/bin/redis_volume_prepare.py",
            "/opt/car-agent/shared/bin/collector_volume_replace.py",
            "/opt/car-agent/shared/models/nlu/edge_nlu.onnx",
        "/opt/car-agent/shared/models/nlu/labels.json",
        "/opt/car-agent/shared/models/nlu/vocab.json",
        "/opt/car-agent/shared/models/voiceprint/campplus_zh-cn_16k-common.onnx",
        "/opt/car-agent/shared/models/hmi/public/models/silero_vad.onnx",
        "/opt/car-agent/shared/models/hmi/public/kws/sherpa-onnx-kws.js",
        "/opt/car-agent/shared/models/hmi/public/kws/sherpa-onnx-wasm-kws-main.data",
        "/opt/car-agent/shared/models/hmi/public/kws/sherpa-onnx-wasm-kws-main.js",
        "/opt/car-agent/shared/models/hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm",
    )
    assert report.source_release == "/opt/car-agent/releases/4c1f479"
    assert all(item.owner == "root:root" for item in report.details)
    model_details = [item for item in report.details if "/models/" in item.path]
    assert all(item.sha256 and len(item.sha256) == 64 for item in model_details)
    client_details = [
        item for item in model_details if "/models/hmi/public/" in item.path
    ]
    assert [item.source for item in client_details] == [
        "approved local asset:hmi/public/models/silero_vad.onnx",
        "approved local asset:hmi/public/kws/sherpa-onnx-kws.js",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.data",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.js",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm",
    ]


def test_bootstrap_requires_all_shared_transaction_scripts():
    assert "transaction-lock.sh" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "backup.sh" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "/opt/car-agent/shared/bin/transaction-lock.sh" in REMOTE_PREFLIGHT_SOURCE


def test_bootstrap_requires_remote_data_migration_script():
    assert "remote-data-migration.sh" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "/opt/car-agent/shared/bin/remote-data-migration.sh" in REMOTE_PREFLIGHT_SOURCE
    assert "remote-e2e-lock.sh" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "redis_volume_prepare.py" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "collector_volume_replace.py" in cloud_release_lib.SHARED_SCRIPT_NAMES
    assert "/opt/car-agent/shared/bin/remote-e2e-lock.sh" in REMOTE_PREFLIGHT_SOURCE


def _working_bash() -> str | None:
    """返回一个**真能跑**的 bash 绝对路径；找不到返回 None。

    ⚠ 不能按「`which bash` 找得到」判定：Windows 的 `C:\\WINDOWS\\system32\\bash.EXE`
    是 WSL 启动器存根，`which` 找得到、跑起来直接报 WSL 错；而且 `CreateProcess`
    在搜 PATH **之前**先命中 system32，往 PATH 前面插 Git Bash 也压不住它
    ——所以只能拿绝对路径调。
    """
    candidates = [shutil.which("bash")]
    candidates += [
        Path(os.environ.get("PROGRAMFILES", "")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Git" / "bin" / "bash.exe",
    ]
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).resolve().parents[1] / "bin" / "bash.exe")
    for candidate in candidates:
        if not candidate:
            continue
        executable = str(candidate)
        if not Path(executable).is_file():
            continue
        try:
            if subprocess.run([executable, "-c", ":"], capture_output=True).returncode == 0:
                return executable
        except OSError:
            continue
    return None


def _load_remote_scripts_ready(script_paths, *, bash: str, calls: list):
    """从下发到服务器的那段源码里**抽出 `scripts_ready` 本体**跑。

    整段不能直接 exec——它是「下发即执行」的脚本，跑到一半就会去
    `readlink -f /opt/car-agent/current`。抽函数体的做法与 `test_cloud_deploy_assets.py`
    抽 shell 函数体同形，且仍然绑在**真实源码文本**上：函数一改，本用例立刻看得见。

    `subprocess` 换成把裸 `bash` 改写成绝对路径的薄壳，并记录每次调用
    ——**记录本身就是断言材料**：`.py` 探针不该出现在任何一次 bash 调用里。
    """
    body = re.search(
        r"(?ms)^def scripts_ready\(\):.*?(?=^\S|\Z)", REMOTE_PREFLIGHT_SOURCE,
    )
    assert body is not None, "远端预检里找不到 scripts_ready，本用例已经失去锚点"

    class _Subprocess:
        DEVNULL = subprocess.DEVNULL

        @staticmethod
        def run(argv, **kwargs):
            resolved = [bash if argv[0] == "bash" else argv[0], *argv[1:]]
            calls.append(tuple(resolved))
            return subprocess.run(resolved, **kwargs)

    namespace: dict[str, object] = {
        "Path": Path,
        "subprocess": _Subprocess,
        "SCRIPTS": tuple(script_paths),
        "secure_regular": lambda path: path.is_file(),
    }
    exec(compile(body.group(0), "<remote-preflight:scripts_ready>", "exec"), namespace)
    return namespace["scripts_ready"]


def test_shared_scripts_readiness_checks_syntax_per_file_type(tmp_path, monkeypatch):
    """共享底座里既有 .sh 也有 .py，语法自检必须按类型走（否则这个闸恒假）。

    2026-08-17 `49b0788` 把 `redis_volume_prepare.py` / `collector_volume_replace.py`
    加进 `SCRIPTS`，而 `scripts_ready()` 仍对每个文件跑 `bash -n`——Python 源在 bash
    眼里是语法错 ⇒ **`shared_scripts_ready` 从此永远为 False**、
    `cloud_release.py plan/deploy` 永远报 `bootstrap_required`。
    那之后没人发过版（数据迁移走另一条工具链），所以这个恒假闸一直没被发现。
    > 判据：**一个永远不可能满足的前置条件，和没有这个前置条件一样糟。**
    """
    bash = _working_bash()
    if bash is None:
        pytest.skip("bash is unavailable; shared-script syntax checking cannot be exercised")

    shell = tmp_path / "remote-data-migration.sh"
    shell.write_text("#!/usr/bin/env bash\nset -euo pipefail\nmain() { :; }\n", encoding="utf-8")
    python_helper = tmp_path / "redis_volume_prepare.py"
    python_helper.write_text(
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "def prepare(path: str) -> str:\n"
        "    return path\n",
        encoding="utf-8",
    )
    # 前提自检：这个 .py 确实是 bash 通不过的，否则本用例什么都没验到
    assert subprocess.run(
        [bash, "-n", str(python_helper)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode != 0

    calls: list = []
    scripts_ready = _load_remote_scripts_ready([shell, python_helper], bash=bash, calls=calls)
    assert scripts_ready() is True
    # 直接钉住「.py 不许走 bash」——不靠语法结果间接推断
    assert not any(str(python_helper) in call for call in calls)
    assert any(str(shell) in call for call in calls)

    python_helper.write_text("def broken(:\n", encoding="utf-8")      # Python 语法错必须红
    assert scripts_ready() is False

    python_helper.write_text("value = 1\n", encoding="utf-8")
    shell.write_text("if true; then\n", encoding="utf-8")             # bash 语法错必须红
    assert scripts_ready() is False


def test_runtime_model_hash_tables_stay_in_sync():
    manifest = json.loads(
        (ROOT / "deploy/cloud/runtime-models.json").read_text(encoding="utf-8")
    )
    expected = {
        item["path"]: item["sha256"]
        for item in manifest["models"]
    }
    assert len(expected) == len(manifest["models"])

    bootstrap = {
        relative: digest
        for relative, digest, _approved_source in MODEL_BOOTSTRAP_FILES
    }
    assert len(bootstrap) == len(MODEL_BOOTSTRAP_FILES)

    preflight_pairs = re.findall(
        r'SHARED / "([^"]+)": "([0-9a-f]{64})"',
        REMOTE_PREFLIGHT_SOURCE,
    )
    preflight = dict(preflight_pairs)
    assert len(preflight) == len(preflight_pairs)

    assert bootstrap == expected
    assert preflight == expected


def test_inline_remote_preflight_is_read_only_and_does_not_read_runtime_env():
    assert REMOTE_PREFLIGHT_COMMAND.startswith("sudo python3 -c ")
    assert "/opt/car-agent/shared/.env" not in REMOTE_PREFLIGHT_SOURCE
    lowered = REMOTE_PREFLIGHT_SOURCE.lower()
    for forbidden in (
        "mkdir",
        "install ",
        "shutil.copy",
        "write_text",
        "write_bytes",
        "os.remove",
        "os.unlink",
        "subprocess.run([\"sudo\"",
    ):
        assert forbidden not in lowered
    for required in (
        "readlink",
        "df",
        "/proc/meminfo",
        "docker",
        "sha256",
        "release-infrastructure.json",
        "runtime-project-name",
    ):
        assert required in REMOTE_PREFLIGHT_SOURCE


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        json.dumps(
            {
                **json.loads(remote_state_payload("4c1f479", approved_digest="a" * 64)),
                "unexpected": True,
            }
        ),
        remote_state_payload("../outside", approved_digest="a" * 64),
    ],
)
def test_parse_remote_state_rejects_invalid_or_extra_data(payload: str):
    with pytest.raises(ReleaseError, match="remote preflight"):
        parse_remote_state(payload)


def test_deploy_without_apply_never_calls_remote_mutation(tmp_path: Path):
    request, base = make_release_request(tmp_path)
    digest = compute_infrastructure_digest(request.repo, request.revision)
    runner = FakeRunner(
        [command_result(remote_state_payload(base, approved_digest=digest))]
    )

    result = execute_deploy(request, apply=False, runner=runner)

    assert isinstance(result, CloudReleaseResult)
    assert result.status == "dry_run"
    assert result.artifact is not None
    assert len(runner.calls) == 1
    assert all(
        "remote-release.sh" not in " ".join(call)
        for call in runner.calls
    )


def test_ci_cd_change_without_request_approval_stops_before_artifact_or_write(
    tmp_path: Path,
):
    request, base = make_release_request(tmp_path, with_ci_cd_change=True)
    infrastructure_digest = compute_infrastructure_digest(
        request.repo,
        request.revision,
    )
    runner = FakeRunner(
        [
            command_result(
                remote_state_payload(
                    base,
                    approved_digest=infrastructure_digest,
                )
            )
        ]
    )

    result = execute_deploy(request, apply=False, runner=runner)

    assert result.status == "plan_rejected"
    assert result.artifact is None
    assert result.plan.target_ci_cd_digest == compute_ci_cd_digest(
        request.repo,
        request.revision,
    )
    assert result.plan.approved_ci_cd_digest is None
    assert len(runner.calls) == 1
    assert not request.artifact_root.exists()
    assert all(
        "remote-release.sh" not in " ".join(call)
        for call in runner.calls
    )


def test_exact_ci_cd_request_approval_is_audited_in_dry_run_artifact(
    tmp_path: Path,
):
    initial_request, base = make_release_request(
        tmp_path,
        with_ci_cd_change=True,
    )
    approval = compute_ci_cd_digest(
        initial_request.repo,
        initial_request.revision,
    )
    assert approval is not None
    request = ReleaseRequest(
        repo=initial_request.repo,
        revision=initial_request.revision,
        artifact_root=initial_request.artifact_root,
        ssh=initial_request.ssh,
        approved_ci_cd_digest=approval,
    )
    infrastructure_digest = compute_infrastructure_digest(
        request.repo,
        request.revision,
    )
    runner = FakeRunner(
        [
            command_result(
                remote_state_payload(
                    base,
                    approved_digest=infrastructure_digest,
                )
            )
        ]
    )

    result = execute_deploy(request, apply=False, runner=runner)

    assert result.status == "dry_run"
    assert result.plan.target_ci_cd_digest == approval
    assert result.plan.approved_ci_cd_digest == approval
    assert result.artifact is not None
    manifest = json.loads(result.artifact.manifest.read_text(encoding="utf-8"))
    assert manifest["target_ci_cd_sha256"] == approval
    assert manifest["approved_ci_cd_sha256"] == approval
    assert len(runner.calls) == 1


def test_deploy_stops_when_remote_bootstrap_is_incomplete(tmp_path: Path):
    request, base = make_release_request(tmp_path)
    digest = compute_infrastructure_digest(request.repo, request.revision)
    payload = json.loads(remote_state_payload(base, approved_digest=digest))
    payload["shared_scripts_ready"] = False
    runner = FakeRunner([command_result(json.dumps(payload))])

    result = execute_deploy(request, apply=False, runner=runner)

    assert result.status == "bootstrap_required"
    assert result.artifact is None
    assert len(runner.calls) == 1


def test_apply_prepares_upload_scps_once_and_deploys_through_entrypoint(
    tmp_path: Path,
):
    request, base = make_release_request(tmp_path)
    digest = compute_infrastructure_digest(request.repo, request.revision)
    incoming = (
        "/opt/car-agent/incoming/releases/"
        f"{request.revision}-{'f' * 32}"
    )
    runner = FakeRunner(
        [
            command_result(remote_state_payload(base, approved_digest=digest)),
            command_result(incoming + "\n"),
            command_result(),
            command_result(),
            command_result(),
        ]
    )

    result = execute_deploy(
        request,
        apply=True,
        runner=runner,
        nonce_factory=lambda: "f" * 32,
    )

    assert result.status == "submitted"
    joined = [" ".join(call) for call in runner.calls]
    assert sum("remote-release.sh prepare-upload" in call for call in joined) == 1
    assert sum(call.startswith("scp ") for call in joined) == 1
    assert sum("chmod 0600 --" in call for call in joined) == 1
    assert sum("remote-release.sh deploy" in call for call in joined) == 1
    assert all(
        "sudo /opt/car-agent/shared/bin/remote-release.sh" in call
        for call in joined
        if "remote-release.sh" in call
    )


def test_apply_rejects_prepare_upload_path_outside_expected_directory(
    tmp_path: Path,
):
    request, base = make_release_request(tmp_path)
    digest = compute_infrastructure_digest(request.repo, request.revision)
    runner = FakeRunner(
        [
            command_result(remote_state_payload(base, approved_digest=digest)),
            command_result("/tmp/attacker-controlled\n"),
        ]
    )

    with pytest.raises(ReleaseError, match="unexpected upload directory"):
        execute_deploy(
            request,
            apply=True,
            runner=runner,
            nonce_factory=lambda: "f" * 32,
        )
    assert all(not call[0].startswith("scp") for call in runner.calls)

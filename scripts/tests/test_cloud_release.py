from __future__ import annotations

import json
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.cloud_release_lib import (
    ControlledChange,
    CommandResult,
    CloudReleaseResult,
    ReleaseRequest,
    ReleaseArtifact,
    ReleasePlan,
    ReleaseError,
    RemoteState,
    REMOTE_PREFLIGHT_COMMAND,
    REMOTE_PREFLIGHT_SOURCE,
    SshConfig,
    SubprocessRunner,
    build_release_artifact,
    classify_changed_path,
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


def make_release_request(tmp_path: Path) -> tuple[ReleaseRequest, str]:
    repo, base, target = make_deploy_repo(tmp_path)
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
        "/opt/car-agent/shared/bin/backup.sh",
        "/opt/car-agent/shared/bin/remote-release.sh",
        "/opt/car-agent/shared/bin/remote-build.sh",
        "/opt/car-agent/shared/bin/activate-release.sh",
        "/opt/car-agent/shared/bin/verify-release.sh",
        "/opt/car-agent/shared/models/nlu/edge_nlu.onnx",
        "/opt/car-agent/shared/models/nlu/labels.json",
        "/opt/car-agent/shared/models/nlu/vocab.json",
        "/opt/car-agent/shared/models/voiceprint/campplus_zh-cn_16k-common.onnx",
    )
    assert report.source_release == "/opt/car-agent/releases/4c1f479"
    assert all(item.owner == "root:root" for item in report.details)
    model_details = [item for item in report.details if "/models/" in item.path]
    assert all(item.sha256 and len(item.sha256) == 64 for item in model_details)


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

"""发布闸对 compose 文件的一次性批准（2026-10-07）。

此前 `compose.yaml` / `deploy/docker-compose.yaml` 归在 infrastructure 下，而基础设施批准锚的摘要只算 deploy/cloud/**：
只改 compose 时目标摘要等于已批准摘要，闸直接放行。现在它们单列 `compose` 一类，不带批准摘要就硬拦；
摘要绑定每个 compose 文件已部署与目标两侧的内容，只放行审过的这一次变更，不落到远端。
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
    classify_changed_path,
    compose_paths,
    compute_compose_digest,
    make_release_plan,
)
from scripts.dev_stack_lib import cloud_release_argv

DEPLOYED = "4c1f479513c8b13564803ba43555a470aacbf640"
TARGET = "c" * 40
COMPOSE = "deploy/docker-compose.yaml"


def plan(paths, *, target=None, approved=None):
    return make_release_plan(deployed_sha=DEPLOYED, target_sha=TARGET, changed_paths=paths,
                             diff_by_path={path: "+changed\n" for path in paths},
                             target_compose_digest=target, approved_compose_digest=approved)


def test_compose_files_are_their_own_category():
    assert classify_changed_path("compose.yaml") == "compose"
    assert classify_changed_path(COMPOSE) == "compose"
    # deploy/cloud 下的覆盖文件装在主机侧、由基础设施批准锚绑定，不变
    assert classify_changed_path("deploy/cloud/compose.cloud.yaml") == "infrastructure"
    assert compose_paths(["agents/x.py", "deploy\\docker-compose.yaml", "compose.yaml"]) == ("compose.yaml", COMPOSE)


def test_an_unapproved_compose_change_is_blocked():
    rejected = plan([COMPOSE])
    assert rejected.status == "plan_rejected"
    assert rejected.blocking_changes == (ControlledChange(COMPOSE, "compose"),)
    stale = plan([COMPOSE], target="e" * 64, approved="d" * 64)
    assert stale.status == "plan_rejected" and stale.blocking_changes


def test_the_exact_digest_releases_the_compose_transition():
    approved = plan([COMPOSE, "compose.yaml", "agents/x.py"], target="d" * 64, approved="d" * 64)
    assert approved.status == "ready" and approved.blocking_changes == ()
    assert approved.target_compose_digest == approved.approved_compose_digest == "d" * 64


@pytest.mark.parametrize(("other", "category", "status"), [
    (".env.example", "runtime_config_contract", "plan_rejected"),
    (".github/workflows/ci.yml", "ci_cd", "plan_rejected"),
    ("memory/schema.sql", "database_schema", "plan_rejected"),
    ("deploy/cloud/remote-release.sh", "infrastructure", "bootstrap_required"),
])
def test_a_compose_digest_never_releases_another_category(other, category, status):
    result = plan([COMPOSE, other], target="d" * 64, approved="d" * 64)
    assert result.status == status
    assert result.blocking_changes == (ControlledChange(other, category),)


def test_an_unused_compose_approval_is_rejected():
    with pytest.raises(ReleaseError) as caught:
        plan(["agents/x.py"], approved="d" * 64)
    assert caught.value.category == "configuration"


@pytest.mark.parametrize("digest", ["", "ABC", "g" * 64, "A" * 64])
def test_malformed_compose_digests_are_configuration_errors(digest):
    for kwargs in ({"target": "d" * 64, "approved": digest}, {"target": digest, "approved": "d" * 64}):
        with pytest.raises(ReleaseError) as caught:
            plan([COMPOSE], **kwargs)
        assert caught.value.category == "configuration"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        path = repo / rel
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
    _git(path, "config", "user.name", "Compose Approval Test")
    _git(path, "config", "user.email", "compose-approval@example.invalid")
    _git(path, "config", "core.autocrlf", "false")
    return path


def test_the_digest_binds_the_exact_compose_transition(repo: Path):
    base = _commit(repo, {COMPOSE: "services: {}\n", "app.py": "x = 1\n"}, "base")
    target = _commit(repo, {COMPOSE: "services:\n  api:\n    environment: [TOKEN]\n"}, "pass a token")
    digest = compute_compose_digest(repo, base, target, [COMPOSE])
    assert digest and digest == compute_compose_digest(repo, base, target, [COMPOSE])
    unrelated = _commit(repo, {"app.py": "x = 2\n"}, "unrelated")
    assert compute_compose_digest(repo, base, unrelated, [COMPOSE]) == digest
    edited = _commit(repo, {COMPOSE: "services:\n  api:\n    privileged: true\n"}, "edit after review")
    assert compute_compose_digest(repo, base, edited, [COMPOSE]) != digest
    # 同一份目标内容、换一个已部署基线，是另一次变更
    assert compute_compose_digest(repo, base, edited, [COMPOSE]) != compute_compose_digest(repo, target, edited, [COMPOSE])
    assert compute_compose_digest(repo, base, target, []) is None


def test_the_flag_exists_for_plan_and_deploy_and_is_forwarded(tmp_path: Path):
    parser = cloud_release.build_parser()
    approval = "d" * 64
    for argv in (["plan", "--approve-compose-sha256", approval], ["deploy", "--approve-compose-sha256", approval]):
        args = parser.parse_args(argv)
        assert args.approve_compose_sha256 == approval
    identity = tmp_path / "identity"
    identity.write_text("test-only identity", encoding="utf-8")
    request = cloud_release._request(tmp_path, args, SshConfig("demo.example", "ubuntu", identity))
    assert request.approved_compose_digest == approval
    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "--approve-compose-sha256", approval])
    argv = cloud_release_argv(tmp_path, "deploy", "HEAD", apply=True, approved_compose_digest=approval)
    assert argv[-3:] == ["--approve-compose-sha256", approval, "--apply"]
    assert "--approve-compose-sha256" not in cloud_release_argv(tmp_path, "deploy", "HEAD", apply=False)


def test_the_payload_audits_both_compose_digests():
    result = CloudReleaseResult(
        status="dry_run",
        plan=ReleasePlan(DEPLOYED, TARGET, (COMPOSE,), (), "ready",
                         target_compose_digest="d" * 64, approved_compose_digest="d" * 64),
        artifact=None,
        remote_state=RemoteState(
            current_release=DEPLOYED, current_path=f"/opt/car-agent/releases/{DEPLOYED}",
            runtime_project_name="4c1f479", running_release_tags=(DEPLOYED,),
            approved_infrastructure_digest=None, disk_available_bytes=100 * 1024**3,
            memory_available_bytes=5 * 1024**3, release_lock_available=True,
            runtime_project_ready=True, shared_scripts_ready=True, shared_models_ready=True),
    )
    payload = cloud_release._result_payload(result)
    assert payload["target_compose_sha256"] == payload["approved_compose_sha256"] == "d" * 64

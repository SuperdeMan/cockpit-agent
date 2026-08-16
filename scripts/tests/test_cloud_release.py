from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.cloud_release_lib import (
    ReleaseError,
    SshConfig,
    SubprocessRunner,
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

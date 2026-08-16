from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SSH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
SSH_KEX_RE = re.compile(r"^[A-Za-z0-9@._+,-]+$")


class ReleaseError(RuntimeError):
    """A safe, user-facing cloud release error."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    def __init__(self, redactions: set[str] | None = None) -> None:
        self._redactions = {value for value in redactions or set() if value}

    def _redact(self, value: str) -> str:
        for secret in sorted(self._redactions, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        stdin: BinaryIO | None = None,
        check: bool = True,
    ) -> CommandResult:
        if not argv:
            raise ReleaseError("cannot run an empty command")
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise ReleaseError(
                f"could not run {argv[0]}: {type(exc).__name__}"
            ) from exc

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        result = CommandResult(
            tuple(argv),
            completed.returncode,
            self._redact(stdout),
            self._redact(stderr),
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise ReleaseError(
                f"command failed ({result.returncode}): {argv[0]}: {detail}"
            )
        return result


@dataclass(frozen=True)
class SshConfig:
    host: str
    user: str
    identity: Path
    kex_algorithms: str | None = None

    def __post_init__(self) -> None:
        if not SSH_HOST_RE.fullmatch(self.host):
            raise ReleaseError("invalid SSH host")
        if not SSH_USER_RE.fullmatch(self.user):
            raise ReleaseError("invalid SSH user")
        if self.kex_algorithms and not SSH_KEX_RE.fullmatch(self.kex_algorithms):
            raise ReleaseError("invalid SSH kex algorithms")

    def _common_options(self) -> list[str]:
        options = [
            "-i",
            str(self.identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=15",
        ]
        if self.kex_algorithms:
            options.extend(["-o", f"KexAlgorithms={self.kex_algorithms}"])
        return options

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def ssh_argv(self, remote_command: str) -> list[str]:
        if not remote_command or "\x00" in remote_command:
            raise ReleaseError("invalid SSH remote command")
        return ["ssh", *self._common_options(), self.target, remote_command]


def _git(repo: Path, *args: str, check: bool = True) -> CommandResult:
    return SubprocessRunner().run(["git", *args], cwd=repo, check=check)


def require_clean_main_commit(repo: Path, revision: str) -> str:
    dirty = _git(
        repo,
        "status",
        "--porcelain",
        "--untracked-files=normal",
    ).stdout
    if dirty:
        raise ReleaseError("worktree is not clean")

    sha = _git(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
    ).stdout.strip()
    if not FULL_SHA_RE.fullmatch(sha):
        raise ReleaseError("git did not return a full commit SHA")

    reachable = _git(
        repo,
        "merge-base",
        "--is-ancestor",
        sha,
        "refs/heads/main",
        check=False,
    )
    if reachable.returncode != 0:
        raise ReleaseError(f"commit {sha} is not reachable from main")
    return sha

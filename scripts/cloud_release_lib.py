from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tarfile
import tempfile
import tokenize
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SSH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
SSH_KEX_RE = re.compile(r"^[A-Za-z0-9@._+,-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_SELECTOR_RE = re.compile(r"^[0-9a-f]{7,40}$")
UPLOAD_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
RUNTIME_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DDL_RE = re.compile(
    r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\s+"
    r"(?:TABLE|INDEX|TYPE|SCHEMA)\b",
    re.IGNORECASE,
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH )?PRIVATE KEY)-----\s+"
    r"[A-Za-z0-9+/=\r\n]{32,}\s+"
    r"-----END (?P=kind)-----",
)
CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|TOKEN|PASSWORD|SECRET)"
    r"\s*=\s*['\"]?(?P<value>[^\s#'\"]{16,})"
)
CREDENTIAL_NAME_RE = re.compile(
    r"^(?:API_?KEY|ACCESS_?TOKEN|TOKEN|PASSWORD|SECRET)$",
    re.IGNORECASE,
)
STRICT_PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"example|placeholder|changeme|mock|test|"
    r"your[_-][A-Za-z0-9_-]+|"
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"
    r")$",
    re.IGNORECASE,
)
SYNTHETIC_CREDENTIAL_FIXTURE_MARKER = "# release-secret-fixture"
CONTROLLED_EXACT = {
    "compose.yaml": "infrastructure",
    "deploy/docker-compose.yaml": "infrastructure",
    ".env.example": "runtime_config_contract",
    "memory/schema.sql": "database_schema",
    "registry/postgres_schema.sql": "database_schema",
    "proactive/schema.sql": "database_schema",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


class ReleaseError(RuntimeError):
    """A safe, user-facing cloud release error with a fixed machine category."""

    def __init__(self, message: str, *, category: str = "runtime") -> None:
        super().__init__(message)
        if category not in {"configuration", "safety", "runtime"}:
            raise ValueError("invalid release error category")
        self.category = category


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, order=True)
class ControlledChange:
    path: str
    category: str


@dataclass(frozen=True)
class ReleasePlan:
    deployed_sha: str
    target_sha: str
    changed_paths: tuple[str, ...]
    blocking_changes: tuple[ControlledChange, ...]
    status: str
    target_infrastructure_digest: str | None = None
    approved_infrastructure_digest: str | None = None


@dataclass(frozen=True)
class ReleaseArtifact:
    directory: Path
    source_tar: Path
    manifest: Path
    checksums: Path
    transport_tar: Path


@dataclass(frozen=True)
class RemoteState:
    current_release: str
    current_path: str
    runtime_project_name: str
    approved_infrastructure_digest: str | None
    disk_available_bytes: int
    memory_available_bytes: int
    release_lock_available: bool
    runtime_project_ready: bool
    shared_scripts_ready: bool
    shared_models_ready: bool


@dataclass(frozen=True)
class BootstrapCandidate:
    path: str
    source: str
    sha256: str | None
    mode: str
    owner: str = "root:root"


@dataclass(frozen=True)
class BootstrapReport:
    status: str
    source_release: str
    candidates: tuple[str, ...]
    details: tuple[BootstrapCandidate, ...]


@dataclass(frozen=True)
class ReleaseRequest:
    repo: Path
    revision: str
    artifact_root: Path
    ssh: SshConfig


@dataclass(frozen=True)
class CloudReleaseResult:
    status: str
    plan: ReleasePlan
    artifact: ReleaseArtifact | None
    remote_state: RemoteState


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        stdin: BinaryIO | None = None,
        check: bool = True,
        timeout_s: float | None = None,
    ) -> CommandResult:
        pass


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
        timeout_s: float | None = None,
    ) -> CommandResult:
        if not argv:
            raise ReleaseError("cannot run an empty command")
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise ReleaseError(
                f"could not run {argv[0]}: {type(exc).__name__}"
            ) from exc
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            process.communicate()
            raise ReleaseError(f"command timed out: {argv[0]}", category="runtime") from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        result = CommandResult(
            tuple(argv),
            process.returncode,
            self._redact(stdout),
            self._redact(stderr),
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise ReleaseError(
                f"command failed ({result.returncode}): {argv[0]}: {detail}"
            )
        return result


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the exact command process tree after a bounded stage timeout."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@dataclass(frozen=True)
class SshConfig:
    host: str
    user: str
    identity: Path
    kex_algorithms: str | None = None

    def __post_init__(self) -> None:
        if not SSH_HOST_RE.fullmatch(self.host):
            raise ReleaseError("invalid SSH host", category="configuration")
        if not SSH_USER_RE.fullmatch(self.user):
            raise ReleaseError("invalid SSH user", category="configuration")
        if self.kex_algorithms and not SSH_KEX_RE.fullmatch(self.kex_algorithms):
            raise ReleaseError("invalid SSH kex algorithms", category="configuration")

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
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=240",
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

    def scp_argv(self, source: Path, remote_path: str) -> list[str]:
        if (
            not remote_path.startswith("/opt/car-agent/")
            or "\x00" in remote_path
            or "\n" in remote_path
            or "\r" in remote_path
            or ".." in PurePosixPath(remote_path).parts
        ):
            raise ReleaseError("invalid SCP remote path")
        return [
            "scp",
            *self._common_options(),
            str(source),
            f"{self.target}:{remote_path}",
        ]


def validate_ssh_identity(identity: Path) -> None:
    if not identity.exists() or not identity.is_file():
        raise ReleaseError(
            "SSH identity must be an existing regular file",
            category="configuration",
        )


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
        raise ReleaseError("worktree is not clean", category="safety")

    resolved = _git(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        check=False,
    )
    sha = resolved.stdout.strip()
    if resolved.returncode != 0 or not FULL_SHA_RE.fullmatch(sha):
        raise ReleaseError(
            "could not resolve requested revision", category="configuration"
        )

    reachable = _git(
        repo,
        "merge-base",
        "--is-ancestor",
        sha,
        "refs/heads/main",
        check=False,
    )
    if reachable.returncode != 0:
        raise ReleaseError(f"commit {sha} is not reachable from main", category="safety")
    return sha


def classify_changed_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if (
        normalized == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith(SECRET_SUFFIXES)
    ):
        return "secret_material"
    if normalized in CONTROLLED_EXACT or normalized.endswith(".sql"):
        return CONTROLLED_EXACT.get(normalized, "database_schema")
    if normalized.startswith(".github/workflows/"):
        return "ci_cd"
    if (
        normalized.startswith("deploy/cloud/")
        and normalized != "deploy/cloud/README.md"
    ):
        return "infrastructure"
    return CONTROLLED_EXACT.get(normalized, "application")


def _is_nonproduction_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized.startswith(("docs/", "test/", "scripts/tests/"))
        or "/tests/" in normalized
        or normalized.endswith("_test.py")
        or normalized.startswith("test_")
    )


def diff_contains_schema_change(path: str, diff: str) -> bool:
    normalized = path.replace("\\", "/")
    if _is_nonproduction_path(normalized):
        return False
    if not normalized.endswith((".py", ".sql")):
        return False
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:].strip()
        if not added or added.startswith(("#", "--", "/*", "*")):
            continue
        if DDL_RE.search(added):
            return True
    return False


def make_release_plan(
    *,
    deployed_sha: str,
    target_sha: str,
    changed_paths: Sequence[str],
    diff_by_path: Mapping[str, str],
    target_infrastructure_digest: str | None = None,
    approved_infrastructure_digest: str | None = None,
) -> ReleasePlan:
    if not FULL_SHA_RE.fullmatch(target_sha):
        raise ReleaseError("target SHA must be a full commit SHA", category="configuration")
    if (
        target_infrastructure_digest is not None
        and not SHA256_RE.fullmatch(target_infrastructure_digest)
    ):
        raise ReleaseError("target infrastructure digest is invalid")

    infrastructure_approved = (
        target_infrastructure_digest is not None
        and approved_infrastructure_digest is not None
        and SHA256_RE.fullmatch(approved_infrastructure_digest) is not None
        and target_infrastructure_digest == approved_infrastructure_digest
    )
    blocking: set[ControlledChange] = set()
    normalized_paths = tuple(path.replace("\\", "/") for path in changed_paths)
    for path in normalized_paths:
        category = classify_changed_path(path)
        if category == "infrastructure" and infrastructure_approved:
            continue
        if category != "application":
            blocking.add(ControlledChange(path, category))
            continue
        if diff_contains_schema_change(path, diff_by_path.get(path, "")):
            blocking.add(ControlledChange(path, "database_schema"))

    ordered_blocking = tuple(sorted(blocking))
    if not ordered_blocking:
        status = "ready"
    elif all(item.category == "infrastructure" for item in ordered_blocking):
        status = "bootstrap_required"
    else:
        status = "plan_rejected"
    return ReleasePlan(
        deployed_sha=deployed_sha,
        target_sha=target_sha,
        changed_paths=normalized_paths,
        blocking_changes=ordered_blocking,
        status=status,
        target_infrastructure_digest=target_infrastructure_digest,
        approved_infrastructure_digest=approved_infrastructure_digest,
    )


def git_changes(
    repo: Path,
    base: str,
    target: str,
) -> tuple[list[str], dict[str, str]]:
    paths_result = _git(
        repo,
        "diff",
        "--name-only",
        "--diff-filter=ACMRTUXB",
        base,
        target,
    )
    changed = [line for line in paths_result.stdout.splitlines() if line]
    diffs = {
        path: _git(
            repo,
            "diff",
            "--unified=0",
            "--no-ext-diff",
            base,
            target,
            "--",
            path,
        ).stdout
        for path in changed
    }
    return changed, diffs


def _git_blob(repo: Path, revision: str, path: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ReleaseError(
            f"could not read committed infrastructure: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        raise ReleaseError(
            f"could not read committed infrastructure path: {path}"
        )
    return completed.stdout


def compute_infrastructure_digest(repo: Path, target_sha: str) -> str:
    if not FULL_SHA_RE.fullmatch(target_sha):
        raise ReleaseError("target SHA must be a full commit SHA", category="configuration")
    listing = _git(
        repo,
        "ls-tree",
        "-r",
        "--name-only",
        target_sha,
        "--",
        "deploy/cloud",
    ).stdout.splitlines()
    paths = sorted(
        path
        for path in listing
        if path and path != "deploy/cloud/README.md"
    )
    per_file = {
        path: hashlib.sha256(_git_blob(repo, target_sha, path)).hexdigest()
        for path in paths
    }
    canonical = json.dumps(
        per_file,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_archive_member_names(names: Sequence[str]) -> None:
    for raw_name in names:
        normalized = raw_name.replace("\\", "/")
        path = PurePosixPath(normalized)
        basename = path.name
        forbidden = (
            not normalized
            or normalized.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or ".artifacts" in path.parts
            or basename == ".env"
            or (
                basename.startswith(".env.")
                and basename != ".env.example"
            )
            or basename.endswith(SECRET_SUFFIXES)
        )
        if forbidden:
            raise ReleaseError(f"forbidden archive member: {raw_name}", category="safety")


def _is_strict_placeholder(value: str) -> bool:
    return STRICT_PLACEHOLDER_RE.fullmatch(value) is not None


def _validate_credential_assignment_text(text: str) -> None:
    for match in CREDENTIAL_ASSIGNMENT_RE.finditer(text):
        value = match.group("value")
        if _is_strict_placeholder(value):
            continue
        raise ReleaseError("credential-like assignment found in release source", category="safety")


def _credential_target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _literal_string(value: ast.expr) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr) and all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in value.values
    ):
        return "".join(str(item.value) for item in value.values)
    return None


def _validate_python_credential_literals(
    text: str,
    *,
    allow_fixture_markers: bool,
) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _validate_credential_assignment_text(text)
        return

    fixture_lines: set[int] = set()
    if allow_fixture_markers:
        try:
            fixture_lines = {
                token.start[0]
                for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT
                and token.string.strip()
                == SYNTHETIC_CREDENTIAL_FIXTURE_MARKER
            }
        except (IndentationError, tokenize.TokenError):
            fixture_lines = set()

    for node in ast.walk(tree):
        targets: tuple[ast.expr, ...] = ()
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
            value = node.value
        elif isinstance(node, ast.NamedExpr):
            targets = (node.target,)
            value = node.value

        literal = _literal_string(value) if value is not None else None
        if literal is not None and len(literal) >= 16:
            if any(
                (name := _credential_target_name(target)) is not None
                and CREDENTIAL_NAME_RE.fullmatch(name)
                for target in targets
            ) and not _is_strict_placeholder(literal) and (
                node.lineno not in fixture_lines
            ):
                raise ReleaseError(
                    "credential-like assignment found in release source",
                    category="safety",
                )


def validate_text_payload(
    text: str,
    *,
    source_path: str | None = None,
) -> None:
    if PRIVATE_KEY_BLOCK_RE.search(text):
        raise ReleaseError("private key material found in release source", category="safety")
    source = PurePosixPath((source_path or "").replace("\\", "/"))
    if source.suffix == ".py":
        is_test_source = any(
            part in {"test", "tests"}
            for part in source.parts[:-1]
        )
        _validate_python_credential_literals(
            text,
            allow_fixture_markers=is_test_source,
        )
        return
    _validate_credential_assignment_text(text)


def _validate_source_tar(path: Path) -> None:
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = archive.getmembers()
            validate_archive_member_names([member.name for member in members])
            for member in members:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ReleaseError(
                        f"forbidden archive member type: {member.name}",
                        category="safety",
                    )
                if member.size > 2 * 1024 * 1024:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReleaseError(
                        f"could not inspect archive member: {member.name}"
                    )
                payload = extracted.read()
                if b"\x00" in payload:
                    continue
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                validate_text_payload(text, source_path=member.name)
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseError("source archive validation failed") from exc


def _canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_transport_tar(path: Path, files: Sequence[Path]) -> None:
    with tarfile.open(path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for source in files:
            payload = source.read_bytes()
            info = tarfile.TarInfo(source.name)
            info.size = len(payload)
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))


def _generate_proto_into_source_tar(
    source_tar: Path,
    *,
    runner: CommandRunner,
    executable: str | None = None,
) -> None:
    with tarfile.open(source_tar, mode="r:") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if not {"buf.gen.yaml", "proto/buf.yaml"}.issubset(names):
            return
        if any(name == "gen" or name.startswith("gen/") for name in names):
            raise ReleaseError("committed source unexpectedly contains gen output")
        resolved_executable = executable or shutil.which("buf")
        if not resolved_executable:
            raise ReleaseError("buf is required to build the release artifact")

        with tempfile.TemporaryDirectory(prefix="car-agent-codegen-") as raw:
            source_root = Path(raw)
            for member in members:
                target = source_root.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ReleaseError(
                        f"forbidden archive member type: {member.name}",
                        category="safety",
                    )
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReleaseError(
                        f"could not inspect archive member: {member.name}"
                    )
                target.write_bytes(extracted.read())

            runner.run(
                [resolved_executable, "generate", "proto"],
                cwd=source_root,
            )
            generated_root = source_root / "gen"
            python_root = generated_root / "python"
            if (
                not python_root.is_dir()
                or not any(python_root.rglob("*_pb2.py"))
                or not any(python_root.rglob("*_pb2_grpc.py"))
            ):
                raise ReleaseError("buf did not produce Python protobuf output")

            generated = sorted(
                generated_root.rglob("*"),
                key=lambda item: item.relative_to(source_root).as_posix(),
            )
            with tarfile.open(source_tar, mode="a") as output:
                for path in generated:
                    if path.is_symlink() or not (path.is_dir() or path.is_file()):
                        raise ReleaseError("buf produced an unsupported output type")
                    name = path.relative_to(source_root).as_posix()
                    info = tarfile.TarInfo(name)
                    info.mode = 0o755 if path.is_dir() else 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.mtime = 0
                    if path.is_dir():
                        info.type = tarfile.DIRTYPE
                        output.addfile(info)
                        continue
                    payload = path.read_bytes()
                    info.size = len(payload)
                    output.addfile(info, io.BytesIO(payload))


def _artifact_paths(directory: Path) -> ReleaseArtifact:
    return ReleaseArtifact(
        directory=directory,
        source_tar=directory / "source.tar",
        manifest=directory / "manifest.json",
        checksums=directory / "checksums.sha256",
        transport_tar=directory / "transport.tar",
    )


def _expected_manifest(
    plan: ReleasePlan,
    *,
    services_digest: str,
    models_digest: str,
    source_digest: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "deployed_sha": plan.deployed_sha,
        "target_sha": plan.target_sha,
        "short_sha": plan.target_sha[:12],
        "changed_paths": list(plan.changed_paths),
        "blocking_changes": [
            {"path": item.path, "category": item.category}
            for item in plan.blocking_changes
        ],
        "plan_status": plan.status,
        "source_sha256": source_digest,
        "release_services_sha256": services_digest,
        "runtime_models_sha256": models_digest,
        "target_infrastructure_sha256": plan.target_infrastructure_digest,
        "approved_infrastructure_sha256": plan.approved_infrastructure_digest,
    }


def _parse_checksums(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or not SHA256_RE.fullmatch(digest)
            or name not in {"source.tar", "manifest.json"}
            or name in parsed
        ):
            raise ReleaseError("artifact exists but does not match")
        parsed[name] = digest
    if set(parsed) != {"source.tar", "manifest.json"}:
        raise ReleaseError("artifact exists but does not match")
    return parsed


def _validate_existing_artifact(
    artifact: ReleaseArtifact,
    *,
    plan: ReleasePlan,
    services_digest: str,
    models_digest: str,
) -> ReleaseArtifact:
    try:
        if not all(
            path.is_file()
            for path in (
                artifact.source_tar,
                artifact.manifest,
                artifact.checksums,
                artifact.transport_tar,
            )
        ):
            raise ReleaseError("artifact exists but does not match")
        checksums = _parse_checksums(artifact.checksums)
        if checksums["source.tar"] != sha256_file(artifact.source_tar):
            raise ReleaseError("artifact exists but does not match")
        if checksums["manifest.json"] != sha256_file(artifact.manifest):
            raise ReleaseError("artifact exists but does not match")
        manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
        expected = _expected_manifest(
            plan,
            services_digest=services_digest,
            models_digest=models_digest,
            source_digest=checksums["source.tar"],
        )
        if manifest != expected:
            raise ReleaseError("artifact exists but does not match")
        _validate_source_tar(artifact.source_tar)
        with tarfile.open(artifact.transport_tar, mode="r:") as archive:
            if archive.getnames() != [
                "source.tar",
                "manifest.json",
                "checksums.sha256",
            ]:
                raise ReleaseError("artifact exists but does not match")
            for source in (
                artifact.source_tar,
                artifact.manifest,
                artifact.checksums,
            ):
                extracted = archive.extractfile(source.name)
                if extracted is None or extracted.read() != source.read_bytes():
                    raise ReleaseError("artifact exists but does not match")
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        raise ReleaseError("artifact exists but does not match") from exc
    return artifact


def build_release_artifact(
    *,
    repo: Path,
    output_root: Path,
    plan: ReleasePlan,
    services_digest: str,
    models_digest: str,
    codegen_runner: CommandRunner | None = None,
    codegen_executable: str | None = None,
) -> ReleaseArtifact:
    if plan.status != "ready" or plan.blocking_changes:
        raise ReleaseError("cannot build artifact for a blocked release plan")
    if not FULL_SHA_RE.fullmatch(plan.target_sha):
        raise ReleaseError("target SHA must be a full commit SHA", category="configuration")
    if not SHA256_RE.fullmatch(services_digest):
        raise ReleaseError("release services digest is invalid")
    if not SHA256_RE.fullmatch(models_digest):
        raise ReleaseError("runtime models digest is invalid")

    output_root.mkdir(parents=True, exist_ok=True)
    directory = output_root / plan.target_sha
    artifact = _artifact_paths(directory)
    if directory.exists():
        return _validate_existing_artifact(
            artifact,
            plan=plan,
            services_digest=services_digest,
            models_digest=models_digest,
        )

    staging = output_root / f".{plan.target_sha}.{uuid.uuid4().hex}.staging"
    staging.mkdir(mode=0o700)
    staged = _artifact_paths(staging)
    SubprocessRunner().run(
        [
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(staged.source_tar),
            plan.target_sha,
        ],
        cwd=repo,
    )
    _generate_proto_into_source_tar(
        staged.source_tar,
        runner=codegen_runner or SubprocessRunner(),
        executable=codegen_executable,
    )
    _validate_source_tar(staged.source_tar)
    source_digest = sha256_file(staged.source_tar)
    manifest_payload = _expected_manifest(
        plan,
        services_digest=services_digest,
        models_digest=models_digest,
        source_digest=source_digest,
    )
    staged.manifest.write_bytes(_canonical_json_bytes(manifest_payload))
    manifest_digest = sha256_file(staged.manifest)
    staged.checksums.write_text(
        f"{source_digest}  source.tar\n"
        f"{manifest_digest}  manifest.json\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_transport_tar(
        staged.transport_tar,
        (staged.source_tar, staged.manifest, staged.checksums),
    )
    try:
        staging.replace(directory)
    except OSError as exc:
        if directory.exists():
            return _validate_existing_artifact(
                artifact,
                plan=plan,
                services_digest=services_digest,
                models_digest=models_digest,
            )
        raise ReleaseError("could not publish local release artifact") from exc
    return _artifact_paths(directory)


REMOTE_STATE_FIELDS = {
    "current_release",
    "current_path",
    "runtime_project_name",
    "approved_infrastructure_digest",
    "disk_available_bytes",
    "memory_available_bytes",
    "release_lock_available",
    "runtime_project_ready",
    "shared_scripts_ready",
    "shared_models_ready",
}

MODEL_BOOTSTRAP_FILES = (
    (
        "models/nlu/edge_nlu.onnx",
        "cda6914c715d7e48f7b1f2ef2e2e9a64843e53ec58165737b41ec4e186080cf8",
        None,
    ),
    (
        "models/nlu/labels.json",
        "11720e1620a6aefafb719ac151052600a8272906762aeff83c9132b6fc5f17d5",
        None,
    ),
    (
        "models/nlu/vocab.json",
        "43ad94d3586ba0c3ddafdf0f989833f730aa6a2cc0b88d10ea6ac7eba85d56b5",
        None,
    ),
    (
        "models/voiceprint/campplus_zh-cn_16k-common.onnx",
        "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11",
        None,
    ),
    (
        "models/hmi/public/models/silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        "approved local asset:hmi/public/models/silero_vad.onnx",
    ),
    (
        "models/hmi/public/kws/sherpa-onnx-kws.js",
        "d2113885f82cf307f52906ddf2a8786315db86fca53209c2d1e54c7fff8c6c76",
        "approved local asset:hmi/public/kws/sherpa-onnx-kws.js",
    ),
    (
        "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.data",
        "b91b148aa19d386fe27624867c21111c6a6bfa739a619538bb705408a8eb7165",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.data",
    ),
    (
        "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.js",
        "93899d72cbb9a8e2ba7e71cc1143fdc7639098107e771860070bd507d8edfd87",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.js",
    ),
    (
        "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm",
        "ca2a000807ab83b20a37b512ff4613872528471a227f738dd30d07efaf563492",
        "approved local asset:hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm",
    ),
)

SHARED_SCRIPT_NAMES = (
    "transaction-lock.sh",
    "remote-e2e-lock.sh",
    "remote-data-migration.sh",
    "backup.sh",
    "remote-release.sh",
    "remote-build.sh",
    "activate-release.sh",
    "verify-release.sh",
)


def make_bootstrap_report(state: RemoteState) -> BootstrapReport:
    source_release = state.current_path
    details: list[BootstrapCandidate] = []
    if not state.runtime_project_ready:
        details.append(
            BootstrapCandidate(
                path="/opt/car-agent/shared/runtime-project-name",
                source="docker:com.docker.compose.project",
                sha256=None,
                mode="0644",
            )
        )
    if state.approved_infrastructure_digest is None:
        details.append(
            BootstrapCandidate(
                path="/opt/car-agent/shared/release-infrastructure.json",
                source="approved target commit deploy/cloud manifest",
                sha256=None,
                mode="0600",
            )
        )
    if not state.shared_scripts_ready:
        for name in SHARED_SCRIPT_NAMES:
            details.append(
                BootstrapCandidate(
                    path=f"/opt/car-agent/shared/bin/{name}",
                    source=f"approved target commit:deploy/cloud/{name}",
                    sha256=None,
                    mode="0755",
                )
            )
    if not state.shared_models_ready:
        for relative, digest, approved_source in MODEL_BOOTSTRAP_FILES:
            shared_relative = relative.removeprefix("models/")
            details.append(
                BootstrapCandidate(
                    path=f"/opt/car-agent/shared/models/{shared_relative}",
                    source=approved_source or f"{source_release}/{relative}",
                    sha256=digest,
                    mode="0644",
                )
            )
    return BootstrapReport(
        status="bootstrap_required" if details else "ready",
        source_release=source_release,
        candidates=tuple(item.path for item in details),
        details=tuple(details),
    )


REMOTE_PREFLIGHT_SOURCE = r'''from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

ROOT = Path("/opt/car-agent")
SHARED = ROOT / "shared"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SCRIPTS = (
    SHARED / "bin/transaction-lock.sh",
    SHARED / "bin/remote-e2e-lock.sh",
    SHARED / "bin/remote-data-migration.sh",
    SHARED / "bin/backup.sh",
    SHARED / "bin/remote-release.sh",
    SHARED / "bin/remote-build.sh",
    SHARED / "bin/activate-release.sh",
    SHARED / "bin/verify-release.sh",
)
MODELS = {
    SHARED / "models/nlu/edge_nlu.onnx": "cda6914c715d7e48f7b1f2ef2e2e9a64843e53ec58165737b41ec4e186080cf8",
    SHARED / "models/nlu/labels.json": "11720e1620a6aefafb719ac151052600a8272906762aeff83c9132b6fc5f17d5",
    SHARED / "models/nlu/vocab.json": "43ad94d3586ba0c3ddafdf0f989833f730aa6a2cc0b88d10ea6ac7eba85d56b5",
    SHARED / "models/voiceprint/campplus_zh-cn_16k-common.onnx": "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11",
    SHARED / "models/hmi/public/models/silero_vad.onnx": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    SHARED / "models/hmi/public/kws/sherpa-onnx-kws.js": "d2113885f82cf307f52906ddf2a8786315db86fca53209c2d1e54c7fff8c6c76",
    SHARED / "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.data": "b91b148aa19d386fe27624867c21111c6a6bfa739a619538bb705408a8eb7165",
    SHARED / "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.js": "93899d72cbb9a8e2ba7e71cc1143fdc7639098107e771860070bd507d8edfd87",
    SHARED / "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm": "ca2a000807ab83b20a37b512ff4613872528471a227f738dd30d07efaf563492",
}
REQUIRED_INSTALLED = {
    "deploy/cloud/transaction-lock.sh": "/opt/car-agent/shared/bin/transaction-lock.sh",
    "deploy/cloud/remote-e2e-lock.sh": "/opt/car-agent/shared/bin/remote-e2e-lock.sh",
    "deploy/cloud/remote-data-migration.sh": "/opt/car-agent/shared/bin/remote-data-migration.sh",
    "deploy/cloud/backup.sh": "/opt/car-agent/shared/bin/backup.sh",
    "deploy/cloud/remote-release.sh": "/opt/car-agent/shared/bin/remote-release.sh",
    "deploy/cloud/remote-build.sh": "/opt/car-agent/shared/bin/remote-build.sh",
    "deploy/cloud/activate-release.sh": "/opt/car-agent/shared/bin/activate-release.sh",
    "deploy/cloud/verify-release.sh": "/opt/car-agent/shared/bin/verify-release.sh",
    "deploy/cloud/compose.cloud.yaml": "/opt/car-agent/shared/compose.cloud.yaml",
    "deploy/cloud/vite.hmi.cloud.config.mjs": "/opt/car-agent/shared/vite.hmi.cloud.config.mjs",
    "deploy/cloud/systemd/car-agent-backup.service": "/etc/systemd/system/car-agent-backup.service",
    "deploy/cloud/systemd/car-agent-backup.timer": "/etc/systemd/system/car-agent-backup.timer",
}


def run(argv):
    completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    return completed.stdout.decode("utf-8", errors="strict").strip()


def secure_regular(path):
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return bool(
        stat.S_ISREG(metadata.st_mode)
        and not path.is_symlink()
        and metadata.st_uid == 0
        and metadata.st_gid == 0
        and metadata.st_mode & 0o022 == 0
    )


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def current_project():
    ids = [item for item in run(["docker", "ps", "--format", "{{.ID}}"]).splitlines() if item]
    if not ids:
        raise RuntimeError("no running containers")
    containers = json.loads(run(["docker", "inspect", *ids]))
    projects = Counter(
        str(item.get("Config", {}).get("Labels", {}).get("com.docker.compose.project", ""))
        for item in containers
    )
    projects.pop("", None)
    if not projects:
        raise RuntimeError("compose project label unavailable")
    project, _ = projects.most_common(1)[0]
    if not PROJECT.fullmatch(project):
        raise RuntimeError("invalid compose project label")
    return project


def lock_available():
    path = SHARED / "locks/release.lock"
    if not secure_regular(path):
        return False
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(descriptor)


def project_file_ready(project):
    path = SHARED / "runtime-project-name"
    if not secure_regular(path):
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    return lines == [project]


def scripts_ready():
    for path in SCRIPTS:
        if not secure_regular(path):
            return False
        if subprocess.run(["bash", "-n", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            return False
    return True


def models_ready():
    return all(secure_regular(path) and digest(path) == expected for path, expected in MODELS.items())


def approved_infrastructure_digest():
    path = SHARED / "release-infrastructure.json"
    if not secure_regular(path):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"schema_version", "infrastructure_sha256", "source_files", "installed_files"}:
            return None
        if payload["schema_version"] != 1:
            return None
        aggregate = payload["infrastructure_sha256"]
        source_files = payload["source_files"]
        installed_files = payload["installed_files"]
        if not isinstance(aggregate, str) or not SHA256.fullmatch(aggregate):
            return None
        if not isinstance(source_files, dict) or not source_files:
            return None
        if any(
            not isinstance(name, str)
            or not name.startswith("deploy/cloud/")
            or name == "deploy/cloud/README.md"
            or not isinstance(value, str)
            or not SHA256.fullmatch(value)
            for name, value in source_files.items()
        ):
            return None
        canonical = json.dumps(source_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != aggregate:
            return None
        if not isinstance(installed_files, list) or not installed_files:
            return None
        installed = {}
        for item in installed_files:
            if not isinstance(item, dict) or set(item) != {"source_path", "target_path", "sha256"}:
                return None
            source = item["source_path"]
            target = item["target_path"]
            expected = item["sha256"]
            if (
                source in installed
                or source_files.get(source) != expected
                or not isinstance(target, str)
                or not isinstance(expected, str)
                or not SHA256.fullmatch(expected)
            ):
                return None
            target_path = Path(target)
            if not secure_regular(target_path) or digest(target_path) != expected:
                return None
            installed[source] = target
        if installed != REQUIRED_INSTALLED:
            return None
        return aggregate
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


current_path = run(["readlink", "-f", "/opt/car-agent/current"])
current_release = Path(current_path).name
if current_path != f"/opt/car-agent/releases/{current_release}":
    raise RuntimeError("invalid current release path")
project = current_project()
disk_lines = run(["df", "--output=avail", "-B1", "/opt/car-agent"]).splitlines()
disk_available = int(disk_lines[-1].strip())
memory_available = 0
with Path("/proc/meminfo").open("r", encoding="ascii") as handle:
    for line in handle:
        if line.startswith("MemAvailable:"):
            memory_available = int(line.split()[1]) * 1024
            break
if memory_available <= 0:
    raise RuntimeError("MemAvailable is unavailable")
result = {
    "current_release": current_release,
    "current_path": current_path,
    "runtime_project_name": project,
    "approved_infrastructure_digest": approved_infrastructure_digest(),
    "disk_available_bytes": disk_available,
    "memory_available_bytes": memory_available,
    "release_lock_available": lock_available(),
    "runtime_project_ready": project_file_ready(project),
    "shared_scripts_ready": scripts_ready(),
    "shared_models_ready": models_ready(),
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''

REMOTE_PREFLIGHT_COMMAND = (
    "sudo python3 -c \"import base64;exec(base64.b64decode('"
    + base64.b64encode(REMOTE_PREFLIGHT_SOURCE.encode("utf-8")).decode("ascii")
    + "'))\""
)


def parse_remote_state(payload: str) -> RemoteState:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReleaseError("remote preflight returned invalid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != REMOTE_STATE_FIELDS:
        raise ReleaseError("remote preflight returned an invalid field set")

    selector = raw["current_release"]
    current_path = raw["current_path"]
    runtime_project = raw["runtime_project_name"]
    approved_digest = raw["approved_infrastructure_digest"]
    if not isinstance(selector, str) or not RELEASE_SELECTOR_RE.fullmatch(selector):
        raise ReleaseError("remote preflight returned an invalid release")
    if current_path != f"/opt/car-agent/releases/{selector}":
        raise ReleaseError("remote preflight returned an invalid current path")
    if (
        not isinstance(runtime_project, str)
        or not RUNTIME_PROJECT_RE.fullmatch(runtime_project)
    ):
        raise ReleaseError("remote preflight returned an invalid runtime project")
    if approved_digest is not None and (
        not isinstance(approved_digest, str)
        or not SHA256_RE.fullmatch(approved_digest)
    ):
        raise ReleaseError(
            "remote preflight returned an invalid infrastructure digest"
        )

    integer_fields = ("disk_available_bytes", "memory_available_bytes")
    for field in integer_fields:
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReleaseError(
                f"remote preflight returned an invalid {field}"
            )
    boolean_fields = (
        "release_lock_available",
        "runtime_project_ready",
        "shared_scripts_ready",
        "shared_models_ready",
    )
    if any(type(raw[field]) is not bool for field in boolean_fields):
        raise ReleaseError("remote preflight returned an invalid boolean field")

    return RemoteState(
        current_release=selector,
        current_path=current_path,
        runtime_project_name=runtime_project,
        approved_infrastructure_digest=approved_digest,
        disk_available_bytes=raw["disk_available_bytes"],
        memory_available_bytes=raw["memory_available_bytes"],
        release_lock_available=raw["release_lock_available"],
        runtime_project_ready=raw["runtime_project_ready"],
        shared_scripts_ready=raw["shared_scripts_ready"],
        shared_models_ready=raw["shared_models_ready"],
    )


def discover_remote_state(
    request: ReleaseRequest,
    *,
    runner: CommandRunner,
) -> RemoteState:
    result = runner.run(
        request.ssh.ssh_argv(REMOTE_PREFLIGHT_COMMAND),
        cwd=request.repo,
    )
    return parse_remote_state(result.stdout)


def _resolve_commit(repo: Path, revision: str) -> str:
    resolved = _git(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        check=False,
    )
    sha = resolved.stdout.strip()
    if resolved.returncode != 0 or not FULL_SHA_RE.fullmatch(sha):
        raise ReleaseError(
            "could not resolve requested revision", category="configuration"
        )
    return sha


def _committed_file_digest(repo: Path, target_sha: str, path: str) -> str:
    return hashlib.sha256(_git_blob(repo, target_sha, path)).hexdigest()


def execute_deploy(
    request: ReleaseRequest,
    *,
    apply: bool,
    runner: CommandRunner,
    nonce_factory: Callable[[], str] | None = None,
) -> CloudReleaseResult:
    validate_ssh_identity(request.ssh.identity)

    target_sha = require_clean_main_commit(request.repo, request.revision)
    remote_state = discover_remote_state(request, runner=runner)
    deployed_sha = _resolve_commit(request.repo, remote_state.current_release)
    changed_paths, diff_by_path = git_changes(
        request.repo,
        deployed_sha,
        target_sha,
    )
    infrastructure_digest = compute_infrastructure_digest(
        request.repo,
        target_sha,
    )
    plan = make_release_plan(
        deployed_sha=deployed_sha,
        target_sha=target_sha,
        changed_paths=changed_paths,
        diff_by_path=diff_by_path,
        target_infrastructure_digest=infrastructure_digest,
        approved_infrastructure_digest=(
            remote_state.approved_infrastructure_digest
        ),
    )
    if plan.status == "plan_rejected":
        return CloudReleaseResult(
            status=plan.status,
            plan=plan,
            artifact=None,
            remote_state=remote_state,
        )
    if make_bootstrap_report(remote_state).status != "ready":
        return CloudReleaseResult(
            status="bootstrap_required",
            plan=plan,
            artifact=None,
            remote_state=remote_state,
        )
    if plan.status != "ready":
        return CloudReleaseResult(
            status=plan.status,
            plan=plan,
            artifact=None,
            remote_state=remote_state,
        )

    artifact = build_release_artifact(
        repo=request.repo,
        output_root=request.artifact_root,
        plan=plan,
        services_digest=_committed_file_digest(
            request.repo,
            target_sha,
            "deploy/cloud/release-services.json",
        ),
        models_digest=_committed_file_digest(
            request.repo,
            target_sha,
            "deploy/cloud/runtime-models.json",
        ),
    )
    if not apply:
        return CloudReleaseResult(
            status="dry_run",
            plan=plan,
            artifact=artifact,
            remote_state=remote_state,
        )

    nonce = (nonce_factory or (lambda: secrets.token_hex(16)))()
    if not UPLOAD_NONCE_RE.fullmatch(nonce):
        raise ReleaseError("upload nonce must be 32 lowercase hex characters")
    upload_id = f"{target_sha}-{nonce}"
    prepare_command = (
        "sudo /opt/car-agent/shared/bin/remote-release.sh prepare-upload "
        f"--sha {target_sha} --upload-id {upload_id}"
    )
    prepared = runner.run(
        request.ssh.ssh_argv(prepare_command),
        cwd=request.repo,
    )
    expected_directory = f"/opt/car-agent/incoming/releases/{upload_id}"
    if prepared.stdout.strip() != expected_directory:
        raise ReleaseError("unexpected upload directory returned by server")

    runner.run(
        request.ssh.scp_argv(
            artifact.transport_tar,
            f"{expected_directory}/transport.tar",
        ),
        cwd=request.repo,
    )
    runner.run(
        request.ssh.ssh_argv(
            f"chmod 0600 -- {expected_directory}/transport.tar"
        ),
        cwd=request.repo,
    )
    deploy_command = (
        "sudo /opt/car-agent/shared/bin/remote-release.sh deploy "
        f"--sha {target_sha} --upload-id {upload_id}"
    )
    runner.run(
        request.ssh.ssh_argv(deploy_command),
        cwd=request.repo,
    )
    return CloudReleaseResult(
        status="submitted",
        plan=plan,
        artifact=artifact,
        remote_state=remote_state,
    )

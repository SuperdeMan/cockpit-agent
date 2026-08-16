from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import BinaryIO, Mapping, Sequence


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SSH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
SSH_KEX_RE = re.compile(r"^[A-Za-z0-9@._+,-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
    """A safe, user-facing cloud release error."""


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
        raise ReleaseError("target SHA must be a full commit SHA")
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
        raise ReleaseError("target SHA must be a full commit SHA")
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
            raise ReleaseError(f"forbidden archive member: {raw_name}")


def validate_text_payload(text: str) -> None:
    if PRIVATE_KEY_BLOCK_RE.search(text):
        raise ReleaseError("private key material found in release source")
    for match in CREDENTIAL_ASSIGNMENT_RE.finditer(text):
        value = match.group("value")
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "example",
                "placeholder",
                "changeme",
                "your_",
                "your-",
                "mock",
                "test",
                "${",
            )
        ):
            continue
        raise ReleaseError("credential-like assignment found in release source")


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
                        f"forbidden archive member type: {member.name}"
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
                validate_text_payload(text)
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
) -> ReleaseArtifact:
    if plan.status != "ready" or plan.blocking_changes:
        raise ReleaseError("cannot build artifact for a blocked release plan")
    if not FULL_SHA_RE.fullmatch(plan.target_sha):
        raise ReleaseError("target SHA must be a full commit SHA")
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

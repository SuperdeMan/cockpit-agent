"""Exclusive stack lease for runner-issued E2E identities."""

from __future__ import annotations

import json
import hashlib
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any
from urllib.request import urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e_identity import encode_secret, generate_secret, sign_identity  # noqa: E402


IDENTITY_SERVICES = ("edge-gateway", "llm-gateway", "memory")
DEFAULT_SCOPES = (
    "vehicle.control",
    "media.control",
    "navigation",
    "food.ordering",
    "location.read",
    "navigation.control",
    "network.external",
    "payment.invoke",
    "memory.read",
    "memory.write",
    "profile.read",
    "profile.write",
    "camera.frame",
)
_BUNDLE_KEYS = {
    "schema_version",
    "lease_id",
    "case_id",
    "run_id",
    "user_id",
    "vehicle_id",
    "identity_token",
    "control_user_id",
    "control_identity_token",
    "voiceprint_fixture",
    # Legacy wire key: the array contains signed capabilities, not session IDs.
    "memory_session_ids",
}
_CAPABILITY_OWNER_KEYS = frozenset({
    "E2E_CAPABILITY_ENABLED",
    "E2E_CAPABILITY_SECRET",
})
_NAMESPACE_ADMIN_OWNER_KEYS = frozenset({
    "E2E_NAMESPACE_ADMIN_ENABLED",
    "E2E_NAMESPACE_ADMIN_SECRET",
})
MAX_BUNDLE_BYTES = 512 * 1024
MAX_IDENTITY_TOKEN_BYTES = 4096
MAX_MEMORY_SESSION_BYTES = 4096
MAX_MEMORY_SESSIONS = 64
MAX_NAMESPACE_BYTES = 512
_SAFE_NAMESPACE_RE = re.compile(r"[A-Za-z0-9._:-]+\Z")
_RUN_ID_RE = re.compile(r"e2e-[A-Za-z0-9._:-]+\Z")
_CASE_ID_RE = re.compile(r"e2e_[a-z0-9_]+\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_VOICEPRINT_FIXTURE_KEYS = frozenset({
    "directory",
    "manifest",
    "manifest_sha256",
    "audio_api_origin",
})
_FIXTURE_ENV_KEYS = frozenset({
    "E2E_VOICEPRINT_FIXTURE_DIR",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
    "E2E_AUDIO_API_ORIGIN",
})
MAX_FIXTURE_MANIFEST_BYTES = 4 * 1024 * 1024


class StackLeaseError(RuntimeError):
    """Base class for identity stack lease failures."""


class StackLeaseProtocolError(StackLeaseError):
    """Owner/child lease protocol was violated."""


class IdentityEnableError(StackLeaseError):
    """The identity-enabled stack could not become ready."""


class IdentityLeaseBusyError(IdentityEnableError):
    """Another owner holds the repository identity stack lease."""


class IdentityCleanupError(StackLeaseError):
    """The default-off stack could not be restored."""


def _canonical_audio_api_origin(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture audio origin is invalid",
        )
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture audio origin is invalid",
        ) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture audio origin is invalid",
        )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    canonical = f"{parsed.scheme.lower()}://{authority}"
    if value != canonical:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture audio origin is not canonical",
        )
    return canonical


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StackLeaseProtocolError(
                f"token bundle has duplicate JSON key: {key}",
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StackLeaseProtocolError(
        f"token bundle contains invalid JSON constant: {value}",
    )


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or (
            hasattr(os.path, "isjunction")
            and os.path.isjunction(path)
        )
    except OSError:
        return True


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_bundle_location(
    path: Path,
    *,
    bundle_root: Path,
    require_file: bool,
) -> tuple[Path, Path]:
    candidate = Path(path)
    root = Path(bundle_root)
    if not candidate.is_absolute() or not root.is_absolute():
        raise StackLeaseProtocolError(
            "token bundle and expected private root must be absolute",
        )
    lexical_root = _lexical_absolute(root)
    lexical_candidate = _lexical_absolute(candidate)
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError as exc:
        raise StackLeaseProtocolError(
            "token bundle escapes its expected private root",
        ) from exc
    if (
        relative == Path(".")
        or lexical_candidate.name != "tokens.json"
        or len(relative.parts) != 2
    ):
        raise StackLeaseProtocolError(
            "token bundle path is not a private case bundle",
        )
    try:
        root_resolved = lexical_root.resolve(strict=True)
    except OSError as exc:
        raise StackLeaseProtocolError(
            "token bundle expected private root is unreadable",
        ) from exc
    if not root_resolved.is_dir() or _is_link_or_junction(lexical_root):
        raise StackLeaseProtocolError(
            "token bundle expected private root is a symlink or junction",
        )
    current = lexical_root
    for part in relative.parts[:-1]:
        current = current / part
        if _is_link_or_junction(current):
            raise StackLeaseProtocolError(
                "token bundle path contains a symlink or junction",
            )
    if _is_link_or_junction(lexical_candidate):
        raise StackLeaseProtocolError(
            "token bundle path contains a symlink or junction",
        )
    if not require_file:
        try:
            parent_resolved = lexical_candidate.parent.resolve(strict=True)
            parent_resolved.relative_to(root_resolved)
        except (OSError, ValueError) as exc:
            raise StackLeaseProtocolError(
                "token bundle escapes its expected private root",
            ) from exc
        return lexical_candidate, root_resolved
    try:
        candidate_resolved = lexical_candidate.resolve(strict=True)
        candidate_resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise StackLeaseProtocolError(
            "token bundle escapes its expected private root",
        ) from exc
    return lexical_candidate, root_resolved


def validate_token_bundle_location(
    path: Path,
    *,
    bundle_root: Path,
    require_file: bool,
) -> Path:
    candidate, _root = _validate_bundle_location(
        path,
        bundle_root=bundle_root,
        require_file=require_file,
    )
    return candidate


def _read_bounded_regular_bundle(
    path: Path,
    *,
    bundle_root: Path,
) -> bytes:
    candidate, _root = _validate_bundle_location(
        path,
        bundle_root=bundle_root,
        require_file=True,
    )
    _verify_private_posix_path(
        candidate.parent,
        expected_mode=0o700,
        expected_kind="directory",
    )
    _verify_private_posix_path(
        candidate,
        expected_mode=0o600,
        expected_kind="file",
    )
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise StackLeaseProtocolError("token bundle is unreadable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise StackLeaseProtocolError("token bundle must be a regular file")
    if before.st_size > MAX_BUNDLE_BYTES:
        raise StackLeaseProtocolError("token bundle is too large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise StackLeaseProtocolError(
                    "token bundle must be a regular file",
                )
            if opened.st_size > MAX_BUNDLE_BYTES:
                raise StackLeaseProtocolError("token bundle is too large")
            content = handle.read(MAX_BUNDLE_BYTES + 1)
            if len(content) > MAX_BUNDLE_BYTES or handle.read(1):
                raise StackLeaseProtocolError("token bundle is too large")
    except StackLeaseProtocolError:
        raise
    except OSError as exc:
        raise StackLeaseProtocolError("token bundle is unreadable") from exc
    return content


def _safe_namespace(
    value: Any,
    *,
    field: str,
    pattern: re.Pattern[str] = _SAFE_NAMESPACE_RE,
) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8", errors="ignore")) > MAX_NAMESPACE_BYTES
        or pattern.fullmatch(value) is None
    ):
        raise StackLeaseProtocolError(
            f"token bundle {field} is invalid",
        )
    return value


def _bounded_token(
    value: Any,
    *,
    field: str,
    prefix: str,
    maximum: int,
) -> str:
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or len(value.encode("utf-8", errors="ignore")) > maximum
        or _TOKEN_RE.fullmatch(value) is None
    ):
        raise StackLeaseProtocolError(
            f"token bundle {field} is invalid or too large",
        )
    return value


def validate_token_bundle_payload(
    payload: Any,
    *,
    memory_mode: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _BUNDLE_KEYS:
        raise StackLeaseProtocolError("token bundle shape is invalid")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise StackLeaseProtocolError("token bundle schema version is invalid")
    lease_id = _safe_namespace(payload["lease_id"], field="lease_id")
    case_id = _safe_namespace(
        payload["case_id"],
        field="case_id",
        pattern=_CASE_ID_RE,
    )
    run_id = _safe_namespace(
        payload["run_id"],
        field="run_id",
        pattern=_RUN_ID_RE,
    )
    user_id = _safe_namespace(payload["user_id"], field="user_id")
    vehicle_id = _safe_namespace(payload["vehicle_id"], field="vehicle_id")
    control_user_id = _safe_namespace(
        payload["control_user_id"],
        field="control_user_id",
    )
    if user_id != f"{run_id}-{case_id}":
        raise StackLeaseProtocolError(
            "token bundle user namespace is invalid",
        )
    if control_user_id != f"{user_id}-control":
        raise StackLeaseProtocolError(
            "token bundle control user namespace is invalid",
        )
    _bounded_token(
        payload["identity_token"],
        field="identity_token",
        prefix="e2e.v1.",
        maximum=MAX_IDENTITY_TOKEN_BYTES,
    )
    fixture = payload["voiceprint_fixture"]
    if fixture is not None:
        if type(fixture) is not dict or set(fixture) != _VOICEPRINT_FIXTURE_KEYS:
            raise StackLeaseProtocolError(
                "token bundle voiceprint fixture shape is invalid",
            )
        for field in ("directory", "manifest"):
            value = fixture[field]
            if (
                type(value) is not str
                or not value
                or len(value.encode("utf-8", errors="ignore")) > 4096
                or not Path(value).is_absolute()
            ):
                raise StackLeaseProtocolError(
                    "token bundle voiceprint fixture path is invalid",
                )
        if (
            type(fixture["manifest_sha256"]) is not str
            or _SHA256_RE.fullmatch(fixture["manifest_sha256"]) is None
        ):
            raise StackLeaseProtocolError(
                "token bundle voiceprint fixture hash is invalid",
            )
        _canonical_audio_api_origin(fixture["audio_api_origin"])
    _bounded_token(
        payload["control_identity_token"],
        field="control_identity_token",
        prefix="e2e.v1.",
        maximum=MAX_IDENTITY_TOKEN_BYTES,
    )
    # ``memory_session_ids`` is a frozen legacy wire name; values are
    # capabilities once the owner presigns the child bundle.
    memory_sessions = payload["memory_session_ids"]
    if (
        type(memory_sessions) is not list
        or len(memory_sessions) > MAX_MEMORY_SESSIONS
        or any(type(value) is not str for value in memory_sessions)
        or len(set(memory_sessions)) != len(memory_sessions)
    ):
        raise StackLeaseProtocolError(
            "token bundle memory sessions are invalid",
        )
    if memory_mode not in {"placeholder", "capability"}:
        raise StackLeaseProtocolError("token bundle memory mode is invalid")
    for index, value in enumerate(memory_sessions, start=1):
        if (
            len(value.encode("utf-8", errors="ignore"))
            > MAX_MEMORY_SESSION_BYTES
            or _TOKEN_RE.fullmatch(value) is None
        ):
            raise StackLeaseProtocolError(
                "token bundle memory session is invalid or too large",
            )
        if memory_mode == "placeholder":
            if value != f"{user_id}-session-{index}":
                raise StackLeaseProtocolError(
                    "token bundle memory placeholder namespace is invalid",
                )
        elif not value.startswith("e2e-mem.v1."):
            raise StackLeaseProtocolError(
                "token bundle memory capability is invalid",
            )
    return payload


def _read_fixture_manifest(path: Path) -> bytes:
    if _is_link_or_junction(path):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture is unsafe",
        )
    try:
        before = path.lstat()
    except OSError as exc:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture is unreadable",
        ) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > MAX_FIXTURE_MANIFEST_BYTES
    ):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture manifest is invalid",
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            content = source.read(MAX_FIXTURE_MANIFEST_BYTES + 1)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != before.st_size
                or len(content) != opened.st_size
                or source.read(1)
            ):
                raise StackLeaseProtocolError(
                    "token bundle voiceprint fixture manifest changed",
                )
    except StackLeaseProtocolError:
        raise
    except OSError as exc:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture is unreadable",
        ) from exc
    return content


def _validate_fixture_attestation(
    payload: Mapping[str, Any],
    *,
    bundle_root: Path,
) -> dict[str, str] | None:
    fixture = payload["voiceprint_fixture"]
    if fixture is None:
        return None
    run_root = _lexical_absolute(Path(bundle_root)).parent
    expected_dir = _lexical_absolute(
        run_root
        / payload["case_id"]
        / "artifacts"
        / "voiceprint-fixtures",
    )
    expected_manifest = expected_dir / "voiceprint-fixtures.json"
    supplied_dir = _lexical_absolute(Path(fixture["directory"]))
    supplied_manifest = _lexical_absolute(Path(fixture["manifest"]))
    if (
        supplied_dir != expected_dir
        or supplied_manifest != expected_manifest
        or supplied_manifest.name != "voiceprint-fixtures.json"
    ):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture path does not match its run",
        )
    for component in (
        run_root,
        run_root / payload["case_id"],
        run_root / payload["case_id"] / "artifacts",
        expected_dir,
        expected_manifest,
    ):
        if _is_link_or_junction(component):
            raise StackLeaseProtocolError(
                "token bundle voiceprint fixture contains a symlink or junction",
            )
    try:
        resolved_dir = expected_dir.resolve(strict=True)
        resolved_manifest = expected_manifest.resolve(strict=True)
    except OSError as exc:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture is missing",
        ) from exc
    if (
        resolved_dir != expected_dir
        or not resolved_dir.is_dir()
        or resolved_manifest != expected_manifest
        or resolved_manifest.parent != resolved_dir
    ):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture is outside its run",
        )
    content = _read_fixture_manifest(expected_manifest)
    if hashlib.sha256(content).hexdigest() != fixture["manifest_sha256"]:
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture manifest hash does not match",
        )
    return {
        "directory": str(expected_dir),
        "manifest": str(expected_manifest),
        "manifest_sha256": fixture["manifest_sha256"],
        "audio_api_origin": fixture["audio_api_origin"],
    }


def load_token_bundle_payload(
    path: Path,
    *,
    bundle_root: Path,
    memory_mode: str,
) -> dict[str, Any]:
    candidate, _root = _validate_bundle_location(
        path,
        bundle_root=bundle_root,
        require_file=True,
    )
    content = _read_bounded_regular_bundle(
        candidate,
        bundle_root=bundle_root,
    )
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except StackLeaseProtocolError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StackLeaseProtocolError("token bundle is invalid JSON") from exc
    payload = validate_token_bundle_payload(
        payload,
        memory_mode=memory_mode,
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if canonical != content:
        raise StackLeaseProtocolError("token bundle JSON is not canonical")
    expected_directory = f"{payload['lease_id']}-{payload['case_id']}"
    if candidate.parent.name != expected_directory:
        raise StackLeaseProtocolError(
            "token bundle path does not match its namespace",
        )
    _validate_fixture_attestation(payload, bundle_root=Path(bundle_root))
    return payload


_HELD_LOCK_PATHS: set[str] = set()
_HELD_LOCKS_GUARD = threading.Lock()


def _git_common_directory(repo_root: Path) -> Path | None:
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        git_dir = dot_git.resolve()
    elif dot_git.is_file():
        try:
            marker = dot_git.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        prefix = "gitdir:"
        if not marker.lower().startswith(prefix):
            return None
        git_dir = (dot_git.parent / marker[len(prefix):].strip()).resolve()
    else:
        return None
    common_marker = git_dir / "commondir"
    if common_marker.is_file():
        try:
            relative = common_marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return git_dir
        if relative:
            return (git_dir / relative).resolve()
    return git_dir


def identity_lock_path(repo_root: Path) -> Path:
    """Return the persistent, repository-scoped OS lock file path."""

    root = Path(repo_root).resolve()
    control_dir = _git_common_directory(root)
    if control_dir is not None:
        return control_dir / "car-agent-e2e-identity-stack.lock"
    return root / ".e2e-identity-stack.lock"


def identity_lock_metadata(repo_root: Path) -> str:
    """Read diagnostic metadata without touching the locked first byte."""

    path = identity_lock_path(repo_root)
    try:
        with path.open("rb", buffering=0) as handle:
            handle.seek(1)
            raw = handle.read(2048)
    except OSError:
        return "<unavailable>"
    return raw.decode("utf-8", errors="replace")


class _RepositoryIdentityLock:
    def __init__(self, *, repo_root: Path, lease_id: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.lease_id = lease_id
        self.path = identity_lock_path(self.repo_root)
        self._key = os.path.normcase(str(self.path.resolve()))
        self._handle: Any | None = None
        self._locked = False
        self._registered = False

    def _diagnostic(self) -> str:
        value = identity_lock_metadata(self.repo_root)
        return value[:2048].replace("\r", " ").replace("\n", " ")

    def _busy(self) -> IdentityLeaseBusyError:
        return IdentityLeaseBusyError(
            f"identity_busy: lock={self.path} owner={self._diagnostic()}",
        )

    def _register(self) -> None:
        with _HELD_LOCKS_GUARD:
            if self._key in _HELD_LOCK_PATHS:
                raise self._busy()
            _HELD_LOCK_PATHS.add(self._key)
            self._registered = True

    def _open(self) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("x+b", buffering=0)
        except FileExistsError:
            handle = self.path.open("r+b", buffering=0)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"L")
            handle.flush()
        handle.seek(0)
        return handle

    def _acquire_os_lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(
            self._handle.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )

    def _write_metadata(self) -> None:
        payload = {
            "schema_version": 1,
            "owner_pid": os.getpid(),
            "lease_id": self.lease_id,
            "started_at": time.time(),
            "repo_root": str(self.repo_root),
        }
        encoded = (
            "L\n"
            + json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ).encode("utf-8")
        self._handle.seek(0)
        self._handle.write(encoded)
        self._handle.truncate()
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def acquire(self) -> None:
        self._register()
        try:
            self._handle = self._open()
        except OSError as exc:
            self.release()
            raise IdentityEnableError("identity_lock") from exc
        try:
            self._acquire_os_lock()
            self._locked = True
        except (OSError, BlockingIOError) as exc:
            self.release()
            raise self._busy() from exc
        try:
            self._write_metadata()
        except OSError as exc:
            self.release()
            raise IdentityEnableError("identity_lock") from exc

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        try:
            if handle is not None and self._locked:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            self._locked = False
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            if self._registered:
                with _HELD_LOCKS_GUARD:
                    _HELD_LOCK_PATHS.discard(self._key)
                self._registered = False


def compose_gate_override(
    extra_services: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the secret-free Compose overlay for runner-only stack gates."""

    services: dict[str, dict[str, dict[str, str]]] = {
        name: {
            "environment": {
                "E2E_IDENTITY_ENABLED": "${E2E_IDENTITY_ENABLED:-false}",
                "E2E_IDENTITY_SECRET": "${E2E_IDENTITY_SECRET-}",
            },
        }
        for name in ("edge-gateway", "llm-gateway")
    }
    services["memory"] = {
        "environment": {
            "E2E_CAPABILITY_ENABLED": "${E2E_CAPABILITY_ENABLED:-false}",
            "E2E_CAPABILITY_SECRET": "${E2E_CAPABILITY_SECRET-}",
        },
    }
    for name in extra_services:
        environment = {
            "E2E_NAMESPACE_ADMIN_ENABLED": (
                "${E2E_NAMESPACE_ADMIN_ENABLED:-false}"
            ),
            "E2E_NAMESPACE_ADMIN_SECRET": "${E2E_NAMESPACE_ADMIN_SECRET-}",
        }
        if name == "mcp-bridge":
            environment["NATS_URL"] = "nats://nats:4222"
        services[name] = {"environment": environment}
    return {"services": services}


def _compose_recreate(
    repo_root: Path,
    environ: Mapping[str, str],
    *,
    extra_services: Sequence[str] = (),
) -> None:
    override = compose_gate_override(extra_services)

    # A worktree may deliberately operate the repository root's shared stack.
    # Overlay the runner-only gates so an older operational checkout cannot
    # silently discard the lease environment before the source worktree merges.
    with tempfile.TemporaryDirectory(prefix="e2e-compose-") as temp_dir:
        override_path = Path(temp_dir) / "identity-gates.json"
        override_path.write_text(
            json.dumps(
                override,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(repo_root / "compose.yaml"),
                "-f",
                str(override_path),
                "up",
                "-d",
                "--force-recreate",
                *IDENTITY_SERVICES,
                *extra_services,
            ],
            cwd=repo_root,
            env=dict(environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=True,
        )


def _http_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _tcp_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def wait_identity_services(timeout_s: int = 90) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (
            _http_ready("http://127.0.0.1:8090/healthz")
            and _http_ready("http://127.0.0.1:50059/api/health")
            and _tcp_ready("127.0.0.1", 50053)
        ):
            return True
        time.sleep(0.5)
    return False


class IdentityStackLease:
    """One owner enables and later restores the three-service identity stack."""

    def __init__(
        self,
        *,
        repo_root: Path,
        environ: MutableMapping[str, str],
        compose: Callable[[Mapping[str, str]], Any] | None = None,
        ready: Callable[[], bool] = wait_identity_services,
        secret_factory: Callable[[], bytes] = generate_secret,
        lease_id_factory: Callable[[], str] | None = None,
        extra_services: Sequence[str] = (),
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.environ = environ
        if any(type(service) is not str or not service for service in extra_services):
            raise StackLeaseProtocolError("extra service names are invalid")
        self.extra_services = tuple(dict.fromkeys(extra_services))
        self.compose = (
            compose
            if compose is not None
            else lambda env: _compose_recreate(
                self.repo_root,
                env,
                extra_services=self.extra_services,
            )
        )
        self.ready = ready
        self.secret_factory = secret_factory
        self.lease_id_factory = lease_id_factory or (
            lambda: "lease-" + uuid.uuid4().hex
        )
        self.lease_id = ""
        self.secret = b""
        self._enabled = False
        self._restored = False
        self._stack_lock: _RepositoryIdentityLock | None = None

    def _clear_process_environment(self) -> None:
        for name in (
            "E2E_IDENTITY_ENABLED",
            "E2E_IDENTITY_SECRET",
            "E2E_STACK_LEASE_ID",
            "E2E_STACK_LEASE_ROLE",
            "E2E_NAMESPACE_ADMIN_ENABLED",
            "E2E_NAMESPACE_ADMIN_SECRET",
        ):
            self.environ.pop(name, None)

    def enable(self) -> None:
        if self._enabled:
            raise StackLeaseProtocolError("identity lease already enabled")
        if self._restored:
            raise StackLeaseProtocolError("identity lease cannot be reused")
        if self.environ.get("E2E_STACK_LEASE_ROLE") == "child":
            raise StackLeaseProtocolError("lease child cannot rebuild the stack")
        self.lease_id = self.lease_id_factory()
        if not isinstance(self.lease_id, str) or not self.lease_id:
            raise StackLeaseProtocolError("lease id factory returned an invalid id")
        stack_lock = _RepositoryIdentityLock(
            repo_root=self.repo_root,
            lease_id=self.lease_id,
        )
        try:
            stack_lock.acquire()
        except IdentityLeaseBusyError:
            self._restored = True
            raise
        self._stack_lock = stack_lock
        try:
            self.secret = self.secret_factory()
            if not isinstance(self.secret, bytes) or len(self.secret) != 32:
                raise StackLeaseProtocolError(
                    "owner secret factory must return 32 bytes",
                )
        except BaseException:
            self.secret = b""
            self._restored = True
            self._stack_lock.release()
            self._stack_lock = None
            raise
        self.environ.update({
            "E2E_IDENTITY_ENABLED": "true",
            "E2E_IDENTITY_SECRET": encode_secret(self.secret),
            "E2E_STACK_LEASE_ID": self.lease_id,
            "E2E_STACK_LEASE_ROLE": "owner",
        })
        if self.extra_services:
            self.environ["E2E_NAMESPACE_ADMIN_ENABLED"] = "true"
            self.environ["E2E_NAMESPACE_ADMIN_SECRET"] = encode_secret(self.secret)
        else:
            self.environ.pop("E2E_NAMESPACE_ADMIN_ENABLED", None)
            self.environ.pop("E2E_NAMESPACE_ADMIN_SECRET", None)
        self._enabled = True
        try:
            self.compose(self.environ)
            if not self.ready():
                raise IdentityEnableError("identity_enable")
        except BaseException as exc:
            try:
                self.restore()
            except IdentityCleanupError as cleanup_exc:
                raise cleanup_exc from exc
            raise IdentityEnableError("identity_enable") from exc

    def restore(self) -> None:
        if self._restored:
            return
        if not self._enabled:
            self._clear_process_environment()
            self._restored = True
            return
        self.environ["E2E_IDENTITY_ENABLED"] = "false"
        self.environ.pop("E2E_IDENTITY_SECRET", None)
        if self.extra_services:
            self.environ["E2E_NAMESPACE_ADMIN_ENABLED"] = "false"
        else:
            self.environ.pop("E2E_NAMESPACE_ADMIN_ENABLED", None)
        self.environ.pop("E2E_NAMESPACE_ADMIN_SECRET", None)
        try:
            self.compose(self.environ)
            if not self.ready():
                raise RuntimeError("default stack did not become ready")
        except BaseException as exc:
            raise IdentityCleanupError("identity_cleanup") from exc
        self.secret = b""
        self._clear_process_environment()
        if self._stack_lock is not None:
            self._stack_lock.release()
            self._stack_lock = None
        self._enabled = False
        self._restored = True


def _require_posix_private_metadata(
    metadata: Any,
    *,
    expected_mode: int,
    expected_kind: str,
    current_uid: int,
) -> None:
    mode = metadata.st_mode
    kind_matches = (
        stat.S_ISDIR(mode)
        if expected_kind == "directory"
        else stat.S_ISREG(mode)
    )
    if not kind_matches:
        raise StackLeaseProtocolError(
            f"private {expected_kind} has an invalid file type",
        )
    if metadata.st_uid != current_uid:
        raise StackLeaseProtocolError(
            f"private {expected_kind} has an invalid owner",
        )
    if stat.S_IMODE(mode) != expected_mode:
        raise StackLeaseProtocolError(
            f"private {expected_kind} has invalid permissions",
        )


def _verify_private_posix_path(
    path: Path,
    *,
    expected_mode: int,
    expected_kind: str,
) -> None:
    if os.name == "nt":
        return
    try:
        metadata = path.lstat()
        current_uid = os.getuid()
    except (AttributeError, OSError) as exc:
        raise StackLeaseProtocolError(
            f"cannot inspect private {expected_kind}",
        ) from exc
    _require_posix_private_metadata(
        metadata,
        expected_mode=expected_mode,
        expected_kind=expected_kind,
        current_uid=current_uid,
    )


def _enforce_private_posix_path(
    path: Path,
    *,
    expected_mode: int,
    expected_kind: str,
) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(expected_mode)
    except OSError as exc:
        raise StackLeaseProtocolError(
            f"cannot restrict private {expected_kind} permissions",
        ) from exc
    _verify_private_posix_path(
        path,
        expected_mode=expected_mode,
        expected_kind=expected_kind,
    )


def replace_private_file(
    path: Path,
    content: bytes,
    *,
    temporary_prefix: str,
) -> None:
    """Atomically install owner-private bytes, failing closed on permissions."""
    candidate = Path(path)
    _verify_private_posix_path(
        candidate.parent,
        expected_mode=0o700,
        expected_kind="directory",
    )
    temporary: Path | None = None
    installed = False
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=temporary_prefix,
            suffix=".tmp",
            dir=candidate.parent,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _enforce_private_posix_path(
            temporary,
            expected_mode=0o600,
            expected_kind="file",
        )
        os.replace(temporary, candidate)
        installed = True
        temporary = None
        _verify_private_posix_path(
            candidate,
            expected_mode=0o600,
            expected_kind="file",
        )
        installed = False
    except StackLeaseProtocolError as exc:
        if installed:
            try:
                candidate.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise StackLeaseProtocolError(
                    "cannot remove invalid private token bundle",
                ) from cleanup_exc
        raise
    except OSError as exc:
        if installed:
            try:
                candidate.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise StackLeaseProtocolError(
                    "cannot remove invalid private token bundle",
                ) from cleanup_exc
        raise StackLeaseProtocolError(
            "cannot replace private token bundle",
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _make_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        username = os.environ.get("USERNAME", "")
        domain = os.environ.get("USERDOMAIN", "")
        principal = f"{domain}\\{username}" if domain and username else username
        if not principal:
            raise StackLeaseProtocolError("cannot resolve Windows bundle owner")
        try:
            subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{principal}:(OI)(CI)F",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise StackLeaseProtocolError(
                "cannot restrict token bundle directory ACL",
            ) from exc
        _enforce_private_posix_path(
            path,
            expected_mode=0o700,
            expected_kind="directory",
        )
        return
    _enforce_private_posix_path(
        path,
        expected_mode=0o700,
        expected_kind="directory",
    )


def write_token_bundle(
    *,
    root: Path,
    lease_id: str,
    case_id: str,
    run_id: str,
    user_id: str,
    vehicle_id: str,
    timeout_s: int,
    secret: bytes,
    now: int | None = None,
    memory_sessions: int = 8,
    fixture_dir: Path | None = None,
    fixture_manifest: Path | None = None,
    fixture_manifest_sha256: str | None = None,
    fixture_audio_api_origin: str | None = None,
) -> Path:
    if (
        type(memory_sessions) is not int
        or not 0 <= memory_sessions <= MAX_MEMORY_SESSIONS
    ):
        raise StackLeaseProtocolError(
            "token bundle memory session count is invalid",
        )
    base = Path(root).resolve()
    base.mkdir(parents=True, exist_ok=True)
    directory = base / f"{lease_id}-{case_id}"
    _make_private_directory(directory)
    control_user = user_id + "-control"
    fixture_values = (
        fixture_dir,
        fixture_manifest,
        fixture_manifest_sha256,
        fixture_audio_api_origin,
    )
    if any(value is not None for value in fixture_values) and not all(
        value is not None for value in fixture_values
    ):
        raise StackLeaseProtocolError(
            "token bundle voiceprint fixture attestation is incomplete",
        )
    fixture_attestation = (
        None
        if fixture_dir is None
        else {
            "directory": str(_lexical_absolute(Path(fixture_dir))),
            "manifest": str(_lexical_absolute(Path(fixture_manifest))),
            "manifest_sha256": fixture_manifest_sha256,
            "audio_api_origin": fixture_audio_api_origin,
        }
    )
    payload = {
        "schema_version": 1,
        "lease_id": lease_id,
        "case_id": case_id,
        "run_id": run_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
        "identity_token": sign_identity(
            secret,
            run_id=run_id,
            user_id=user_id,
            vehicle_id=vehicle_id,
            scopes=DEFAULT_SCOPES,
            timeout_s=timeout_s,
            now=now,
        ),
        "control_user_id": control_user,
        "control_identity_token": sign_identity(
            secret,
            run_id=run_id,
            user_id=control_user,
            vehicle_id=vehicle_id,
            scopes=DEFAULT_SCOPES,
            timeout_s=timeout_s,
            now=now,
        ),
        "voiceprint_fixture": fixture_attestation,
        # Legacy wire name. Placeholder values are replaced by capabilities
        # before the bundle is exposed to the child.
        "memory_session_ids": [
            f"{user_id}-session-{number}"
            for number in range(1, memory_sessions + 1)
        ],
    }
    validate_token_bundle_payload(payload, memory_mode="placeholder")
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise StackLeaseProtocolError("token bundle is too large")
    path = (directory / "tokens.json").resolve()
    replace_private_file(
        path,
        encoded,
        temporary_prefix=".token-bundle-",
    )
    load_token_bundle_payload(
        path,
        bundle_root=base,
        memory_mode="placeholder",
    )
    return path


def load_child_bundle(
    path: Path,
    *,
    bundle_root: Path,
    lease_id: str,
    inherited: Mapping[str, str],
    fixture_required: bool = False,
) -> dict[str, str]:
    if "E2E_IDENTITY_SECRET" in inherited:
        raise StackLeaseProtocolError("lease child inherited owner secret")
    if "E2E_IDENTITY_ENABLED" in inherited:
        raise StackLeaseProtocolError(
            "lease child inherited owner identity state",
        )
    if _CAPABILITY_OWNER_KEYS & set(inherited):
        raise StackLeaseProtocolError(
            "lease child inherited owner capability state",
        )
    if _NAMESPACE_ADMIN_OWNER_KEYS & set(inherited):
        raise StackLeaseProtocolError(
            "lease child inherited owner namespace admin state",
        )
    payload = load_token_bundle_payload(
        Path(path),
        bundle_root=Path(bundle_root),
        memory_mode="capability",
    )
    if payload["lease_id"] != lease_id:
        raise StackLeaseProtocolError("token bundle lease does not match child")
    fixture = _validate_fixture_attestation(
        payload,
        bundle_root=Path(bundle_root),
    )
    if fixture_required and fixture is None:
        raise StackLeaseProtocolError(
            "lease child is missing voiceprint fixture attestation",
        )
    env = {
        key: value
        for key, value in inherited.items()
        if key not in {
            "E2E_IDENTITY_ENABLED",
            "E2E_IDENTITY_SECRET",
            "E2E_CAPABILITY_ENABLED",
            "E2E_CAPABILITY_SECRET",
            "E2E_NAMESPACE_ADMIN_ENABLED",
            "E2E_NAMESPACE_ADMIN_SECRET",
            "E2E_STACK_LEASE_ID",
            "E2E_STACK_LEASE_ROLE",
        }
        and key not in _FIXTURE_ENV_KEYS
    }
    env.update({
        "E2E_RUN_ID": payload["run_id"],
        "E2E_TEST_ID": payload["case_id"],
        "E2E_USER_ID": payload["user_id"],
        "E2E_SESSION_PREFIX": f"{payload['user_id']}-session",
        "E2E_IDENTITY_TOKEN": payload["identity_token"],
        "E2E_EXPECTED_VEHICLE_ID": payload["vehicle_id"],
        "E2E_CONTROL_USER_ID": payload["control_user_id"],
        "E2E_CONTROL_IDENTITY_TOKEN": payload["control_identity_token"],
        # Legacy env name: serialized values are capabilities, not sessions.
        "E2E_MEMORY_SESSION_IDS": json.dumps(
            payload["memory_session_ids"],
            separators=(",", ":"),
        ),
        "E2E_STACK_LEASE_ID": lease_id,
        "E2E_STACK_LEASE_ROLE": "child",
    })
    if fixture is not None:
        env.update({
            "E2E_VOICEPRINT_FIXTURE_DIR": fixture["directory"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST": fixture["manifest"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": (
                fixture["manifest_sha256"]
            ),
            "E2E_AUDIO_API_ORIGIN": fixture["audio_api_origin"],
        })
    return env


def wait_for_children(children: Sequence[Any]) -> list[int]:
    outcomes: list[int] = []
    for child in children:
        try:
            code = child.wait()
            outcomes.append(code if type(code) is int else 1)
        except BaseException:
            outcomes.append(1)
    return outcomes


def aggregate_return_codes(return_codes: Sequence[int]) -> int:
    return 0 if return_codes and all(code == 0 for code in return_codes) else 1


def parallel_subrun_argv(
    *,
    repo_root: Path,
    case_id: str,
    lane: str,
    milestone: str,
    lease_id: str,
    token_bundle: Path,
) -> list[str]:
    bundle = Path(token_bundle)
    if not bundle.is_absolute():
        raise StackLeaseProtocolError("parallel token bundle must be absolute")
    bundle_root = bundle.parent.parent.resolve()
    return [
        sys.executable,
        str(Path(repo_root).resolve() / "scripts" / "run_e2e.py"),
        "--lane",
        lane,
        "--milestone",
        milestone,
        "--id",
        case_id,
        "--lease-child",
        "--lease-id",
        lease_id,
        "--token-bundle",
        str(bundle.resolve()),
        "--token-bundle-root",
        str(bundle_root),
    ]

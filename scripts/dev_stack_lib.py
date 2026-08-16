"""Safe, shared development-stack target parsing and endpoint resolution."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
import json
import socket
import ssl
from typing import Iterable, Literal, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.cloud_release_lib import (
    CommandResult,
    ReleaseError,
    ReleaseRequest,
    SubprocessRunner,
    discover_remote_state,
)


class DevStackError(RuntimeError):
    """Raised when development-stack configuration is unsafe to use."""


@dataclass(frozen=True)
class TargetSelection:
    name: Literal["local", "cloud"]
    source: Literal["default", "file", "argument"]


@dataclass(frozen=True)
class StackEndpoints:
    hmi: str
    edge_http: str
    edge_ws: str
    audio: str
    dashboard: str
    collector_http: str
    collector_ws: str


@dataclass(frozen=True)
class HttpResponse:
    """The deliberately small HTTP result used by read-only status probes."""

    status_code: int


@dataclass(frozen=True)
class EndpointStatus:
    name: str
    url: str
    status: Literal[
        "healthy",
        "http_error",
        "timeout",
        "tls_error",
        "dns_error",
        "network_error",
        "invalid_endpoint",
    ]
    http_status: int | None


@dataclass(frozen=True)
class StackStatus:
    target: Literal["local", "cloud"]
    release_sha: str | None
    container_total: int | None
    container_running: int | None
    healthy_endpoints: int
    endpoint_results: tuple[EndpointStatus, ...]
    warnings: tuple[str, ...]


class StackStatusRunner(Protocol):
    """Injectable command and HTTP boundary for status-only callers and tests."""

    def run(self, argv: Sequence[str], *, cwd: Path, **kwargs) -> CommandResult:
        pass

    def get(self, url: str, *, timeout_s: float) -> HttpResponse:
        pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


class DefaultStackStatusRunner:
    """Production runner with no mutating command or HTTP capability."""

    def __init__(self) -> None:
        self._commands = SubprocessRunner()
        self._opener = build_opener(_NoRedirectHandler())

    def run(self, argv: Sequence[str], *, cwd: Path, **kwargs) -> CommandResult:
        return self._commands.run(argv, cwd=cwd, **kwargs)

    def get(self, url: str, *, timeout_s: float) -> HttpResponse:
        request = Request(url, method="GET")
        with self._opener.open(request, timeout=timeout_s) as response:
            return HttpResponse(status_code=int(response.getcode()))


LOCAL_ENDPOINTS = StackEndpoints(
    hmi="http://localhost:5173",
    edge_http="http://localhost:8090",
    edge_ws="ws://localhost:8090/ws",
    audio="http://localhost:50059",
    dashboard="http://localhost:5174",
    collector_http="http://localhost:8092",
    collector_ws="ws://localhost:8092/stream",
)

TAILNET_FQDN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FQDN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_TARGETS = frozenset(("local", "cloud"))
TARGET_FILE_MAX_BYTES = 4 * 1024
ROOT_ENV_MAX_BYTES = 1024 * 1024
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _target_path(repo_root: Path) -> Path:
    return Path(repo_root) / "dev-stack.local"


def _is_regular_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _lstat_regular_file(
    path: Path, *, error_message: str, missing_ok: bool = False
) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise DevStackError(error_message) from None
    except OSError as exc:
        raise DevStackError(error_message) from exc
    if not _is_regular_file(info):
        raise DevStackError(error_message)
    return info


def _read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    error_message: str,
    missing_ok: bool = False,
) -> bytes | None:
    """Read one bounded regular file without following links or reparse points."""
    before = _lstat_regular_file(
        path, error_message=error_message, missing_ok=missing_ok
    )
    if before is None:
        return None

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DevStackError(error_message) from exc
    try:
        opened = os.fstat(fd)
        current = _lstat_regular_file(path, error_message=error_message)
        if (
            not _is_regular_file(opened)
            or not _same_file(before, opened)
            or not _same_file(opened, current)
            or opened.st_size > max_bytes
        ):
            raise DevStackError(error_message)

        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise DevStackError(error_message)
            chunks.append(chunk)
            remaining -= len(chunk)

        final = os.fstat(fd)
        final_path = _lstat_regular_file(path, error_message=error_message)
        if (
            not _same_file(opened, final)
            or not _same_file(final, final_path)
            or final.st_size != opened.st_size
        ):
            raise DevStackError(error_message)
        return b"".join(chunks)
    except OSError as exc:
        raise DevStackError(error_message) from exc
    finally:
        os.close(fd)


def _verify_owned_regular_file(
    fd: int, path: Path, owned: os.stat_result, *, error_message: str
) -> None:
    opened = os.fstat(fd)
    current = _lstat_regular_file(path, error_message=error_message)
    if (
        not _is_regular_file(opened)
        or not _same_file(owned, opened)
        or not _same_file(opened, current)
    ):
        raise DevStackError(error_message)


def _unlink_owned_regular_file(path: Path, owned: os.stat_result) -> None:
    try:
        current = _lstat_regular_file(
            path, error_message="development stack target cannot be written safely"
        )
        if _same_file(owned, current):
            path.unlink()
    except (DevStackError, OSError):
        pass


def resolve_target(repo_root: Path, target: str | None = None) -> TargetSelection:
    """Resolve an explicit target or the strict repository-root target file."""
    if target is not None:
        if target not in _TARGETS:
            raise DevStackError("development stack target is invalid")
        return TargetSelection(name=target, source="argument")

    raw = _read_regular_file(
        _target_path(repo_root),
        max_bytes=TARGET_FILE_MAX_BYTES,
        error_message="development stack target is invalid",
        missing_ok=True,
    )
    if raw is None:
        return TargetSelection(name="local", source="default")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DevStackError("development stack target is invalid") from exc

    values = {
        "target=local": "local",
        "target=cloud": "cloud",
    }
    lines = text.splitlines()
    if not lines or any(lines[1:]):
        raise DevStackError("development stack target is invalid")
    name = values.get(lines[0])
    if name is None:
        raise DevStackError("development stack target is invalid")
    return TargetSelection(name=name, source="file")


def set_target(repo_root: Path, target: str) -> None:
    """Atomically persist an exact local/cloud selector without touching ``.env``."""
    if target not in _TARGETS:
        raise DevStackError("development stack target is invalid")

    error_message = "development stack target cannot be written safely"
    path = _target_path(repo_root)
    _lstat_regular_file(path, error_message=error_message, missing_ok=True)
    temporary: Path | None = None
    owned_temporary: os.stat_result | None = None
    fd: int | None = None
    payload = f"target={target}\n".encode("ascii")
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        temporary = Path(temporary_name)
        owned_temporary = os.fstat(fd)
        if not _is_regular_file(owned_temporary):
            raise DevStackError(error_message)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(fd)
        _verify_owned_regular_file(
            fd, temporary, owned_temporary, error_message=error_message
        )
        os.close(fd)
        fd = None
        os.replace(temporary, path)
        written = _read_regular_file(
            path,
            max_bytes=TARGET_FILE_MAX_BYTES,
            error_message=error_message,
        )
        if written != payload:
            raise DevStackError(error_message)
    except OSError as exc:
        raise DevStackError(error_message) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None and owned_temporary is not None:
            _unlink_owned_regular_file(temporary, owned_temporary)


def cloud_endpoints(fqdn: str) -> StackEndpoints:
    """Build the one approved remote endpoint set for a Tailnet FQDN."""
    if (
        not TAILNET_FQDN_RE.fullmatch(fqdn)
        or len(fqdn) > 253
        or any(
            len(label) > 63 or not _FQDN_LABEL_RE.fullmatch(label)
            for label in fqdn.split(".")
        )
    ):
        raise DevStackError("TAILNET_FQDN is missing or invalid")
    return StackEndpoints(
        hmi=f"https://{fqdn}",
        edge_http=f"https://{fqdn}:8443",
        edge_ws=f"wss://{fqdn}:8443/ws",
        audio=f"https://{fqdn}:8444",
        dashboard=f"https://{fqdn}:8445",
        collector_http=f"https://{fqdn}:8446",
        collector_ws=f"wss://{fqdn}:8446/stream",
    )


def _parse_env_value(value: str) -> str:
    """Parse a non-executing dotenv value; expansion syntax is always literal."""
    if not value or value[0] not in "\"'":
        uncommented = value.strip()
        for index, character in enumerate(uncommented):
            if character == "#" and (index == 0 or uncommented[index - 1].isspace()):
                uncommented = uncommented[:index].rstrip()
                break
        if "\"" in uncommented or "'" in uncommented:
            raise ValueError("unquoted quote")
        return uncommented

    quote = value[0]
    parsed: list[str] = []
    escaped = False
    for index, character in enumerate(value[1:], start=1):
        if escaped:
            if character in (quote, "\\"):
                parsed.append(character)
            else:
                parsed.extend(("\\", character))
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character != quote:
            parsed.append(character)
            continue
        trailing = value[index + 1 :]
        if trailing.strip() and not trailing.lstrip().startswith("#"):
            raise ValueError("trailing value content")
        return "".join(parsed)
    raise ValueError("unterminated quote")


def read_root_env(repo_root: Path, keys: Iterable[str]) -> dict[str, str]:
    """Read requested root ``.env`` values with a deliberately non-shell parser."""
    if isinstance(keys, str):
        raise DevStackError("root environment request is invalid")
    try:
        requested = set(keys)
    except TypeError as exc:
        raise DevStackError("root environment request is invalid") from exc
    if any(
        not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key)
        for key in requested
    ):
        raise DevStackError("root environment request is invalid")
    path = Path(repo_root) / ".env"
    raw = _read_regular_file(
        path,
        max_bytes=ROOT_ENV_MAX_BYTES,
        error_message="root environment is unreadable",
    )
    assert raw is not None
    if b"\0" in raw:
        raise DevStackError("root environment is unreadable")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DevStackError("root environment is unreadable") from exc

    parsed: dict[str, str] = {}
    try:
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                raise ValueError("missing equals")
            key, value = line.split("=", 1)
            if not _ENV_KEY_RE.fullmatch(key) or key in parsed:
                raise ValueError("invalid key")
            parsed[key] = _parse_env_value(value)
    except ValueError as exc:
        raise DevStackError("root environment is unreadable") from exc

    return {key: parsed[key] for key in requested if key in parsed}


_STATUS_ENDPOINTS = (
    ("hmi", "hmi", "/"),
    ("edge", "edge_http", "/healthz"),
    ("audio", "audio", "/api/llm/providers"),
    ("dashboard", "dashboard", "/"),
    ("collector", "collector_http", "/healthz"),
)
_REQUIRED_LOCAL_SERVICES = frozenset(
    (
        "postgres",
        "redis",
        "edge-gateway",
        "llm-gateway",
        "observability-collector",
        "hmi",
        "dashboard",
    )
)
def _status_url(base: str, path: str) -> str | None:
    """Return a public HTTP URL with credentials, query, and fragment removed."""
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    return f"{parsed.scheme}://{parsed.hostname}{port}{path}"


def _endpoint_status(
    name: str, base: str, path: str, runner: StackStatusRunner
) -> EndpointStatus:
    url = _status_url(base, path)
    if url is None:
        return EndpointStatus(name, "<invalid endpoint>", "invalid_endpoint", None)
    try:
        response = runner.get(url, timeout_s=3.0)
        code = int(response.status_code)
    except HTTPError as exc:
        return EndpointStatus(name, url, "http_error", int(exc.code))
    except TimeoutError:
        return EndpointStatus(name, url, "timeout", None)
    except ssl.SSLError:
        return EndpointStatus(name, url, "tls_error", None)
    except URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            return EndpointStatus(name, url, "dns_error", None)
        if isinstance(exc.reason, ssl.SSLError):
            return EndpointStatus(name, url, "tls_error", None)
        if isinstance(exc.reason, TimeoutError):
            return EndpointStatus(name, url, "timeout", None)
        return EndpointStatus(name, url, "network_error", None)
    except ReleaseError:
        return EndpointStatus(name, url, "network_error", None)
    except (ConnectionError, socket.gaierror):
        return EndpointStatus(name, url, "network_error", None)
    if 200 <= code < 300:
        return EndpointStatus(name, url, "healthy", code)
    return EndpointStatus(name, url, "http_error", code)


def _inspect_endpoints(
    endpoints: StackEndpoints, runner: StackStatusRunner
) -> tuple[EndpointStatus, ...]:
    return tuple(
        _endpoint_status(name, getattr(endpoints, attribute), path, runner)
        for name, attribute, path in _STATUS_ENDPOINTS
    )


def _parse_compose_ps(payload: str) -> tuple[tuple[str, bool], ...] | None:
    """Safely accept Compose's array or one-JSON-object-per-line formats."""
    if not payload.strip():
        return ()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        decoded_items: list[object] = []
        try:
            for line in payload.splitlines():
                if line.strip():
                    decoded_items.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    else:
        decoded_items = decoded if isinstance(decoded, list) else [decoded]

    parsed: list[tuple[str, bool]] = []
    for item in decoded_items:
        if not isinstance(item, dict):
            return None
        service = item.get("Service", item.get("service"))
        state = item.get("State", item.get("state"))
        if not isinstance(service, str) or not isinstance(state, str):
            return None
        parsed.append((service, state.lower() == "running"))
    return tuple(parsed)


def _local_container_status(
    repo: Path, runner: StackStatusRunner
) -> tuple[int | None, int | None, tuple[str, ...]]:
    try:
        info = runner.run(("docker", "info"), cwd=repo, check=False)
    except (OSError, ReleaseError):
        return None, None, ("local Docker daemon is unavailable",)
    if info.returncode != 0:
        return None, None, ("local Docker daemon is unavailable",)
    try:
        compose = runner.run(
            (
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "ps",
                "--all",
                "--format",
                "json",
            ),
            cwd=repo,
            check=False,
        )
    except (OSError, ReleaseError):
        return None, None, ("local Compose status is unavailable",)
    if compose.returncode != 0:
        return None, None, ("local Compose status is unavailable",)
    containers = _parse_compose_ps(compose.stdout)
    if containers is None:
        return None, None, ("local Compose status returned invalid JSON",)
    observed: dict[str, bool] = {}
    for service, running in containers:
        observed[service] = observed.get(service, False) or running
    warnings = tuple(
        f"required local service is {'not running' if service in observed else 'missing'}: {service}"
        for service in sorted(_REQUIRED_LOCAL_SERVICES)
        if not observed.get(service, False)
    )
    return len(containers), sum(running for _, running in containers), warnings


def inspect_local_status(
    repo: Path, endpoints: StackEndpoints, runner: StackStatusRunner
) -> StackStatus:
    """Read local Compose state and five public development endpoints; never start it."""
    total, running, warnings = _local_container_status(Path(repo), runner)
    endpoint_results = _inspect_endpoints(endpoints, runner)
    return StackStatus(
        target="local",
        release_sha=None,
        container_total=total,
        container_running=running,
        healthy_endpoints=sum(item.status == "healthy" for item in endpoint_results),
        endpoint_results=endpoint_results,
        warnings=warnings,
    )


def inspect_cloud_status(
    cloud_request: ReleaseRequest, endpoints: StackEndpoints, runner: StackStatusRunner
) -> StackStatus:
    """Read the deployed release and five cloud endpoints without deploying anything."""
    warnings: list[str] = []
    release_sha: str | None = None
    try:
        state = discover_remote_state(cloud_request, runner=runner)
    except (ReleaseError, OSError):
        warnings.append("remote cloud status is unavailable")
    else:
        release_sha = state.current_release
        if not state.release_lock_available:
            warnings.append("remote release lock is unavailable")
        if not state.runtime_project_ready:
            warnings.append("remote runtime project is not ready")
        if not state.shared_scripts_ready:
            warnings.append("remote shared scripts are not ready")
        if not state.shared_models_ready:
            warnings.append("remote shared models are not ready")
        if state.disk_available_bytes <= 0 or state.memory_available_bytes <= 0:
            warnings.append("remote resource availability is invalid")
    endpoint_results = _inspect_endpoints(endpoints, runner)
    return StackStatus(
        target="cloud",
        release_sha=release_sha,
        container_total=None,
        container_running=None,
        healthy_endpoints=sum(item.status == "healthy" for item in endpoint_results),
        endpoint_results=endpoint_results,
        warnings=tuple(warnings),
    )


def stack_status_to_dict(status: StackStatus) -> dict[str, object]:
    """Serialize only allow-listed, already-redacted status fields for the CLI."""
    return {
        "target": status.target,
        "release_sha": status.release_sha,
        "container_total": status.container_total,
        "container_running": status.container_running,
        "healthy_endpoints": status.healthy_endpoints,
        "endpoint_results": [
            {
                "name": result.name,
                "url": result.url,
                "status": result.status,
                "http_status": result.http_status,
            }
            for result in status.endpoint_results
        ],
        "warnings": list(status.warnings),
    }

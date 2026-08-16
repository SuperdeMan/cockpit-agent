"""Safe, shared development-stack target parsing and endpoint resolution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


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
_TARGETS = frozenset(("local", "cloud"))


def _target_path(repo_root: Path) -> Path:
    return Path(repo_root) / "dev-stack.local"


def _read_regular_file(path: Path, *, error_message: str) -> bytes:
    if path.is_symlink():
        raise DevStackError(error_message)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DevStackError(error_message) from exc


def resolve_target(repo_root: Path, target: str | None = None) -> TargetSelection:
    """Resolve an explicit target or the strict repository-root target file."""
    if target is not None:
        if target not in _TARGETS:
            raise DevStackError("development stack target is invalid")
        return TargetSelection(name=target, source="argument")

    path = _target_path(repo_root)
    if path.is_symlink():
        raise DevStackError("development stack target is invalid")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return TargetSelection(name="local", source="default")
    except OSError as exc:
        raise DevStackError("development stack target is invalid") from exc

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

    path = _target_path(repo_root)
    partial = path.with_name(f"{path.name}.partial")
    if path.is_symlink() or partial.exists() or partial.is_symlink():
        raise DevStackError("development stack target cannot be written safely")

    owned_partial: tuple[int, int] | None = None
    try:
        with partial.open("xb") as handle:
            stat = os.fstat(handle.fileno())
            owned_partial = (stat.st_dev, stat.st_ino)
            handle.write(f"target={target}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    except OSError as exc:
        raise DevStackError("development stack target cannot be written safely") from exc
    finally:
        if owned_partial is not None:
            try:
                current = partial.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == owned_partial:
                    partial.unlink(missing_ok=True)
            except OSError:
                pass


def cloud_endpoints(fqdn: str) -> StackEndpoints:
    """Build the one approved remote endpoint set for a Tailnet FQDN."""
    if not TAILNET_FQDN_RE.fullmatch(fqdn):
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
    if not value or value[0] not in "\"'":
        if "\"" in value or "'" in value:
            raise ValueError("unmatched quote")
        return value
    quote = value[0]
    if len(value) < 2 or not value.endswith(quote):
        raise ValueError("unterminated quote")
    return value[1:-1]


def read_root_env(repo_root: Path, keys: Iterable[str]) -> dict[str, str]:
    """Read requested root ``.env`` values with a deliberately non-shell parser."""
    requested = set(keys)
    path = Path(repo_root) / ".env"
    raw = _read_regular_file(path, error_message="root environment is unreadable")
    if b"\0" in raw:
        raise DevStackError("root environment is unreadable")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DevStackError("root environment is unreadable") from exc

    parsed: dict[str, str] = {}
    try:
        for line in text.splitlines():
            if not line or line.startswith("#"):
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

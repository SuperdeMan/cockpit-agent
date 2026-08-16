"""Safe, shared development-stack target parsing and endpoint resolution."""

from __future__ import annotations

import os
import re
import stat
import tempfile
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
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        temporary = Path(temporary_name)
        owned_temporary = os.fstat(fd)
        if not _is_regular_file(owned_temporary):
            raise DevStackError(error_message)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(f"target={target}\n".encode("ascii"))
            handle.flush()
            os.fsync(fd)
        _verify_owned_regular_file(
            fd, temporary, owned_temporary, error_message=error_message
        )
        os.close(fd)
        fd = None
        os.replace(temporary, path)
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
        uncommented = value.split("#", 1)[0].rstrip()
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

"""Structured, fail-closed result helpers for repository E2E scripts."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
import tempfile
import traceback as traceback_module
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any
import urllib.error
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


SCHEMA_VERSION = 1
APPROVED_SKIP_CODES = frozenset({
    "credential_unavailable",
    "profile_unavailable",
    "provider_unavailable",
    "hardware_unavailable",
    "data_unavailable",
    "manual_review_required",
})

_COUNT_KEYS = ("selected", "executed", "passed", "failed", "skipped")
_STATUSES = frozenset({"pass", "pass_with_skips", "skip", "fail"})
_RUN_ID_RE = re.compile(r"e2e-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_TEST_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_SUFFIX_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_CASE_ID_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]*\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_SENSITIVE_LABEL = (
    r"(?:token|secret|password|credential|authorization|sentinel|"
    r"(?:api|private|access)(?:[_ -]?key))"
)
_SENSITIVE_KEY_RE = re.compile(_SENSITIVE_LABEL, re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"\b({_SENSITIVE_LABEL})\b[\"']?\s*[:=]\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_SENSITIVE_MAPPING_RE = re.compile(
    r"([\"'](?:key|api[_ -]?key|x-api-key|authorization)[\"']\s*:\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_E2E_TOKEN_RE = re.compile(
    r"\be2e(?:-mem)?\.v1\.[A-Za-z0-9._~+/=?%-]+",
)
_E2E_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTHORIZATION|SENTINEL|"
    r"(?:API|PRIVATE|ACCESS)_?KEY)",
)
_PROTOCOL_ENV_NAMES = frozenset({
    "E2E_RUN_ID",
    "E2E_TEST_ID",
    "E2E_USER_ID",
    "E2E_SESSION_PREFIX",
    "E2E_RESULT_FILE",
    "E2E_ARTIFACT_DIR",
    "E2E_LANE",
    "E2E_PROFILE",
    "E2E_IDENTITY_TOKEN",
    "E2E_CONTROL_USER_ID",
    "E2E_CONTROL_IDENTITY_TOKEN",
    "E2E_MEMORY_SESSION_IDS",
    "E2E_STACK_LEASE_ID",
    "E2E_STACK_LEASE_ROLE",
    "E2E_EXPECTED_VEHICLE_ID",
    "E2E_VOICEPRINT_FIXTURE_DIR",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST",
    "WS_URL",
})
_MAX_METADATA_DEPTH = 32
_MAX_MEMORY_SESSIONS = 64
_MAX_MEMORY_SESSION_ENV_BYTES = 512 * 1024


class ProtocolError(ValueError):
    """The child result or its runtime namespace violates the E2E contract."""


class CleanupFailure(ProtocolError):
    """One or more registered cleanup callbacks failed."""


class E2EExecutionFailure(ProtocolError):
    """A body exception could not be made safe enough to rethrow."""


def is_network_timeout(exc: BaseException) -> bool:
    """Return whether a direct or exception-chain-wrapped failure timed out."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        if isinstance(current, urllib.error.URLError):
            reason = current.reason
            if isinstance(reason, BaseException):
                pending.append(reason)
        for nested in (current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _source_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _source_literal(node.left)
        right = _source_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def assert_persistent_source_contract(source: str) -> None:
    """Reject ownership guesses and process-wide cleanup in persistent E2Es."""

    if not isinstance(source, str) or not source:
        raise ProtocolError("persistent E2E source must be non-empty text")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ProtocolError("persistent E2E source is invalid Python") from exc

    violations: set[str] = set()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        text = _source_literal(node)
        if text is not None:
            compact = " ".join(text.lower().split())
            if re.search(r"\bu1\b", compact):
                violations.add("fixed_user")
            if "order by created_at desc limit 1" in compact:
                violations.add("latest_row")
            if (
                re.search(r"\bselect count\(\*\) from [a-z_][a-z0-9_.]*\s*;?\Z", compact)
                is not None
            ):
                violations.add("global_count")
            if re.search(r"\bdelete from\b", compact) and " where " not in compact:
                violations.add("delete_without_where")
            if (
                re.search(r"\b(?:delete|update)\b", compact)
                and " like " in compact
                and re.search(r"['\"]%?e2e[-_.].*%?['\"]", compact)
            ):
                violations.add("wide_prefix_cleanup")
            if compact in {"flushdb", "flushall"}:
                violations.add("redis_database_cleanup")

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            target_names = {
                child.id.lower()
                for target in targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name)
            }
            value = node.value
            unstable = any(
                (
                    isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id.lower() in {"time", "random", "uuid"}
                )
                or (
                    isinstance(child.func, ast.Name)
                    and child.func.id.lower() in {"uuid4"}
                )
                for child in ast.walk(value)
                if isinstance(child, ast.Call)
            )
            if unstable and any(
                name in {"session", "sid", "session_id", "task_id", "user_id"}
                or name.endswith(("_session", "_session_id", "_task_id"))
                for name in target_names
            ):
                violations.add("unstable_namespace")

        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id.lower()
        elif isinstance(func, ast.Attribute):
            name = func.attr.lower()
        if name in {"flushdb", "flushall"}:
            violations.add("redis_database_cleanup")
        if name.startswith("reset_") or name.startswith("cleanup_stale"):
            violations.add("global_pre_run_cleanup")
        for keyword in node.keywords:
            if (
                keyword.arg in {"session_id", "task_id", "user_id"}
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                violations.add("fixed_namespace")
        call_literals = {
            child.value.lower()
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if {"docker", "compose", "restart"} <= call_literals:
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                owner = parents.get(owner)
            owner_name = owner.name.lower() if owner is not None else ""
            if not owner_name.startswith(("inject_", "simulate_")):
                violations.add("global_pre_run_cleanup")
    if violations:
        raise ProtocolError(
            "persistent E2E source violates isolation contract: "
            + ",".join(sorted(violations)),
        )


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _required_env(name: str, env: Mapping[str, str]) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"required E2E environment variable is missing: {name}")
    return value


def _test_id(*, env: Mapping[str, str] | None = None) -> str:
    source = _environment(env)
    value = _required_env("E2E_TEST_ID", source)
    if _TEST_ID_RE.fullmatch(value) is None:
        raise ProtocolError("E2E_TEST_ID is not a stable test identifier")
    return value


def run_id(*, env: Mapping[str, str] | None = None) -> str:
    """Return the runner namespace, rejecting missing or non-``e2e-`` IDs."""

    source = _environment(env)
    value = _required_env("E2E_RUN_ID", source)
    if _RUN_ID_RE.fullmatch(value) is None:
        raise ProtocolError("E2E_RUN_ID must be a stable lowercase e2e-* identifier")
    return value


def _base_user_id(*, env: Mapping[str, str] | None = None) -> str:
    source = _environment(env)
    expected = f"{run_id(env=source)}-{_test_id(env=source)}"
    actual = _required_env("E2E_USER_ID", source)
    if actual != expected:
        raise ProtocolError("E2E_USER_ID does not match the current run/test namespace")
    return actual


def _validate_suffix(suffix: str) -> None:
    if not isinstance(suffix, str) or _SUFFIX_RE.fullmatch(suffix) is None:
        raise ProtocolError("namespace suffix must contain stable lowercase components")


def user_id(
    suffix: str = "",
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the exact runner user or a safely derived child user."""

    base = _base_user_id(env=env)
    if suffix == "":
        return base
    _validate_suffix(suffix)
    return f"{base}-{suffix}"


def control_user_id(
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return the optional, runner-issued control user."""

    source = _environment(env)
    value = source.get("E2E_CONTROL_USER_ID")
    if value is None or value == "":
        return None
    expected = user_id("control", env=source)
    if value != expected:
        raise ProtocolError(
            "E2E_CONTROL_USER_ID must be the exact current namespace control user",
        )
    return value


def _session_prefix(*, env: Mapping[str, str] | None = None) -> str:
    source = _environment(env)
    expected = f"{_base_user_id(env=source)}-session"
    actual = _required_env("E2E_SESSION_PREFIX", source)
    if actual != expected:
        raise ProtocolError(
            "E2E_SESSION_PREFIX does not match the current run/test namespace",
        )
    return actual


def session_id(
    number: int,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return a one-based, runner-namespaced session ID."""

    if type(number) is not int or number < 1:
        raise ProtocolError("session number must be a positive integer")
    return f"{_session_prefix(env=env)}-{number}"


def _memory_session_ids(
    env: Mapping[str, str],
    *,
    required: bool,
) -> tuple[str, ...]:
    """Read capabilities from the legacy wire/env name.

    ``E2E_MEMORY_SESSION_IDS`` is retained for compatibility; its values are
    signed capabilities, never business session IDs.
    """
    if "E2E_CAPABILITY_SECRET" in env:
        raise ProtocolError("E2E child inherited memory capability secret")
    raw = env.get("E2E_MEMORY_SESSION_IDS")
    if raw is None or raw == "":
        if required:
            raise ProtocolError("runner did not issue memory session capabilities")
        return ()
    if (
        not isinstance(raw, str)
        or len(raw.encode("utf-8", errors="ignore"))
        > _MAX_MEMORY_SESSION_ENV_BYTES
    ):
        raise ProtocolError("E2E_MEMORY_SESSION_IDS is invalid")
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProtocolError("E2E_MEMORY_SESSION_IDS is invalid JSON") from exc
    if (
        type(values) is not list
        or len(values) > _MAX_MEMORY_SESSIONS
        or any(type(value) is not str or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise ProtocolError("E2E_MEMORY_SESSION_IDS must be a unique token array")

    try:
        from scripts.e2e_identity import (
            IdentityTokenError,
            parse_memory_extraction_session,
        )
    except ImportError as exc:
        raise ProtocolError("memory capability parser is unavailable") from exc

    expected_run = run_id(env=env)
    expected_user = _base_user_id(env=env)
    expected_prefix = _session_prefix(env=env)
    for index, token in enumerate(values, start=1):
        try:
            claims = parse_memory_extraction_session(token)
        except IdentityTokenError as exc:
            raise ProtocolError("runner issued an invalid memory session") from exc
        if (
            claims.run_id != expected_run
            or claims.user_id != expected_user
            or claims.session_id != f"{expected_prefix}-{index}"
        ):
            raise ProtocolError(
                "memory session does not match the current run/test namespace",
            )
    return tuple(values)


def memory_capability(
    number: int,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return a one-based runner-signed extraction capability."""

    if type(number) is not int or number < 1:
        raise ProtocolError("memory capability number must be a positive integer")
    values = _memory_session_ids(_environment(env), required=True)
    if number > len(values):
        raise ProtocolError(
            "memory capability number exceeds the issued capability count",
        )
    return values[number - 1]


def memory_session_id(
    number: int,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Deprecated alias returning a capability, not a business session.

    Use :func:`memory_capability` for capabilities and :func:`session_id` for
    observable business session IDs.
    """

    warnings.warn(
        "memory_session_id() is deprecated and returns a capability, not a business session; "
        "use memory_capability()",
        DeprecationWarning,
        stacklevel=2,
    )
    return memory_capability(number, env=env)


def _reject_child_secrets(env: Mapping[str, str]) -> None:
    if (
        "E2E_IDENTITY_SECRET" in env
        or "E2E_CAPABILITY_SECRET" in env
        or "E2E_NAMESPACE_ADMIN_SECRET" in env
    ):
        raise ProtocolError("E2E child inherited a runner signing secret")


def identity_token(
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the runner bearer after strict, secret-free owner self-checks."""

    source = _environment(env)
    _reject_child_secrets(source)
    token = _required_env("E2E_IDENTITY_TOKEN", source)
    try:
        from scripts.e2e_identity import (
            IdentityTokenError,
            parse_identity_claims_unverified,
        )
    except ImportError as exc:
        raise ProtocolError("identity claims parser is unavailable") from exc
    try:
        claims = parse_identity_claims_unverified(token)
    except IdentityTokenError as exc:
        raise ProtocolError("runner issued an invalid identity token") from exc
    expected = (
        run_id(env=source),
        _base_user_id(env=source),
        _required_env("E2E_EXPECTED_VEHICLE_ID", source),
    )
    actual = (claims.run_id, claims.user_id, claims.vehicle_id)
    if actual != expected:
        raise ProtocolError(
            "identity token does not match the current run/user/vehicle namespace",
        )
    return token


def require_namespaced(
    value: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Fail closed unless *value* belongs to the exact run/test namespace."""

    base = _base_user_id(env=env)
    if not isinstance(value, str):
        raise ProtocolError("cleanup target must be a string identifier")
    if value == base:
        return value
    prefix = f"{base}-"
    if not value.startswith(prefix):
        raise ProtocolError("cleanup target is outside the current run/test namespace")
    suffix = value[len(prefix):]
    if _SUFFIX_RE.fullmatch(suffix) is None:
        raise ProtocolError("cleanup target has an unstable namespace suffix")
    return value


def _resolve_artifact_candidate(root: Path, relative: Path) -> Path:
    """Resolve a candidate so existing symlinks cannot escape *root*."""

    return (root / relative).resolve(strict=False)


def artifact_path(
    relative: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return a bounded artifact path, creating only its parent directory."""

    source = _environment(env)
    text = os.fspath(relative)
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError("artifact path must be a non-empty relative path")
    sensitive_values = _secret_values(source)
    _reject_sensitive_structured_string(
        text,
        sensitive_values,
        "artifact path",
    )
    posix = PurePosixPath(text.replace("\\", "/"))
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ProtocolError("artifact path must be relative")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ProtocolError("artifact path contains an unsafe component")

    artifact_root = Path(_required_env("E2E_ARTIFACT_DIR", source)).resolve(
        strict=False,
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    candidate = _resolve_artifact_candidate(
        artifact_root,
        Path(*posix.parts),
    )
    try:
        candidate.relative_to(artifact_root)
    except ValueError as exc:
        raise ProtocolError("resolved artifact path escapes E2E_ARTIFACT_DIR") from exc
    if candidate == artifact_root:
        raise ProtocolError("artifact path must identify a file")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def ws_url(*, env: Mapping[str, str] | None = None) -> str:
    """Build the authenticated WS URL from the runner-issued token only."""

    source = _environment(env)
    token = _required_env("E2E_IDENTITY_TOKEN", source)
    base = _required_env("WS_URL", source)
    try:
        parsed = urlsplit(base)
    except ValueError as exc:
        raise ProtocolError("WS_URL is invalid") from exc
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc or parsed.fragment:
        raise ProtocolError("WS_URL must be an absolute ws/wss URL without a fragment")
    query = [(key, value) for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ) if key != "token"]
    query.append(("token", token))
    encoded = urlencode(query, doseq=True, quote_via=quote)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, encoded, ""))


def confirm_identity_ack(
    message: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Prove the upgraded WS is owned before any destructive E2E setup."""

    source = _environment(env)
    _required_env("E2E_IDENTITY_TOKEN", source)
    expected = {
        "type": "e2e_identity_ack",
        "run_id": run_id(env=source),
        "user_id": _base_user_id(env=source),
        "vehicle_id": _required_env("E2E_EXPECTED_VEHICLE_ID", source),
    }
    if not isinstance(message, Mapping) or any(
        message.get(key) != value for key, value in expected.items()
    ):
        raise ProtocolError("E2E owner proof ACK does not match this child")
    return dict(message)


def _secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    names = {
        "E2E_IDENTITY_TOKEN",
        "E2E_CONTROL_IDENTITY_TOKEN",
        "E2E_MEMORY_SESSION_IDS",
    }
    names.update(
        key
        for key in env
        if (
            isinstance(key, str)
            and key.startswith("E2E_")
            and _E2E_SENSITIVE_ENV_KEY_RE.search(key.upper()) is not None
        )
    )
    values = set()
    for name in names:
        value = env.get(name)
        if isinstance(value, str) and value and value != "[REDACTED]":
            values.add(value)
            if name == "E2E_MEMORY_SESSION_IDS":
                try:
                    sessions = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    sessions = ()
                if type(sessions) is list:
                    values.update(
                        token
                        for token in sessions
                        if isinstance(token, str) and token
                    )
    return tuple(sorted(values, key=len, reverse=True))


def _snapshot_protocol_environment(
    env: Mapping[str, str],
) -> dict[str, str]:
    names = set(_PROTOCOL_ENV_NAMES)
    names.update(
        key
        for key in env
        if (
            isinstance(key, str)
            and key.startswith("E2E_")
            and _E2E_SENSITIVE_ENV_KEY_RE.search(key.upper()) is not None
        )
    )
    snapshot: dict[str, str] = {}
    for name in names:
        value = env.get(name)
        if isinstance(value, str):
            snapshot[name] = value
    return snapshot


def _redact_text(value: str, sensitive_values: Sequence[str]) -> str:
    if not isinstance(value, str):
        raise ProtocolError("result detail must be a string")
    safe = value
    for secret in sensitive_values:
        safe = safe.replace(secret, "[REDACTED]")
    safe = _SENSITIVE_MAPPING_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        safe,
    )
    safe = _BEARER_RE.sub("Bearer [REDACTED]", safe)
    safe = _E2E_TOKEN_RE.sub("[REDACTED]", safe)
    safe = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        safe,
    )
    return safe


def _safe_detail(value: str, env: Mapping[str, str]) -> str:
    return _redact_text(value, _secret_values(env))


def _contains_secret_value(
    value: str,
    sensitive_values: Sequence[str],
) -> bool:
    return any(secret in value for secret in sensitive_values)


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _contains_normalized_secret(
    value: str,
    sensitive_values: Sequence[str],
) -> bool:
    normalized_value = _normalized_identifier(value)
    for secret in sensitive_values:
        normalized_secret = _normalized_identifier(secret)
        if len(normalized_secret) >= 8 and normalized_secret in normalized_value:
            return True
    return False


def _contains_unsafe_text(
    value: str,
    sensitive_values: Sequence[str] = (),
) -> bool:
    if _contains_secret_value(value, sensitive_values):
        return True
    if _BEARER_RE.search(value) or _E2E_TOKEN_RE.search(value):
        return True
    return any(
        "[REDACTED]" not in match.group(0)
        for match in _SENSITIVE_ASSIGNMENT_RE.finditer(value)
    )


def _reject_sensitive_structured_string(
    value: str,
    sensitive_values: Sequence[str],
    field: str,
    *,
    identifier: bool = False,
) -> None:
    unsafe = _contains_unsafe_text(value, sensitive_values)
    if identifier:
        unsafe = unsafe or _contains_normalized_secret(value, sensitive_values)
    if unsafe:
        raise ProtocolError(f"{field} contains sensitive material")


def _walk_metadata(
    value: Any,
    sensitive_values: Sequence[str],
    *,
    sanitize: bool,
    depth: int = 0,
    ancestors: frozenset[int] = frozenset(),
) -> Any:
    if depth > _MAX_METADATA_DEPTH:
        raise ProtocolError("metadata exceeds maximum nesting depth")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("metadata must contain finite numbers")
        return value
    if isinstance(value, str):
        if sanitize:
            return _redact_text(value, sensitive_values)
        if _contains_unsafe_text(value, sensitive_values):
            raise ProtocolError("result metadata contains sensitive material")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise ProtocolError("metadata contains a reference cycle")
        child_ancestors = ancestors | {identity}
        walked: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("metadata keys must be strings")
            if _contains_secret_value(key, sensitive_values):
                raise ProtocolError("metadata key contains sensitive material")
            if _SENSITIVE_KEY_RE.search(key):
                if sanitize:
                    walked[key] = "[REDACTED]"
                elif item != "[REDACTED]":
                    raise ProtocolError(
                        "sensitive result metadata values must be redacted",
                    )
                else:
                    walked[key] = item
                continue
            walked[key] = _walk_metadata(
                item,
                sensitive_values,
                sanitize=sanitize,
                depth=depth + 1,
                ancestors=child_ancestors,
            )
        return walked
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise ProtocolError("metadata contains a reference cycle")
        child_ancestors = ancestors | {identity}
        return [
            _walk_metadata(
                item,
                sensitive_values,
                sanitize=sanitize,
                depth=depth + 1,
                ancestors=child_ancestors,
            )
            for item in value
        ]
    raise ProtocolError("metadata contains an unsupported value type")


def _safe_metadata(value: Any, sensitive_values: Sequence[str]) -> Any:
    return _walk_metadata(
        value,
        sensitive_values,
        sanitize=True,
    )


def _validate_safe_json(
    value: Any,
    sensitive_values: Sequence[str] = (),
) -> None:
    _walk_metadata(
        value,
        sensitive_values,
        sanitize=False,
    )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze_json(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_case_id(value: Any) -> str:
    if not isinstance(value, str) or _CASE_ID_RE.fullmatch(value) is None:
        raise ProtocolError("case_id must be a stable identifier")
    return value


def _validate_code(value: Any) -> str:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value) is None:
        raise ProtocolError("failure code must be a stable identifier")
    return value


def _normalize_record(
    value: Any,
    *,
    approved_skip: bool,
    sensitive_values: Sequence[str] = (),
) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"case_id", "code", "detail"}:
        raise ProtocolError("result record must contain case_id, code and detail")
    case_id = _validate_case_id(value["case_id"])
    code = _validate_code(value["code"])
    _reject_sensitive_structured_string(
        case_id,
        sensitive_values,
        "case_id",
        identifier=True,
    )
    _reject_sensitive_structured_string(
        code,
        sensitive_values,
        "result code",
        identifier=True,
    )
    if approved_skip and code not in APPROVED_SKIP_CODES:
        raise ProtocolError("skip reason code is not approved")
    detail = value["detail"]
    if (
        not isinstance(detail, str)
        or _contains_unsafe_text(detail, sensitive_values)
    ):
        raise ProtocolError("result detail contains sensitive material")
    return MappingProxyType({
        "case_id": case_id,
        "code": code,
        "detail": detail,
    })


def _normalize_artifact(
    value: Any,
    sensitive_values: Sequence[str] = (),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "metadata"}:
        raise ProtocolError("artifact must contain path and metadata")
    path = value["path"]
    if not isinstance(path, str) or not path:
        raise ProtocolError("artifact path must be a non-empty string")
    _reject_sensitive_structured_string(
        path,
        sensitive_values,
        "artifact result path",
    )
    posix = PurePosixPath(path.replace("\\", "/"))
    windows = PureWindowsPath(path)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ProtocolError("artifact result path must remain relative")
    metadata = value["metadata"]
    _validate_safe_json(metadata, sensitive_values)
    return MappingProxyType({
        "path": posix.as_posix(),
        "metadata": _freeze_json(metadata),
    })


class E2EResult:
    """An immutable, fully validated child result."""

    __slots__ = (
        "schema_version",
        "test_id",
        "run_id",
        "status",
        "counts",
        "skip_reasons",
        "artifacts",
        "failures",
    )

    def __init__(
        self,
        *,
        test_id: str,
        run_id: str,
        status: str,
        counts: Mapping[str, int],
        skip_reasons: Sequence[Mapping[str, str]] = (),
        artifacts: Sequence[Mapping[str, Any]] = (),
        failures: Sequence[Mapping[str, str]] = (),
        schema_version: int = SCHEMA_VERSION,
        sensitive_values: Sequence[str] = (),
    ) -> None:
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise ProtocolError("unsupported E2E result schema_version")
        normalized_sensitive_values: tuple[str, ...] = tuple(
            value
            for value in sensitive_values
            if isinstance(value, str) and value and value != "[REDACTED]"
        )
        if len(normalized_sensitive_values) != len(sensitive_values):
            raise ProtocolError("sensitive_values must contain non-empty strings")
        if not isinstance(test_id, str) or _TEST_ID_RE.fullmatch(test_id) is None:
            raise ProtocolError("result test_id is invalid")
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ProtocolError("result run_id is invalid")
        if status not in _STATUSES:
            raise ProtocolError("result status is invalid")
        if not isinstance(counts, Mapping) or set(counts) != set(_COUNT_KEYS):
            raise ProtocolError("result counts have an invalid shape")
        normalized_counts: dict[str, int] = {}
        for key in _COUNT_KEYS:
            value = counts[key]
            if type(value) is not int or value < 0:
                raise ProtocolError("result counts must be non-negative integers")
            normalized_counts[key] = value
        if (
            normalized_counts["selected"]
            != normalized_counts["executed"] + normalized_counts["skipped"]
            or normalized_counts["executed"]
            != normalized_counts["passed"] + normalized_counts["failed"]
        ):
            raise ProtocolError("result count conservation is broken")

        normalized_skips = tuple(
            _normalize_record(
                item,
                approved_skip=True,
                sensitive_values=normalized_sensitive_values,
            )
            for item in skip_reasons
        )
        normalized_failures = tuple(
            _normalize_record(
                item,
                approved_skip=False,
                sensitive_values=normalized_sensitive_values,
            )
            for item in failures
        )
        normalized_artifacts = tuple(
            _normalize_artifact(
                item,
                sensitive_values=normalized_sensitive_values,
            )
            for item in artifacts
        )
        if len(normalized_skips) != normalized_counts["skipped"]:
            raise ProtocolError("skip_reasons must account for every skipped case")
        if len(normalized_failures) != normalized_counts["failed"]:
            raise ProtocolError("failures must account for every failed case")

        executed = normalized_counts["executed"]
        failed = normalized_counts["failed"]
        skipped = normalized_counts["skipped"]
        valid_status = (
            (status == "pass" and executed > 0 and failed == 0 and skipped == 0)
            or (
                status == "pass_with_skips"
                and executed > 0
                and failed == 0
                and skipped > 0
            )
            or (
                status == "skip"
                and executed == 0
                and skipped >= 1
                and failed == 0
            )
            or (status == "fail" and failed >= 1)
        )
        if not valid_status:
            raise ProtocolError("result status does not match its exact count mapping")

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "test_id", test_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "counts",
            MappingProxyType(normalized_counts),
        )
        object.__setattr__(self, "skip_reasons", normalized_skips)
        object.__setattr__(self, "artifacts", normalized_artifacts)
        object.__setattr__(self, "failures", normalized_failures)

    def __setattr__(self, name: str, value: Any) -> None:
        raise FrozenInstanceError(f"cannot assign to field {name!r}")

    def __delattr__(self, name: str) -> None:
        raise FrozenInstanceError(f"cannot delete field {name!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "test_id": self.test_id,
            "run_id": self.run_id,
            "status": self.status,
            "counts": dict(self.counts),
            "skip_reasons": [_thaw_json(item) for item in self.skip_reasons],
            "artifacts": [_thaw_json(item) for item in self.artifacts],
            "failures": [_thaw_json(item) for item in self.failures],
        }

    def exit_code(self) -> int:
        if self.status in {"pass", "pass_with_skips"}:
            return 0
        if self.status == "skip":
            return 77
        return 1

    def write(self, target: str | os.PathLike[str]) -> Path:
        """Atomically serialize this already-validated result."""

        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(
                    self.to_dict(),
                    stream,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, destination)
        except Exception:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return destination


class CaseRecorder:
    """Record E2E cases and finalize one result on context exit."""

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        source = _environment(env)
        _reject_child_secrets(source)
        self._env = _snapshot_protocol_environment(source)
        self._sensitive_values = _secret_values(self._env)
        self._run_id = run_id(env=self._env)
        self._test_id = _test_id(env=self._env)
        self._user_id = _base_user_id(env=self._env)
        self._session_prefix = _session_prefix(env=self._env)
        control_user_id(env=self._env)
        _memory_session_ids(self._env, required=False)
        self._result_file = Path(_required_env("E2E_RESULT_FILE", self._env))
        _required_env("E2E_ARTIFACT_DIR", self._env)

        self._outcomes: dict[str, str] = {}
        self._skip_reasons: list[dict[str, str]] = []
        self._failures: list[dict[str, str]] = []
        self._artifacts: list[dict[str, Any]] = []
        self._cleanups: list[tuple[str, Callable[[], Any]]] = []
        self._cleanup_ran = False
        self._cleanup_failed = False
        self._finalizing = False
        self._finalization_violations = 0
        self._active = False
        self._finalized = False
        self._written = False
        self._result: E2EResult | None = None

    def __enter__(self) -> "CaseRecorder":
        if self._finalizing:
            self._finalization_violations += 1
            raise ProtocolError("CaseRecorder cannot be entered during cleanup")
        if (
            self._active
            or self._finalized
            or self._result is not None
            or self._written
        ):
            raise ProtocolError("CaseRecorder cannot be reused")
        self._active = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._finalizing:
            self._finalization_violations += 1
            raise ProtocolError("CaseRecorder cannot exit during cleanup")
        if (
            not self._active
            or self._finalized
            or self._result is not None
            or self._written
        ):
            raise ProtocolError("CaseRecorder exit requires one active context")
        if exc is not None:
            code = (
                "assertion_failed"
                if isinstance(exc, AssertionError)
                else "unhandled_exception"
            )
            self._internal_failure(
                "exception",
                code,
                self._exception_detail(exc),
            )
        self._active = False
        self._run_cleanups()
        self._finalize()
        self._write_once()
        if exc is not None:
            safe_exception = self._safe_exception_for_raise(exc)
            if safe_exception is exc:
                return False
            raise safe_exception from None
        if self._cleanup_failed:
            raise CleanupFailure("one or more E2E cleanup callbacks failed")
        return False

    @property
    def result(self) -> E2EResult:
        self._guard_not_finalizing()
        if self._result is None:
            raise ProtocolError("CaseRecorder has not been finalized")
        return self._result

    def run_id(self) -> str:
        return self._run_id

    def user_id(self, suffix: str = "") -> str:
        return user_id(suffix, env=self._env)

    def control_user_id(self) -> str | None:
        return control_user_id(env=self._env)

    def session_id(self, number: int) -> str:
        return session_id(number, env=self._env)

    def memory_capability(self, number: int) -> str:
        self._guard_not_finalizing()
        return memory_capability(number, env=self._env)

    def identity_token(self) -> str:
        self._guard_not_finalizing()
        return identity_token(env=self._env)

    def artifact_path(self, relative: str | os.PathLike[str]) -> Path:
        self._guard_not_finalizing()
        return artifact_path(relative, env=self._env)

    def ws_url(self) -> str:
        self._guard_not_finalizing()
        return ws_url(env=self._env)

    def confirm_identity_ack(self, message: Mapping[str, Any]) -> dict[str, Any]:
        self._guard_not_finalizing()
        return confirm_identity_ack(message, env=self._env)

    def _ensure_recordable(self, case_id: str) -> str:
        self._guard_not_finalizing()
        if self._finalized:
            raise ProtocolError("cannot record after result finalization")
        stable = _validate_case_id(case_id)
        _reject_sensitive_structured_string(
            stable,
            self._sensitive_values,
            "case_id",
            identifier=True,
        )
        if stable in self._outcomes:
            raise ProtocolError("each E2E case_id may be recorded exactly once")
        return stable

    def pass_case(self, case_id: str) -> None:
        stable = self._ensure_recordable(case_id)
        self._outcomes[stable] = "pass"

    def fail_case(self, case_id: str, code: str, detail: str = "") -> None:
        stable = self._ensure_recordable(case_id)
        stable_code = _validate_code(code)
        _reject_sensitive_structured_string(
            stable_code,
            self._sensitive_values,
            "failure code",
            identifier=True,
        )
        safe = _safe_detail(detail, self._env)
        self._outcomes[stable] = "fail"
        self._failures.append({
            "case_id": stable,
            "code": stable_code,
            "detail": safe,
        })

    def skip_case(self, case_id: str, code: str, detail: str = "") -> None:
        stable = self._ensure_recordable(case_id)
        stable_code = _validate_code(code)
        _reject_sensitive_structured_string(
            stable_code,
            self._sensitive_values,
            "skip code",
            identifier=True,
        )
        if stable_code not in APPROVED_SKIP_CODES:
            raise ProtocolError("skip reason code is not approved")
        safe = _safe_detail(detail, self._env)
        self._outcomes[stable] = "skip"
        self._skip_reasons.append({
            "case_id": stable,
            "code": stable_code,
            "detail": safe,
        })

    def add_artifact(
        self,
        relative: str | os.PathLike[str],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        self._guard_not_finalizing()
        if self._finalized:
            raise ProtocolError("cannot add an artifact after result finalization")
        path = artifact_path(relative, env=self._env)
        root = Path(self._env["E2E_ARTIFACT_DIR"]).resolve(strict=False)
        relative_path = path.relative_to(root).as_posix()
        safe_metadata = _safe_metadata(metadata or {}, self._sensitive_values)
        _validate_safe_json(safe_metadata, self._sensitive_values)
        self._artifacts.append({
            "path": relative_path,
            "metadata": safe_metadata,
        })
        return path

    def register_cleanup(
        self,
        target: str,
        callback: Callable[[], Any],
    ) -> None:
        self._guard_not_finalizing()
        if self._finalized or self._cleanup_ran:
            raise ProtocolError("cannot register cleanup after finalization")
        safe_target = require_namespaced(target, env=self._env)
        if not callable(callback):
            raise ProtocolError("cleanup callback must be callable")
        self._cleanups.append((safe_target, callback))

    def _unique_internal_id(self, base: str) -> str:
        candidate = base
        number = 2
        while candidate in self._outcomes:
            candidate = f"{base}-{number}"
            number += 1
        return candidate

    def _internal_failure(self, case_id: str, code: str, detail: str) -> None:
        stable = self._unique_internal_id(case_id)
        self._outcomes[stable] = "fail"
        self._failures.append({
            "case_id": stable,
            "code": _validate_code(code),
            "detail": _safe_detail(detail, self._env),
        })

    def _exception_detail(self, exc: BaseException) -> str:
        try:
            message = str(exc)
        except Exception:
            message = ""
        prefix = type(exc).__name__
        return f"{prefix}: {message}" if message else prefix

    def _guard_not_finalizing(self) -> None:
        if self._finalizing:
            self._finalization_violations += 1
            raise ProtocolError(
                "public result APIs are unavailable while cleanup is finalizing",
            )

    def _safe_exception_arg(self, value: Any) -> Any:
        if isinstance(value, str):
            return _redact_text(value, self._sensitive_values)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return "[REDACTED_OBJECT]"

    def _exception_string_attrs(self, exc: BaseException) -> set[str]:
        names = {
            key
            for key in getattr(exc, "__dict__", {})
            if isinstance(key, str)
        }
        names.update({
            "detail",
            "filename",
            "filename2",
            "message",
            "msg",
            "name",
            "path",
            "reason",
        })
        for cls in type(exc).__mro__:
            slots = cls.__dict__.get("__slots__", ())
            if isinstance(slots, str):
                names.add(slots)
            else:
                names.update(
                    slot
                    for slot in slots
                    if isinstance(slot, str)
                )
        names.difference_update({"args", "__dict__", "__notes__", "__traceback__"})
        return names

    def _sanitize_exception_node(
        self,
        exc: BaseException,
        seen: set[int],
    ) -> bool:
        if id(exc) in seen:
            return False
        seen.add(id(exc))
        try:
            exc.args = tuple(self._safe_exception_arg(arg) for arg in exc.args)

            notes = getattr(exc, "__notes__", None)
            if notes is not None:
                safe_notes = [
                    _redact_text(note, self._sensitive_values)
                    if isinstance(note, str)
                    else "[REDACTED_NOTE]"
                    for note in notes
                ]
                try:
                    exc.__notes__ = safe_notes
                except BaseException:
                    if any(
                        isinstance(note, str)
                        and _contains_unsafe_text(
                            note,
                            self._sensitive_values,
                        )
                        for note in notes
                    ):
                        return False

            for name in self._exception_string_attrs(exc):
                try:
                    value = getattr(exc, name)
                except BaseException:
                    continue
                if not isinstance(value, str):
                    continue
                safe_value = _redact_text(value, self._sensitive_values)
                try:
                    setattr(exc, name, safe_value)
                except BaseException:
                    if safe_value != value:
                        return False

            rendered = str(exc)
            if _contains_unsafe_text(rendered, self._sensitive_values):
                return False
            notes = getattr(exc, "__notes__", ())
            if any(
                isinstance(note, str)
                and _contains_unsafe_text(note, self._sensitive_values)
                for note in notes
            ):
                return False
            for name in self._exception_string_attrs(exc):
                try:
                    value = getattr(exc, name)
                except BaseException:
                    continue
                if (
                    isinstance(value, str)
                    and _contains_unsafe_text(value, self._sensitive_values)
                ):
                    return False

            for chain_name in ("__cause__", "__context__"):
                chain = getattr(exc, chain_name, None)
                if chain is None:
                    continue
                safe_chain = self._sanitize_exception_tree(chain, seen)
                if safe_chain is None:
                    safe_chain = E2EExecutionFailure(
                        "E2E exception chain entry could not be safely rendered",
                    )
                if safe_chain is chain:
                    continue
                try:
                    setattr(exc, chain_name, safe_chain)
                except BaseException:
                    return False
        except BaseException:
            return False
        return True

    def _cycle_exception_marker(self, exc: BaseException) -> BaseException:
        if isinstance(exc, Exception):
            return E2EExecutionFailure("E2E exception cycle omitted")
        return BaseException("E2E exception cycle omitted")

    def _sanitize_exception_group(
        self,
        exc: BaseExceptionGroup,
        seen: set[int],
    ) -> BaseException | None:
        try:
            safe_children: list[BaseException] = []
            for child in exc.exceptions:
                safe_child = self._sanitize_exception_tree(child, seen)
                if safe_child is None:
                    return None
                safe_children.append(safe_child)

            derived = exc.derive(tuple(safe_children))
            message = getattr(exc, "message", "")
            if not isinstance(message, str):
                return None
            safe_message = _redact_text(message, self._sensitive_values)
            safe_group = type(exc)(safe_message, tuple(derived.exceptions))
            if type(safe_group) is not type(exc):
                return None
            safe_group = safe_group.with_traceback(exc.__traceback__)

            notes = getattr(exc, "__notes__", None)
            if notes is not None:
                safe_group.__notes__ = [
                    _redact_text(note, self._sensitive_values)
                    if isinstance(note, str)
                    else "[REDACTED_NOTE]"
                    for note in notes
                ]

            for name in self._exception_string_attrs(exc):
                if name == "message":
                    continue
                try:
                    value = getattr(exc, name)
                except BaseException:
                    continue
                if not isinstance(value, str):
                    continue
                safe_value = _redact_text(value, self._sensitive_values)
                try:
                    setattr(safe_group, name, safe_value)
                except BaseException:
                    if safe_value != value:
                        return None

            for chain_name in ("__cause__", "__context__"):
                chain = getattr(exc, chain_name, None)
                if chain is None:
                    continue
                safe_chain = self._sanitize_exception_tree(chain, seen)
                if safe_chain is None:
                    return None
                setattr(safe_group, chain_name, safe_chain)
            safe_group.__suppress_context__ = exc.__suppress_context__

            if _contains_unsafe_text(
                str(safe_group),
                self._sensitive_values,
            ):
                return None
            if any(
                isinstance(note, str)
                and _contains_unsafe_text(note, self._sensitive_values)
                for note in getattr(safe_group, "__notes__", ())
            ):
                return None
            for name in self._exception_string_attrs(safe_group):
                try:
                    value = getattr(safe_group, name)
                except BaseException:
                    continue
                if (
                    isinstance(value, str)
                    and _contains_unsafe_text(value, self._sensitive_values)
                ):
                    return None
            return safe_group
        except BaseException:
            return None

    def _sanitize_exception_tree(
        self,
        exc: BaseException,
        seen: set[int],
    ) -> BaseException | None:
        if id(exc) in seen:
            return self._cycle_exception_marker(exc)
        if isinstance(exc, BaseExceptionGroup):
            seen.add(id(exc))
            return self._sanitize_exception_group(exc, seen)
        if self._sanitize_exception_node(exc, seen):
            return exc
        return None

    def _safe_exception_for_raise(
        self,
        exc: BaseException,
    ) -> BaseException:
        safe_exception = self._sanitize_exception_tree(exc, set())
        if safe_exception is not None:
            try:
                rendered = "".join(
                    traceback_module.format_exception(
                        type(safe_exception),
                        safe_exception,
                        safe_exception.__traceback__,
                    ),
                )
            except BaseException:
                safe_exception = None
            else:
                if _contains_unsafe_text(rendered, self._sensitive_values):
                    safe_exception = None
        if safe_exception is not None:
            return safe_exception
        return E2EExecutionFailure(
            "E2E body exception could not be safely rendered",
        )

    def _run_cleanups(self) -> None:
        if self._cleanup_ran:
            return
        self._cleanup_ran = True
        self._finalizing = True
        try:
            index = 0
            while self._cleanups:
                _target, callback = self._cleanups.pop()
                index += 1
                violations_before = self._finalization_violations
                callback_failed = False
                try:
                    callback()
                except BaseException as exc:
                    callback_failed = True
                    self._cleanup_failed = True
                    self._internal_failure(
                        f"cleanup-{index}",
                        "cleanup_failed",
                        f"cleanup callback raised {type(exc).__name__}",
                    )
                if (
                    not callback_failed
                    and self._finalization_violations > violations_before
                ):
                    self._cleanup_failed = True
                    self._internal_failure(
                        f"cleanup-{index}",
                        "cleanup_failed",
                        "cleanup callback attempted result finalization reentry",
                    )
        finally:
            self._finalizing = False

    def _finalize(self) -> E2EResult:
        if self._result is not None:
            return self._result
        if not self._outcomes:
            self._internal_failure(
                "result-protocol",
                "result_protocol",
                "no E2E cases were selected",
            )
        passed = sum(outcome == "pass" for outcome in self._outcomes.values())
        failed = sum(outcome == "fail" for outcome in self._outcomes.values())
        skipped = sum(outcome == "skip" for outcome in self._outcomes.values())
        executed = passed + failed
        selected = executed + skipped
        if failed:
            status = "fail"
        elif skipped and not executed:
            status = "skip"
        elif skipped:
            status = "pass_with_skips"
        else:
            status = "pass"
        self._result = E2EResult(
            test_id=self._test_id,
            run_id=self._run_id,
            status=status,
            counts={
                "selected": selected,
                "executed": executed,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
            },
            skip_reasons=self._skip_reasons,
            artifacts=self._artifacts,
            failures=self._failures,
            sensitive_values=self._sensitive_values,
        )
        self._finalized = True
        return self._result

    def _write_once(self) -> Path:
        if not self._written:
            self._finalize().write(self._result_file)
            self._written = True
        return self._result_file

    def write(self) -> Path:
        """Finalize cleanup and atomically write exactly once."""

        self._guard_not_finalizing()
        if self._active:
            raise ProtocolError("explicit write is not allowed inside the context")
        self._run_cleanups()
        path = self._write_once()
        if self._cleanup_failed:
            raise CleanupFailure("one or more E2E cleanup callbacks failed")
        return path

    def exit_code(self) -> int:
        """Return the protocol exit code without calling ``sys.exit``."""

        self._guard_not_finalizing()
        if self._active:
            raise ProtocolError("exit_code is unavailable inside the context")
        self._run_cleanups()
        return self._finalize().exit_code()

"""Single manifest-driven E2E runner with a strict child result protocol."""

from __future__ import annotations

import argparse
import hashlib
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _import_root in (REPO_ROOT / "test", REPO_ROOT, SCRIPT_DIR):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from e2e_contract import (  # noqa: E402
    CanonicalSnapshot,
    E2ECase,
    E2EManifest,
    ManifestError,
    _read_bounded_report_bytes,
    atomic_write_report_bytes_pair,
    atomic_write_report_pair,
    canonical_journey_contract,
    canonical_report_count_reasons,
    canonical_report_detail_reasons,
    canonical_write_reasons,
    compute_canonical_snapshot,
    evaluate_canonical_freshness,
    journey_result_case_id,
    load_manifest,
    recover_report_transaction,
    strict_json_loads,
)
from scripts.e2e_stack_lease import (  # noqa: E402
    IdentityCleanupError,
    IdentityEnableError,
    IdentityStackLease,
    MAX_BUNDLE_BYTES,
    StackLeaseProtocolError,
    compose_gate_override,
    load_child_bundle,
    load_token_bundle_payload,
    parallel_subrun_argv,
    replace_private_file,
    validate_token_bundle_location,
    validate_token_bundle_payload,
    write_token_bundle,
)
from scripts.e2e_identity import (  # noqa: E402
    IdentityTokenError,
    encode_secret,
    generate_secret,
    prove_identity_owner,
    sign_memory_extraction_session,
)
from scripts.e2e_profiles import (  # noqa: E402
    ProfileCoordinator,
    ProfileEnableError,
    ProfilePreflightError,
    ProfileRestoreError,
    ProfileStartError,
    plan_profile_epochs,
)
from scripts.prepare_voiceprint_fixtures import (  # noqa: E402
    fixture_manifest_sha256,
)
from support.e2e import (  # noqa: E402
    E2EResult,
    ProtocolError,
    _redact_text,
    _secret_values,
)


GROUPS = (
    "default",
    "security",
    "provider_probe",
    "acoustic_probe",
    "manual_inspection",
)
JOURNEY_SHARD_SIZE = 6


def _partition_journey_ids(
    journey_ids: Sequence[str],
    *,
    shard_size: int = JOURNEY_SHARD_SIZE,
) -> tuple[tuple[str, ...], ...]:
    """Keep each sequential journey child well inside its signed-token TTL."""
    if type(shard_size) is not int or shard_size < 1:
        raise RunnerArgumentError("journey shard size must be a positive integer")
    normalized = tuple(journey_ids)
    if any(type(item) is not str or not item for item in normalized):
        raise RunnerArgumentError("journey ids must be non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise RunnerArgumentError("journey ids must be unique")
    return tuple(
        normalized[index:index + shard_size]
        for index in range(0, len(normalized), shard_size)
    )


def _should_shard_journey_case(
    *,
    case_id: str,
    lane: str | None,
    lease_child: bool,
    has_lease: bool,
    full: bool,
    journey_count: int,
) -> bool:
    """Only a declared full-corpus request may expand into canonical shards."""
    return (
        case_id == "e2e_journeys"
        and lane == "milestone"
        and not lease_child
        and has_lease
        and full
        and journey_count > JOURNEY_SHARD_SIZE
    )


def _restore_identity_lease(
    lease: IdentityStackLease,
    *,
    attempts: int = 2,
) -> bool:
    """Retry a fail-closed identity restore while the lease still owns state."""
    for _ in range(attempts):
        try:
            lease.restore()
            return True
        except IdentityCleanupError:
            continue
    return False


def _restore_profile_coordinator(
    coordinator: ProfileCoordinator,
    *,
    attempts: int = 2,
) -> bool:
    """Retry profile restore before reporting an unrecoverable cleanup failure."""
    for _ in range(attempts):
        try:
            coordinator.restore()
            return True
        except ProfileRestoreError:
            continue
    return False
LANES = ("ci", "nightly", "milestone")
CLI_PROFILES = ("root", "auth", "mtls", "real")
MILESTONES = ("M-A", "M-B", "M-C", "M-D")
STALE_POLICIES = ("warn", "error")
MAX_RESULT_BYTES = 1024 * 1024
MAX_RUNTIME_METADATA_BYTES = 1024 * 1024
MAX_JOURNEY_REPORT_BYTES = 16 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_DIAGNOSTIC_BYTES = 4096
_WINDOWS_CREATE_SUSPENDED = 0x00000004
_SENSITIVE_ENV_TERMS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "CREDENTIALS",
    "AUTHORIZATION",
    "COOKIE",
)
_SENSITIVE_ENV_TERM_RE = re.compile(
    r"(?:^|_)(?:"
    + "|".join(_SENSITIVE_ENV_TERMS)
    + r")(?:_|$)",
    re.IGNORECASE,
)
_NAMESPACE_ENV = frozenset({
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
    "E2E_CAPABILITY_ENABLED",
    "E2E_CAPABILITY_SECRET",
    "E2E_NAMESPACE_ADMIN_ENABLED",
    "E2E_NAMESPACE_ADMIN_SECRET",
    "E2E_EXPECTED_VEHICLE_ID",
    "E2E_STACK_LEASE_ID",
    "E2E_STACK_LEASE_ROLE",
    "E2E_PROVIDER",
    "E2E_MODEL",
    "E2E_CANONICAL_METADATA",
    "E2E_VOICEPRINT_FIXTURE_DIR",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
})
_STACK_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_STACK_ENV_BYTES = 1024 * 1024
_FIXTURE_ENV_NAMES = frozenset({
    "E2E_VOICEPRINT_FIXTURE_DIR",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST",
    "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
})
_CAPABILITY_ENV_NAMES = (
    "E2E_CAPABILITY_ENABLED",
    "E2E_CAPABILITY_SECRET",
)
_NAMESPACE_ADMIN_ENV_NAMES = (
    "E2E_NAMESPACE_ADMIN_ENABLED",
    "E2E_NAMESPACE_ADMIN_SECRET",
)
NAMESPACE_ADMIN_SERVICE_BY_CASE = {
    "e2e_proactive": "proactive",
    "e2e_mcp": "mcp-bridge",
}
_NAMESPACE_ADMIN_SERVICE_ORDER = ("proactive", "mcp-bridge")
_EMPTY_COUNTS = {
    "selected": 0,
    "executed": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
}


def _presign_memory_bundle(
    path: Path,
    *,
    expected_bundle_root: Path,
    secret: bytes,
    run_id: str,
    user_id: str,
    timeout_s: int,
    memory_sessions: int,
    now: int | None = None,
    owner_bundle_loader: Callable[..., Mapping[str, Any]] | None = None,
) -> Path:
    """Atomically replace owner-private plain session placeholders with bearers."""

    # ``memory_session_ids`` is the frozen legacy bundle key. Owner input is
    # plain IDs here; the same key is atomically rewritten with capabilities.
    if (
        type(memory_sessions) is not int
        or memory_sessions < 0
        or memory_sessions > 64
    ):
        raise StackLeaseProtocolError("memory capability bundle is invalid")
    bundle_root = Path(expected_bundle_root)
    if owner_bundle_loader is None:
        payload = load_token_bundle_payload(
            Path(path),
            bundle_root=bundle_root,
            memory_mode="placeholder",
        )
        candidate = validate_token_bundle_location(
            Path(path),
            bundle_root=bundle_root,
            require_file=True,
        )
    else:
        candidate = validate_token_bundle_location(
            Path(path),
            bundle_root=bundle_root,
            require_file=False,
        )
        try:
            payload = owner_bundle_loader(
                candidate,
                bundle_root=bundle_root,
                memory_mode="placeholder",
            )
        except StackLeaseProtocolError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise StackLeaseProtocolError(
                "memory capability test transport failed",
            ) from exc
        payload = validate_token_bundle_payload(
            payload,
            memory_mode="placeholder",
        )
        if candidate.parent.name != (
            f"{payload['lease_id']}-{payload['case_id']}"
        ):
            raise StackLeaseProtocolError(
                "memory capability bundle path does not match its namespace",
            )
    expected_plain = [
        f"{user_id}-session-{number}"
        for number in range(1, memory_sessions + 1)
    ]
    if (
        not isinstance(payload, dict)
        or payload.get("run_id") != run_id
        or payload.get("user_id") != user_id
        or payload.get("memory_session_ids") != expected_plain
    ):
        raise StackLeaseProtocolError(
            "memory capability bundle does not match its owner",
        )
    try:
        payload["memory_session_ids"] = [
            sign_memory_extraction_session(
                secret,
                run_id=run_id,
                user_id=user_id,
                session_id=session_id,
                timeout_s=timeout_s,
                now=now,
            )
            for session_id in expected_plain
        ]
    except IdentityTokenError as exc:
        raise StackLeaseProtocolError(
            "memory capability signing failed",
        ) from exc

    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise StackLeaseProtocolError("memory capability bundle is too large")
    try:
        replace_private_file(
            candidate,
            encoded,
            temporary_prefix=".memory-capability-",
        )
    except StackLeaseProtocolError as exc:
        raise StackLeaseProtocolError(
            "memory capability bundle rewrite failed",
        ) from exc
    load_token_bundle_payload(
        candidate,
        bundle_root=bundle_root,
        memory_mode="capability",
    )
    return candidate


def _clear_capability_environment(environ: MutableMapping[str, str]) -> None:
    for name in _CAPABILITY_ENV_NAMES:
        environ.pop(name, None)


def _configure_capability_environment(
    environ: MutableMapping[str, str],
    *,
    secret: bytes,
    enabled: bool,
) -> None:
    if enabled:
        environ["E2E_CAPABILITY_ENABLED"] = "true"
        environ["E2E_CAPABILITY_SECRET"] = encode_secret(secret)
        return
    environ["E2E_CAPABILITY_ENABLED"] = "false"
    environ.pop("E2E_CAPABILITY_SECRET", None)


def _configure_namespace_admin_environment(
    environ: MutableMapping[str, str],
    *,
    secret: bytes,
    enabled: bool,
) -> None:
    if enabled:
        environ["E2E_NAMESPACE_ADMIN_ENABLED"] = "true"
        environ["E2E_NAMESPACE_ADMIN_SECRET"] = encode_secret(secret)
        return
    environ["E2E_NAMESPACE_ADMIN_ENABLED"] = "false"
    environ.pop("E2E_NAMESPACE_ADMIN_SECRET", None)


def _namespace_admin_services(
    selected: Sequence[E2ECase],
) -> tuple[str, ...]:
    requested = {
        NAMESPACE_ADMIN_SERVICE_BY_CASE[case.id]
        for case in selected
        if case.id in NAMESPACE_ADMIN_SERVICE_BY_CASE
    }
    return tuple(
        service
        for service in _NAMESPACE_ADMIN_SERVICE_ORDER
        if service in requested
    )


def _memory_sessions_for(case: E2ECase, lane: str | None) -> int:
    nightly = getattr(case, "nightly", None)
    if (
        lane == "nightly"
        and nightly is not None
        and nightly.memory_sessions is not None
    ):
        return nightly.memory_sessions
    return case.memory_sessions


def _identity_capability_lease(
    *,
    repo_root: Path,
    source_root: Path | None = None,
    environ: MutableMapping[str, str],
    capability_enabled: bool = True,
    extra_services: Sequence[str] = (),
    lease_factory: Callable[..., IdentityStackLease] = IdentityStackLease,
) -> IdentityStackLease:
    """Create one lease whose identity and memory gates share one secret."""

    secret = generate_secret()
    _configure_capability_environment(
        environ,
        secret=secret,
        enabled=capability_enabled,
    )
    try:
        lease = lease_factory(
            repo_root=repo_root,
            environ=environ,
        )
    except BaseException:
        _clear_capability_environment(environ)
        raise
    if hasattr(lease, "secret_factory"):
        lease.secret_factory = lambda: secret
    elif hasattr(lease, "secret"):
        lease.secret = secret
    if hasattr(lease, "extra_services"):
        lease.extra_services = tuple(dict.fromkeys(extra_services))
    original_compose = getattr(lease, "compose", None)
    if not callable(original_compose):
        return lease
    resolved_repo_root = Path(repo_root).resolve()
    resolved_source_root = (
        resolved_repo_root
        if source_root is None
        else Path(source_root).resolve()
    )
    shared_compose = (
        _profile_compose_runner(
            resolved_repo_root,
            build=False,
            source_root=resolved_source_root,
            gate_services=tuple(extra_services),
        )
        if resolved_source_root != resolved_repo_root
        else None
    )

    def compose_with_capability(source: Mapping[str, str]) -> Any:
        compose_env = dict(source)
        enabled = (
            capability_enabled
            and compose_env.get("E2E_IDENTITY_ENABLED") == "true"
        )
        _configure_capability_environment(
            compose_env,
            secret=secret,
            enabled=enabled,
        )
        _configure_namespace_admin_environment(
            compose_env,
            secret=secret,
            enabled=bool(extra_services) and enabled,
        )
        if shared_compose is not None:
            return shared_compose(
                (
                    "docker",
                    "compose",
                    "-f",
                    "compose.yaml",
                    "up",
                    "-d",
                    "--build",
                ),
                compose_env,
            )
        return original_compose(compose_env)

    lease.compose = compose_with_capability
    return lease


def _profile_compose_with_capability(
    compose: Callable[[Sequence[str], Mapping[str, str]], Any],
    *,
    secret: bytes,
    enabled: Callable[[], bool],
    admin_services: Callable[[], Sequence[str]] = lambda: (),
) -> Callable[[Sequence[str], Mapping[str, str]], Any]:
    """Inject the gate only into epochs that declared extraction sessions."""

    last_actual_state: bool | None = None
    last_actual_admin_services: tuple[str, ...] = ()

    def invoke(
        argv: Sequence[str],
        source: Mapping[str, str],
    ) -> Any:
        nonlocal last_actual_state, last_actual_admin_services
        capability_enabled = bool(enabled())
        current_admin_services = tuple(dict.fromkeys(admin_services()))
        compose_env = dict(source)
        _configure_capability_environment(
            compose_env,
            secret=secret,
            enabled=capability_enabled,
        )
        _configure_namespace_admin_environment(
            compose_env,
            secret=secret,
            enabled=bool(current_admin_services),
        )
        command = list(argv)
        state_change_requires_memory = (
            (last_actual_state is None and capability_enabled)
            or (
                last_actual_state is not None
                and capability_enabled != last_actual_state
            )
        )
        if state_change_requires_memory and "memory" not in command:
            command.append("memory")
        for service in (
            *current_admin_services,
            *last_actual_admin_services,
        ):
            if service not in command:
                command.append(service)
        result = compose(command, compose_env)
        last_actual_state = capability_enabled
        last_actual_admin_services = current_admin_services
        return result

    return invoke


class RunnerArgumentError(ValueError):
    """The command line cannot be interpreted safely."""


def _git_common_dir(repo_root: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        raw = (completed.stdout or b"").decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not raw:
        return None
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _resolve_stack_root(
    repo_root: Path,
    environ: Mapping[str, str],
) -> Path:
    """Let a clean worktree drive its repository's shared root Compose stack."""
    raw = str(environ.get("E2E_STACK_ROOT", "") or "").strip()
    if not raw:
        return repo_root
    candidate_path = Path(raw)
    if not candidate_path.is_absolute():
        raise RunnerArgumentError("E2E_STACK_ROOT must be absolute")
    try:
        candidate = candidate_path.resolve(strict=True)
    except OSError as exc:
        raise RunnerArgumentError("E2E_STACK_ROOT does not exist") from exc
    if not candidate.is_dir():
        raise RunnerArgumentError("E2E_STACK_ROOT is not a directory")
    if not (candidate / "compose.yaml").is_file():
        raise RunnerArgumentError("E2E_STACK_ROOT has no compose.yaml")
    if not (candidate / ".env").is_file():
        raise RunnerArgumentError("E2E_STACK_ROOT has no root .env")
    source_common = _git_common_dir(repo_root)
    stack_common = _git_common_dir(candidate)
    if (
        source_common is None
        or stack_common is None
        or source_common != stack_common
    ):
        raise RunnerArgumentError(
            "E2E_STACK_ROOT must belong to the same Git repository",
        )
    return candidate


def _load_stack_environment(
    stack_root: Path,
    environ: Mapping[str, str],
) -> dict[str, str]:
    """Load the supported root .env for E2E children without overriding callers."""

    merged = {
        key: value
        for key, value in environ.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    path = stack_root / ".env"
    if not path.is_file():
        return merged
    try:
        if path.stat().st_size > _MAX_STACK_ENV_BYTES:
            raise RunnerArgumentError("root .env exceeds size limit")
        text = path.read_text(encoding="utf-8-sig")
    except RunnerArgumentError:
        raise
    except (OSError, UnicodeError) as exc:
        raise RunnerArgumentError("root .env cannot be read safely") from exc
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if _STACK_ENV_NAME_RE.fullmatch(key) is None:
            continue
        merged.setdefault(key, value.strip())
    return merged


def _normalize_audio_api_origin(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RunnerArgumentError("audio API origin is invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise RunnerArgumentError("audio API origin is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RunnerArgumentError("audio API origin is invalid")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RunnerArgumentError(message)


@dataclass(frozen=True)
class Staleness:
    """Replaceable Task 4 freshness hook; Task 11 owns digest-grade logic."""

    stale: bool
    reasons: tuple[str, ...]
    runtime_freshness: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        return {"stale": self.stale, "reasons": list(self.reasons)}


def _canonical_snapshot(
    repo_root: Path,
    manifest: E2EManifest,
    environ: Mapping[str, str],
) -> CanonicalSnapshot:
    return compute_canonical_snapshot(
        repo_root,
        canonical_inputs=manifest.canonical_inputs,
        manifest_path="test/e2e_manifest.yaml",
        runner_roots=("scripts/run_e2e.py", "test/e2e_journeys.py"),
        runner_dependencies=manifest.runner_dependencies,
        non_secret_config_keys=manifest.non_secret_config_keys,
        environ=environ,
    )


def _revision(value: Any, *, kind: str) -> str:
    encoded = json.dumps(
        {"kind": kind, "value": value},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bootstrap_model_capabilities(provider: str) -> dict[str, Any]:
    """Static capabilities of the provider classes built by llm_runtime."""

    native_tool_providers = frozenset({
        "anthropic",
        "deepseek",
        "mimo",
        "minimax",
        "qwen",
        "qwen-vl",
    })
    return {
        "chat_completion": True,
        "streaming": True,
        "modalities": (
            ["text", "image"]
            if provider == "qwen-vl"
            else ["text"]
        ),
        "native_tool_calling": provider in native_tool_providers,
    }


def _stable_runtime_revisions(
    payload: Mapping[str, Any],
    *,
    provider: str,
    model: str,
    non_secret_config: str,
    planner_toolcall_enabled: bool = True,
) -> dict[str, str]:
    """Hash only stable public catalog identity, never health or key presence."""

    raw_providers = payload.get("providers")
    if (
        not isinstance(raw_providers, Sequence)
        or isinstance(raw_providers, (str, bytes))
    ):
        raise RunnerArgumentError("runtime provider catalog is invalid")
    catalog: list[dict[str, Any]] = []
    for raw in raw_providers:
        if not isinstance(raw, Mapping):
            raise RunnerArgumentError("runtime provider catalog is invalid")
        provider_id = raw.get("id")
        primary = raw.get("primary")
        fast = raw.get("fast")
        raw_models = raw.get("models")
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or not isinstance(primary, str)
            or not primary
            or not isinstance(fast, str)
            or not fast
            or not isinstance(raw_models, Sequence)
            or isinstance(raw_models, (str, bytes))
        ):
            raise RunnerArgumentError("runtime provider catalog is invalid")
        models: list[str] = []
        for raw_model in raw_models:
            if (
                not isinstance(raw_model, Mapping)
                or not isinstance(raw_model.get("id"), str)
                or not raw_model["id"]
            ):
                raise RunnerArgumentError("runtime provider model catalog is invalid")
            models.append(raw_model["id"])
        if len(models) != len(set(models)):
            raise RunnerArgumentError("runtime provider model catalog is duplicated")
        catalog.append({
            "id": provider_id,
            "primary": primary,
            "fast": fast,
            "models": sorted(models),
            "internal": bool(raw.get("internal", False)),
        })
    catalog.sort(key=lambda item: item["id"])
    if len({item["id"] for item in catalog}) != len(catalog):
        raise RunnerArgumentError("runtime provider catalog is duplicated")
    active_catalog = next(
        (item for item in catalog if item["id"] == provider),
        None,
    )
    if active_catalog is None or model not in active_catalog["models"]:
        raise RunnerArgumentError("locked provider/model is absent from catalog")
    provider_revision = _revision(
        {"provider": active_catalog},
        kind="provider_catalog",
    )
    model_capabilities = _bootstrap_model_capabilities(provider)
    capability_revision = _revision(
        {
            "provider": provider,
            "model": model,
            "model_capabilities": model_capabilities,
            "planner": {
                "submit_plan_tool_calling_enabled": planner_toolcall_enabled,
            },
        },
        kind="bootstrap_capability",
    )
    return {
        "provider": provider,
        "model": model,
        "provider_revision": provider_revision,
        "capability_revision": capability_revision,
        "capability_source": "bootstrap_static",
        "non_secret_config": non_secret_config,
    }


def _runtime_state(
    *,
    provider: str,
    model: str,
    non_secret_config: str,
    planner_toolcall_enabled: bool = True,
    timeout_s: float = 10.0,
) -> dict[str, str]:
    request = urllib.request.Request(
        "http://localhost:50059/api/llm/providers",
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read(MAX_RUNTIME_METADATA_BYTES + 1)
            if len(raw) > MAX_RUNTIME_METADATA_BYTES:
                raise RunnerArgumentError("locked runtime metadata exceeds size limit")
            payload = strict_json_loads(raw.decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ManifestError,
    ) as exc:
        raise RunnerArgumentError("locked runtime metadata is unavailable") from exc
    active = payload.get("active") if isinstance(payload, Mapping) else None
    if (
        not isinstance(active, Mapping)
        or active.get("provider") != provider
        or active.get("model") != model
    ):
        raise RunnerArgumentError("locked provider/model does not match runtime active")
    return _stable_runtime_revisions(
        payload,
        provider=provider,
        model=model,
        non_secret_config=non_secret_config,
        planner_toolcall_enabled=planner_toolcall_enabled,
    )


def _planner_toolcall_enabled(environ: Mapping[str, str]) -> bool:
    """Mirror PlanBuilder's non-sensitive bootstrap switch exactly."""

    return environ.get("PLANNER_TOOLCALL", "on").strip().lower() == "on"


def _code_sha(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerArgumentError("current code SHA is unavailable") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RunnerArgumentError("current code SHA is unavailable")
    return value


def _canonical_metadata(
    *,
    repo_root: Path,
    args: argparse.Namespace,
    state: CanonicalSnapshot,
    runtime: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "runner_version": "1.0",
        "code_sha": _code_sha(repo_root),
        "provider": args.provider,
        "model": args.model,
        "provider_revision": runtime["provider_revision"],
        "capability_revision": runtime["capability_revision"],
        "capability_source": runtime["capability_source"],
        "runtime_freshness": "verified",
        "selection": {
            "runner_lane": args.lane,
            "runner_group": args.group,
            "runner_ids": list(args.ids),
            "full": bool(args.full),
        },
        "canonical_input_state": {
            "dirty": state.dirty,
            "dirty_paths": list(state.dirty_paths),
            "untracked_input_paths": list(state.untracked_input_paths),
        },
        "digests": {"algorithm": "sha256", **dict(state.digests)},
        "tracked_input_count": state.tracked_input_count,
    }


def evaluate_staleness(repo_root: Path) -> Staleness:
    """Recompute all canonical file digests without reading runtime secrets."""

    root = Path(repo_root).resolve()
    try:
        manifest = load_manifest(root / "test/e2e_manifest.yaml", repo_root=root)
        state = _canonical_snapshot(root, manifest, os.environ)
        freshness = evaluate_canonical_freshness(
            root,
            state=state,
            report_path=root / "docs/reviews/eval/journeys_report.json",
            markdown_path=root / "docs/reviews/eval/journeys_report.md",
            milestone=False,
            runtime_current=None,
        )
    except (ManifestError, OSError, ValueError):
        return Staleness(True, ("canonical_recompute_failed",))
    return Staleness(
        freshness.stale,
        freshness.reasons,
        freshness.runtime_freshness,
    )


def _normalize_staleness(value: Any) -> Staleness:
    if isinstance(value, Staleness):
        return value
    if not isinstance(value, Mapping) or set(value) != {"stale", "reasons"}:
        raise RunnerArgumentError("staleness evaluator returned an invalid shape")
    stale = value["stale"]
    reasons = value["reasons"]
    if type(stale) is not bool:
        raise RunnerArgumentError("staleness evaluator returned a non-boolean state")
    if (
        not isinstance(reasons, Sequence)
        or isinstance(reasons, (str, bytes))
        or any(not isinstance(item, str) or not item for item in reasons)
    ):
        raise RunnerArgumentError("staleness evaluator returned invalid reasons")
    normalized = tuple(reasons)
    if len(set(normalized)) != len(normalized):
        raise RunnerArgumentError("staleness evaluator returned duplicate reasons")
    if stale is False and normalized:
        raise RunnerArgumentError("fresh staleness state cannot contain reasons")
    if stale is True and not normalized:
        raise RunnerArgumentError("stale state must contain at least one reason")
    return Staleness(stale, normalized)


def _parser() -> _Parser:
    parser = _Parser(prog="python scripts/run_e2e.py", add_help=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--group", choices=GROUPS)
    parser.add_argument("--lane", choices=LANES)
    parser.add_argument("--id", dest="ids", action="append", default=[])
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--profile", choices=CLI_PROFILES)
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    parser.add_argument("--milestone", choices=MILESTONES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--parallel-isolation", type=int)
    parser.add_argument("--lease-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lease-id", help=argparse.SUPPRESS)
    parser.add_argument("--token-bundle", help=argparse.SUPPRESS)
    parser.add_argument("--token-bundle-root", help=argparse.SUPPRESS)
    return parser


def _base_summary(*, mode: str, args: argparse.Namespace | None) -> dict[str, Any]:
    return {
        "mode": mode,
        "canonical": bool(args.canonical) if args is not None else False,
        "full": False,
        "lane": args.lane if args is not None else None,
        "milestone": args.milestone if args is not None else None,
        "selection": [],
        "results": [],
        "stale": {"stale": False, "reasons": []},
        "runtime_freshness": "unverified",
        "canonical_promoted": False,
        "canonical_rejection_reasons": [],
        "warnings": [],
        "errors": [],
        "exit_code": 0,
    }


def _emit(
    stdout: TextIO,
    summary: Mapping[str, Any],
    *,
    human_lines: Sequence[str] = (),
) -> None:
    for line in human_lines:
        print(line, file=stdout)
    print(
        json.dumps(
            summary,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=stdout,
    )


def _preflight(args: argparse.Namespace) -> None:
    if args.milestone is not None and args.lane is None:
        if args.check:
            pass
        elif args.parallel_isolation == 2:
            args.lane = "milestone"
        else:
            raise RunnerArgumentError("--milestone requires an explicit runner --lane")
    if args.check and args.dry_run:
        raise RunnerArgumentError("--check and --dry-run are mutually exclusive")
    if len(args.ids) != len(set(args.ids)):
        raise RunnerArgumentError("--id entries must be unique")
    if args.ids and args.full:
        raise RunnerArgumentError("--id and --full are incompatible")
    if bool(args.provider) != bool(args.model):
        raise RunnerArgumentError("--provider and --model must be supplied together")
    internal = (
        args.lease_id is not None,
        args.token_bundle is not None,
        args.token_bundle_root is not None,
    )
    if args.lease_child:
        if not all(internal) or len(args.ids) != 1:
            raise RunnerArgumentError(
                "--lease-child requires --lease-id, --token-bundle, "
                "--token-bundle-root, and one --id",
            )
        if (
            not Path(args.token_bundle).is_absolute()
            or not Path(args.token_bundle_root).is_absolute()
        ):
            raise RunnerArgumentError(
                "--token-bundle and --token-bundle-root must be absolute paths",
            )
        if args.parallel_isolation is not None:
            raise RunnerArgumentError("lease child cannot own parallel isolation")
    elif any(internal):
        raise RunnerArgumentError("lease parameters are internal-only")
    if args.parallel_isolation is not None:
        if args.parallel_isolation != 2:
            raise RunnerArgumentError("--parallel-isolation currently requires 2")
        if args.milestone != "M-A" or set(args.ids) != {
            "e2e_memory",
            "e2e_voiceprint",
        }:
            raise RunnerArgumentError(
                "parallel isolation is fixed to M-A memory plus voiceprint",
            )
        if args.full or args.canonical or args.lease_child:
            raise RunnerArgumentError("parallel isolation cannot be full/canonical/child")
    if args.canonical:
        if args.ids:
            raise RunnerArgumentError("--id and --canonical are incompatible")
        if args.group is not None or args.profile is not None:
            raise RunnerArgumentError("filtered runs are not canonical-eligible")
        if args.milestone is None:
            raise RunnerArgumentError("--canonical requires --milestone")
        if args.lane != "milestone":
            raise RunnerArgumentError("--canonical requires --lane milestone")
        if not args.full:
            raise RunnerArgumentError("--canonical requires --full")
        if not args.provider or not args.model:
            raise RunnerArgumentError(
                "--canonical requires both --provider and --model",
            )


def _select(
    manifest: E2EManifest,
    args: argparse.Namespace,
) -> tuple[tuple[E2ECase, ...], bool]:
    explicit_scope = any((
        args.group is not None,
        args.lane is not None,
        bool(args.ids),
        args.profile is not None,
    ))
    implicit_default = not explicit_scope
    effective_group = "default" if implicit_default else args.group

    unknown = sorted(set(args.ids) - set(manifest.by_id))
    if unknown:
        raise RunnerArgumentError(f"unknown --id entries: {unknown}")

    ids = set(args.ids)
    selected = []
    for case in manifest.cases:
        if ids and case.id not in ids:
            continue
        if effective_group is not None and case.group != effective_group:
            continue
        if args.lane is not None and args.lane not in case.lanes:
            continue
        if args.profile is not None and case.profile != args.profile:
            continue
        selected.append(case)
    if not selected:
        raise RunnerArgumentError("selection is empty")
    effective_full = (args.full or implicit_default) and not args.ids
    return tuple(selected), effective_full


def _child_argv(
    case: E2ECase,
    lane: str | None,
    provider: str | None = None,
    *,
    canonical: bool = False,
) -> list[str]:
    argv = list(case.command)
    if (
        lane == "nightly"
        and case.nightly is not None
        and not case.nightly.all
    ):
        argv.extend(case.nightly.args)
    if _is_journey_case_id(case.id) and provider:
        argv.extend(("--provider", provider))
    if case.id == "e2e_planner_toolcall" and provider:
        argv.extend(("--providers", provider))
    if _is_journey_case_id(case.id) and canonical:
        argv.append("--strict-target")
    return argv


def _is_journey_case_id(case_id: str) -> bool:
    return case_id == "e2e_journeys" or case_id.startswith(
        "e2e_journeys_shard_"
    )


def _selection_summary(
    selected: Sequence[E2ECase],
    lane: str | None,
    provider: str | None,
    *,
    canonical: bool = False,
) -> list[dict[str, Any]]:
    return [
        {
            "id": case.id,
            "argv": _child_argv(
                case,
                lane,
                provider,
                canonical=canonical,
            ),
            "profile": case.profile,
            "timeout_s": case.timeout_s,
        }
        for case in selected
    ]


def _uses_profile_coordinator(selected: Sequence[E2ECase]) -> bool:
    """Keep the Task 5a root-only signed path byte-for-byte in ownership."""

    return any(case.profile in {"auth", "mtls", "real"} for case in selected)


def _profile_plan_summary(
    selected: Sequence[E2ECase],
) -> list[dict[str, Any]]:
    supported = {"root", "real", "auth", "mtls"}
    if all(case.profile in supported for case in selected):
        return [epoch.to_dict() for epoch in plan_profile_epochs(selected)]
    return [
        {
            "name": case.profile,
            "profiles": [case.profile],
            "entries": [{"id": case.id, "profile": case.profile}],
            "restore": False,
        }
        for case in selected
    ]


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"e2e-{stamp}-{uuid.uuid4().hex[:12]}"


class _ProcessTree:
    """A child-owned process group/job that remains valid after leader exit."""

    def __init__(self) -> None:
        self._job: int | None = None
        self._kernel32: Any = None
        self._ntdll: Any = None
        if os.name == "nt":
            self._create_windows_job()

    @property
    def available(self) -> bool:
        return os.name != "nt" or self._job is not None

    def _create_windows_job(self) -> None:
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(job)
            return
        self._job = int(job)
        self._kernel32 = kernel32
        self._ntdll = ntdll

    def attach(self, process: subprocess.Popen[bytes]) -> bool:
        if os.name != "nt":
            return True
        if self._job is None:
            return False
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not self._kernel32.AssignProcessToJobObject(
            self._job,
            int(process_handle),
        ):
            return False
        return True

    def resume(self, process: subprocess.Popen[bytes]) -> bool:
        if os.name != "nt":
            return True
        process_handle = getattr(process, "_handle", None)
        if self._job is None or process_handle is None or self._ntdll is None:
            return False
        return self._ntdll.NtResumeProcess(int(process_handle)) >= 0

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            if self._job is not None:
                self._kernel32.TerminateJobObject(self._job, 1)
                return
            self._terminate_windows_fallback(process.pid)
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _terminate_windows_fallback(pid: int) -> None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def close(self) -> None:
        if self._job is not None:
            self._kernel32.CloseHandle(self._job)
            self._job = None
        self._kernel32 = None
        self._ntdll = None


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError("result JSON contains duplicate object keys")
        value[key] = item
    return value


def _load_result(
    path: Path,
    *,
    sensitive_values: Sequence[str] = (),
) -> E2EResult:
    try:
        if path.stat().st_size > MAX_RESULT_BYTES:
            raise ProtocolError("result file exceeds maximum size")
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ProtocolError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
    ) as exc:
        raise ProtocolError("result file is missing or invalid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ProtocolError("result file root must be an object")
    required = {
        "schema_version",
        "test_id",
        "run_id",
        "status",
        "counts",
        "skip_reasons",
        "artifacts",
        "failures",
    }
    if set(raw) != required:
        raise ProtocolError("result file keys do not match the protocol")
    try:
        return E2EResult(
            schema_version=raw["schema_version"],
            test_id=raw["test_id"],
            run_id=raw["run_id"],
            status=raw["status"],
            counts=raw["counts"],
            skip_reasons=raw["skip_reasons"],
            artifacts=raw["artifacts"],
            failures=raw["failures"],
            sensitive_values=sensitive_values,
        )
    except (ProtocolError, TypeError, ValueError) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError("result file cannot be normalized") from exc


def _artifact_paths(
    result: E2EResult,
    artifact_dir: Path,
    *,
    child_root: Path,
    sensitive_values: Sequence[str] = (),
) -> list[str]:
    owned_root = child_root.resolve(strict=True)
    if artifact_dir.is_symlink():
        raise ProtocolError("child artifact root cannot be a symbolic link")
    try:
        root = artifact_dir.resolve(strict=True)
        root.relative_to(owned_root)
    except (OSError, ValueError) as exc:
        raise ProtocolError("child artifact root escapes its namespace") from exc
    if not root.is_dir():
        raise ProtocolError("child artifact root is not a directory")
    paths: list[str] = []
    for item in result.artifacts:
        unresolved = root / item["path"]
        if unresolved.is_symlink():
            raise ProtocolError("result artifact cannot be a symbolic link")
        try:
            candidate = unresolved.resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ProtocolError("result artifact escapes its child namespace") from exc
        if not candidate.is_file():
            raise ProtocolError("result artifact must be an existing regular file")
        if any(secret in str(candidate) for secret in sensitive_values):
            raise ProtocolError("result artifact path contains sensitive material")
        paths.append(str(candidate))
    return paths


def _skip_policy_errors(
    case: E2ECase,
    lane: str | None,
    result: E2EResult,
) -> list[str]:
    if result.status not in {"skip", "pass_with_skips"}:
        return []
    if lane == "milestone" or case.group in {"default", "security"}:
        return ["skip_forbidden"]
    declared = set(case.skip_reasons)
    actual = {item["code"] for item in result.skip_reasons}
    if "forbid" in declared or not actual.issubset(declared):
        return ["result_protocol"]
    return []


def _child_environment(
    source: Mapping[str, str],
    *,
    case: E2ECase,
    run_id: str,
    result_file: Path,
    artifact_dir: Path,
    lane: str | None,
    provider: str | None,
    model: str | None,
) -> dict[str, str]:
    env = {
        key: value
        for key, value in source.items()
        if isinstance(key, str)
        and isinstance(value, str)
        and key not in _NAMESPACE_ENV
        and key != "AUDIO_API_URL"
        and key not in {"E2E_IDENTITY_ENABLED", "E2E_IDENTITY_SECRET"}
    }
    if not env.get("WS_URL"):
        env["WS_URL"] = "ws://127.0.0.1:8090/ws"
    user = f"{run_id}-{case.id}"
    env.update({
        "E2E_RUN_ID": run_id,
        "E2E_TEST_ID": case.id,
        "E2E_USER_ID": user,
        "E2E_SESSION_PREFIX": f"{user}-session",
        "E2E_RESULT_FILE": str(result_file),
        "E2E_ARTIFACT_DIR": str(artifact_dir),
        "E2E_LANE": lane or "",
        "E2E_PROFILE": case.profile,
    })
    if _is_journey_case_id(case.id):
        if provider:
            env["E2E_PROVIDER"] = provider
        if model:
            env["E2E_MODEL"] = model
        metadata = source.get("E2E_CANONICAL_METADATA")
        if isinstance(metadata, str) and metadata:
            env["E2E_CANONICAL_METADATA"] = metadata
    if case.signed_identity:
        for name in (
            "E2E_IDENTITY_TOKEN",
            "E2E_CONTROL_USER_ID",
            "E2E_CONTROL_IDENTITY_TOKEN",
            "E2E_MEMORY_SESSION_IDS",
            "E2E_EXPECTED_VEHICLE_ID",
            "E2E_STACK_LEASE_ID",
            "E2E_STACK_LEASE_ROLE",
        ):
            value = source.get(name)
            if isinstance(value, str) and value:
                env[name] = value
    return env


def _is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    if upper.endswith(("_PATH", "_FILE")):
        return False
    if "PUBLIC_KEY" in upper or upper.endswith("_KEY_ID"):
        return False
    if _SENSITIVE_ENV_TERM_RE.search(upper) is not None:
        return True
    if upper == "AUTH" or upper.endswith("_AUTH"):
        return True
    return upper.endswith("_KEY")


def _runner_sensitive_values(source: Mapping[str, str]) -> tuple[str, ...]:
    values = set(_secret_values(source))
    values.update(
        value
        for name, value in source.items()
        if (
            isinstance(name, str)
            and isinstance(value, str)
            and value
            and value != "[REDACTED]"
            and _is_sensitive_env_name(name)
        )
    )
    return tuple(sorted(values, key=len, reverse=True))


def _terminate_and_reap(
    process_tree: _ProcessTree,
    process: subprocess.Popen[bytes],
) -> None:
    """Best-effort bounded cleanup; callers must still close the tree."""

    try:
        process_tree.terminate(process)
    except BaseException:
        pass
    try:
        process.wait(timeout=5)
        return
    except BaseException:
        pass
    try:
        process.kill()
    except BaseException:
        pass
    try:
        process.wait(timeout=5)
    except BaseException:
        pass


def _run_profile_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environ: Mapping[str, str],
    timeout_s: int,
) -> int:
    """Run Compose/cert helpers with no captured output and owned descendants."""

    process_tree: _ProcessTree | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        process_tree = _ProcessTree()
        if not process_tree.available:
            raise RuntimeError("profile_command_failed")
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | _WINDOWS_CREATE_SUSPENDED
            )
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
        if (
            not process_tree.attach(process)
            or not process_tree.resume(process)
        ):
            raise RuntimeError("profile_command_failed")
        returncode = process.wait(timeout=timeout_s)
        if returncode != 0:
            raise RuntimeError("profile_command_failed")
        return returncode
    except KeyboardInterrupt:
        raise
    except BaseException:
        raise RuntimeError("profile_command_failed") from None
    finally:
        try:
            if process_tree is not None and process is not None:
                _terminate_and_reap(process_tree, process)
        finally:
            if process_tree is not None:
                process_tree.close()


def _build_shared_source_images(
    *,
    stack_root: Path,
    source_root: Path,
    services: Sequence[str],
    environ: Mapping[str, str],
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="e2e-source-build-",
    ) as temp_dir:
        source_compose_path = Path(temp_dir) / "source-build.json"
        source_compose_path.write_text(
            json.dumps(
                {
                    "include": [{
                        "path": str(source_root / "deploy" / "docker-compose.yaml"),
                        "project_directory": str(source_root / "deploy"),
                        "env_file": str(stack_root / ".env"),
                    }],
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        _run_profile_command(
            [
                "docker",
                "compose",
                "--project-directory",
                str(stack_root),
                "-f",
                str(source_compose_path),
                "build",
                *services,
            ],
            cwd=source_root,
            environ={
                **environ,
                "CAR_AGENT_MODELS_ROOT": str(stack_root / "models"),
            },
            timeout_s=900,
        )


def _profile_compose_runner(
    repo_root: Path,
    *,
    build: bool = True,
    source_root: Path | None = None,
    gate_services: Sequence[str] = ("proactive", "mcp-bridge"),
) -> Callable[[Sequence[str], Mapping[str, str]], None]:
    resolved_repo_root = Path(repo_root).resolve()
    resolved_source_root = (
        resolved_repo_root
        if source_root is None
        else Path(source_root).resolve()
    )
    source_built = False

    def invoke(argv: Sequence[str], environ: Mapping[str, str]) -> None:
        nonlocal source_built
        profile_environ = dict(environ)
        if resolved_source_root != resolved_repo_root:
            profile_environ["CAR_AGENT_SKILLS_ROOT"] = str(
                resolved_source_root / "skills"
            )
        requested_build = "--build" in argv
        command = list(argv) if build else [
            item for item in argv if item != "--build"
        ]
        try:
            compose_file_index = command.index("-f")
        except ValueError as exc:
            raise RuntimeError("profile_command_failed") from exc
        with tempfile.TemporaryDirectory(
            prefix="e2e-profile-compose-",
        ) as temp_dir:
            if (
                not build
                and requested_build
                and resolved_source_root != resolved_repo_root
                and not source_built
            ):
                _build_shared_source_images(
                    stack_root=resolved_repo_root,
                    source_root=resolved_source_root,
                    services=(),
                    environ=profile_environ,
                )
                source_built = True
                up_index = command.index("up")
                service_index = up_index + 1
                while (
                    service_index < len(command)
                    and command[service_index].startswith("-")
                ):
                    service_index += 1
                command = command[:service_index]
            override_path = Path(temp_dir) / "identity-gates.json"
            runtime_override = compose_gate_override(gate_services)
            if resolved_source_root != resolved_repo_root:
                runtime_override["services"]["cloud-planner"] = {
                    "volumes": [{
                        "type": "bind",
                        "source": str(resolved_source_root / "skills"),
                        "target": "/app/skills",
                        "read_only": True,
                    }],
                }
            override_path.write_text(
                json.dumps(
                    runtime_override,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            command[compose_file_index + 2:compose_file_index + 2] = [
                "-f",
                str(override_path),
            ]
            _run_profile_command(
                command,
                cwd=resolved_repo_root,
                environ=profile_environ,
                timeout_s=900,
            )

    return invoke


def _profile_cert_generator(
    repo_root: Path,
    environ: Mapping[str, str],
) -> Callable[[], bool]:
    def generate() -> bool:
        try:
            return _run_profile_command(
                ["powershell", "-File", "scripts/gen-certs.ps1"],
                cwd=repo_root,
                environ=environ,
                timeout_s=120,
            ) == 0
        except KeyboardInterrupt:
            raise
        except RuntimeError:
            return False

    return generate


class _BoundedPipeCapture:
    """Continuously drain both child pipes into bounded in-memory tail rings."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        stdout_path: Path,
        stderr_path: Path,
        sensitive_values: Sequence[str],
    ) -> None:
        self._paths = {
            "stdout": stdout_path,
            "stderr": stderr_path,
        }
        self._sensitive_values = tuple(sensitive_values)
        self._buffers = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        self._streams: dict[str, Any] = {}
        self._threads: list[threading.Thread] = []
        for name in ("stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is None:
                continue
            self._streams[name] = stream
            thread = threading.Thread(
                target=self._drain,
                args=(name, stream),
                name=f"e2e-{name}-capture",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _drain(self, name: str, stream: Any) -> None:
        buffer = self._buffers[name]
        try:
            while True:
                chunk = stream.read(16384)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                buffer.extend(chunk)
                excess = len(buffer) - MAX_LOG_BYTES
                if excess > 0:
                    del buffer[:excess]
        except (OSError, ValueError):
            return

    def finish(self) -> tuple[str, str, list[str]]:
        for thread in self._threads:
            thread.join(timeout=5)
        for name, stream in self._streams.items():
            thread = next(
                item
                for item in self._threads
                if item.name == f"e2e-{name}-capture"
            )
            if thread.is_alive():
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
                thread.join(timeout=1)

        tails: dict[str, str] = {}
        logs: list[str] = []
        for name, path in self._paths.items():
            try:
                safe = _redact_text(
                    bytes(self._buffers[name]).decode(
                        "utf-8",
                        errors="replace",
                    ),
                    self._sensitive_values,
                )
                safe_bytes = safe.encode(
                    "utf-8",
                    errors="replace",
                )[-MAX_LOG_BYTES:]
                path.write_bytes(safe_bytes)
                tails[name] = safe_bytes.decode("utf-8", errors="replace")
                logs.append(str(path.resolve()))
            except (OSError, ProtocolError):
                tails[name] = ""
        return tails.get("stdout", ""), tails.get("stderr", ""), logs


def _run_fixture_command(
    argv: Sequence[str],
    *,
    repo_root: Path,
    environ: Mapping[str, str],
    timeout_s: int,
    stdout_path: Path,
    stderr_path: Path,
    sensitive_values: Sequence[str],
) -> tuple[bool, int | None, str, list[str]]:
    process: subprocess.Popen[bytes] | None = None
    process_tree: _ProcessTree | None = None
    capture: _BoundedPipeCapture | None = None
    returncode: int | None = None
    timed_out = False
    start_failed = False
    stdout_tail = ""
    stderr_tail = ""
    logs: list[str] = []
    try:
        process_tree = _ProcessTree()
        if not process_tree.available:
            start_failed = True
        else:
            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | _WINDOWS_CREATE_SUSPENDED
                )
            process = subprocess.Popen(
                list(argv),
                cwd=repo_root,
                env=dict(environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
            capture = _BoundedPipeCapture(
                process,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                sensitive_values=sensitive_values,
            )
            if (
                not process_tree.attach(process)
                or not process_tree.resume(process)
            ):
                start_failed = True
            else:
                try:
                    returncode = process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
    except KeyboardInterrupt:
        raise
    except Exception:
        start_failed = True
    finally:
        try:
            if process is not None and process_tree is not None:
                _terminate_and_reap(process_tree, process)
                if returncode is None:
                    returncode = process.returncode
        finally:
            try:
                if capture is not None:
                    stdout_tail, stderr_tail, logs = capture.finish()
            finally:
                if process_tree is not None:
                    process_tree.close()
    diagnostic = "\n".join(
        item for item in (stderr_tail, stdout_tail) if item
    )[-MAX_DIAGNOSTIC_BYTES:]
    return (
        not start_failed and not timed_out and returncode == 0,
        returncode,
        diagnostic,
        logs,
    )


def _fixture_environment(
    source: Mapping[str, str],
) -> dict[str, str]:
    env = {
        key: value
        for key, value in source.items()
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and (
                not key.startswith("E2E_")
                or key == "E2E_AUDIO_API_ORIGIN"
            )
            and key != "AUDIO_API_URL"
            and not _is_sensitive_env_name(key)
        )
    }
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_fixture_pre_step(
    case: E2ECase,
    *,
    repo_root: Path,
    artifact_dir: Path,
    environ: Mapping[str, str],
    sensitive_values: Sequence[str],
) -> dict[str, Any]:
    if case.fixture_pre_step != "voiceprint":
        return {
            "ok": False,
            "error": "fixture_prepare",
            "returncode": None,
            "diagnostic": "",
            "logs": [],
            "fixture_dir": "",
            "manifest": "",
        }
    generator = repo_root / "scripts" / "prepare_voiceprint_fixtures.py"
    fixture_dir = artifact_dir / "voiceprint-fixtures"
    manifest = fixture_dir / "voiceprint-fixtures.json"
    fixture_env = _fixture_environment(environ)
    base_url = environ["E2E_AUDIO_API_ORIGIN"]
    commands = (
        (
            "prepare",
            (
                sys.executable,
                str(generator),
                "--artifact-dir",
                str(fixture_dir),
                "--audio-api-url",
                base_url,
            ),
        ),
        (
            "verify",
            (
                sys.executable,
                str(generator),
                "--artifact-dir",
                str(fixture_dir),
                "--verify",
            ),
        ),
    )
    logs: list[str] = []
    for stage, argv in commands:
        ok, returncode, diagnostic, stage_logs = _run_fixture_command(
            argv,
            repo_root=repo_root,
            environ=fixture_env,
            timeout_s=case.timeout_s,
            stdout_path=artifact_dir.parent / f"fixture_{stage}_stdout.log",
            stderr_path=artifact_dir.parent / f"fixture_{stage}_stderr.log",
            sensitive_values=sensitive_values,
        )
        logs.extend(stage_logs)
        if not ok:
            return {
                "ok": False,
                "error": f"fixture_{stage}",
                "returncode": returncode,
                "diagnostic": diagnostic,
                "logs": logs,
                "fixture_dir": "",
                "manifest": "",
            }
    return {
        "ok": True,
        "error": "",
        "returncode": 0,
        "diagnostic": "",
        "logs": logs,
        "fixture_dir": str(fixture_dir.resolve(strict=True)),
        "manifest": str(manifest.resolve(strict=True)),
        "manifest_sha256": fixture_manifest_sha256(manifest),
        "audio_api_origin": base_url,
    }


def _prepared_fixture_environment(
    case: E2ECase,
    *,
    repo_root: Path,
    run_root: Path,
    environ: Mapping[str, str],
    sensitive_values: Sequence[str],
) -> tuple[dict[str, str], dict[str, Any] | None]:
    prepared = {
        key: value
        for key, value in environ.items()
        if key not in _FIXTURE_ENV_NAMES
    }
    if case.fixture_pre_step is None:
        return prepared, None
    child_root = run_root / case.id
    artifact_dir = child_root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result = _run_fixture_pre_step(
        case,
        repo_root=repo_root,
        artifact_dir=artifact_dir,
        environ=prepared,
        sensitive_values=sensitive_values,
    )
    if result["ok"]:
        prepared.update({
            "E2E_VOICEPRINT_FIXTURE_DIR": result["fixture_dir"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST": result["manifest"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": (
                result["manifest_sha256"]
            ),
        })
    return prepared, result


def _attested_fixture_result(
    case: E2ECase,
    environ: Mapping[str, str],
) -> dict[str, Any] | None:
    if case.fixture_pre_step is None:
        return None
    fixture_dir = environ.get("E2E_VOICEPRINT_FIXTURE_DIR", "")
    manifest = environ.get("E2E_VOICEPRINT_FIXTURE_MANIFEST", "")
    manifest_sha256 = environ.get(
        "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
        "",
    )
    audio_api_origin = environ.get("E2E_AUDIO_API_ORIGIN", "")
    if (
        not fixture_dir
        or not manifest
        or not manifest_sha256
        or not audio_api_origin
    ):
        return {
            "ok": False,
            "error": "fixture_prepare",
            "returncode": None,
            "diagnostic": "lease child fixture attestation is missing",
            "logs": [],
            "fixture_dir": "",
            "manifest": "",
            "manifest_sha256": "",
            "audio_api_origin": "",
        }
    return {
        "ok": True,
        "error": "",
        "returncode": 0,
        "diagnostic": "",
        "logs": [],
        "fixture_dir": fixture_dir,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "audio_api_origin": audio_api_origin,
    }


def _fixture_failure_result(
    case: E2ECase,
    *,
    artifact_dir: Path,
    fixture_result: Mapping[str, Any],
) -> dict[str, Any]:
    logs = list(fixture_result.get("logs", []))
    return {
        "id": case.id,
        "status": "FAIL",
        "returncode": fixture_result.get("returncode"),
        "errors": [str(fixture_result.get("error") or "fixture_prepare")],
        "counts": dict(_EMPTY_COUNTS),
        "artifact_dir": str(artifact_dir.absolute()),
        "artifacts": logs,
        "logs": logs,
        "diagnostic": str(fixture_result.get("diagnostic") or ""),
        "result_file": str((artifact_dir.parent / "result.json").absolute()),
        "profile": case.profile,
        "timeout_s": case.timeout_s,
    }


def _fixture_bundle_arguments(
    fixture_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if fixture_result is None:
        return {}
    if not fixture_result.get("ok"):
        raise StackLeaseProtocolError(
            "fixture cannot be attested before successful verification",
        )
    return {
        "fixture_dir": Path(str(fixture_result["fixture_dir"])),
        "fixture_manifest": Path(str(fixture_result["manifest"])),
        "fixture_manifest_sha256": str(
            fixture_result["manifest_sha256"],
        ),
        "fixture_audio_api_origin": str(
            fixture_result["audio_api_origin"],
        ),
    }


def _run_child(
    case: E2ECase,
    *,
    repo_root: Path,
    run_root: Path,
    run_id: str,
    lane: str | None,
    provider: str | None,
    model: str | None,
    environ: Mapping[str, str],
    fixture_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    child_root = run_root / case.id
    artifact_dir = child_root / "artifacts"
    result_file = child_root / "result.json"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = child_root / "runner_stdout.log"
    stderr_log = child_root / "runner_stderr.log"
    env = _child_environment(
        environ,
        case=case,
        run_id=run_id,
        result_file=result_file,
        artifact_dir=artifact_dir,
        lane=lane,
        provider=provider,
        model=model,
    )
    sensitive_values = _runner_sensitive_values(env)
    fixture_logs = list(
        fixture_result.get("logs", [])
        if fixture_result is not None
        else ()
    )
    if case.fixture_pre_step is not None:
        if fixture_result is None and case.signed_identity:
            fixture_result = {
                "ok": False,
                "error": "fixture_prepare",
                "returncode": None,
                "diagnostic": "signed fixture was not prepared by its owner",
                "logs": [],
                "fixture_dir": "",
                "manifest": "",
                "manifest_sha256": "",
            }
        elif fixture_result is None:
            fixture_result = _run_fixture_pre_step(
                case,
                repo_root=repo_root,
                artifact_dir=artifact_dir,
                environ=env,
                sensitive_values=sensitive_values,
            )
            fixture_logs = list(fixture_result["logs"])
        if fixture_result.get("ok"):
            fixture_dir = str(fixture_result.get("fixture_dir") or "")
            fixture_manifest = str(fixture_result.get("manifest") or "")
            fixture_sha256 = str(
                fixture_result.get("manifest_sha256") or "",
            )
            fixture_audio_origin = str(
                fixture_result.get("audio_api_origin") or "",
            )
            if (
                not fixture_dir
                or not fixture_manifest
                or not fixture_sha256
                or not fixture_audio_origin
                or environ.get("E2E_STACK_LEASE_ROLE") == "child"
                and (
                    environ.get("E2E_VOICEPRINT_FIXTURE_DIR")
                    != fixture_dir
                    or environ.get("E2E_VOICEPRINT_FIXTURE_MANIFEST")
                    != fixture_manifest
                    or environ.get(
                        "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
                    )
                    != fixture_sha256
                    or environ.get("E2E_AUDIO_API_ORIGIN")
                    != fixture_audio_origin
                )
            ):
                fixture_result = {
                    "ok": False,
                    "error": "fixture_prepare",
                    "returncode": None,
                    "diagnostic": "prepared fixture attestation is invalid",
                    "logs": list(fixture_result.get("logs", [])),
                }
        if not fixture_result["ok"]:
            return _fixture_failure_result(
                case,
                artifact_dir=artifact_dir,
                fixture_result=fixture_result,
            )
        env.update({
            "E2E_VOICEPRINT_FIXTURE_DIR": fixture_result["fixture_dir"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST": fixture_result["manifest"],
            "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": (
                fixture_result["manifest_sha256"]
            ),
            "E2E_AUDIO_API_ORIGIN": fixture_result["audio_api_origin"],
        })
    argv = _child_argv(
        case,
        lane,
        provider,
        canonical=bool(environ.get("E2E_CANONICAL_METADATA")),
    )
    errors: list[str] = []
    returncode: int | None = None
    timed_out = False
    process: subprocess.Popen[bytes] | None = None
    process_tree: _ProcessTree | None = None
    capture: _BoundedPipeCapture | None = None
    stdout_tail = ""
    stderr_tail = ""
    logs: list[str] = []
    interrupted: KeyboardInterrupt | None = None
    try:
        process_tree = _ProcessTree()
        if not process_tree.available:
            errors.append("process_tree_unavailable")
        else:
            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | _WINDOWS_CREATE_SUSPENDED
                )
            process = subprocess.Popen(
                argv,
                cwd=repo_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
            capture = _BoundedPipeCapture(
                process,
                stdout_path=stdout_log,
                stderr_path=stderr_log,
                sensitive_values=sensitive_values,
            )
            if not process_tree.attach(process):
                errors.append("process_tree_unavailable")
            elif not process_tree.resume(process):
                errors.append("process_tree_unavailable")
            else:
                try:
                    returncode = process.wait(timeout=case.timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    errors.append("timeout")
                except KeyboardInterrupt as exc:
                    interrupted = exc
                except Exception:
                    errors.append("child_runtime_failed")
    except KeyboardInterrupt as exc:
        interrupted = exc
    except Exception:
        errors.append("child_start_failed")
    finally:
        try:
            if process is not None and process_tree is not None:
                _terminate_and_reap(process_tree, process)
                if returncode is None:
                    returncode = process.returncode
        finally:
            try:
                if capture is not None:
                    stdout_tail, stderr_tail, logs = capture.finish()
            finally:
                if process_tree is not None:
                    process_tree.close()

    if interrupted is not None:
        raise interrupted

    diagnostic = "\n".join(
        item for item in (stderr_tail, stdout_tail) if item
    )
    diagnostic = diagnostic[-MAX_DIAGNOSTIC_BYTES:]

    result: E2EResult | None = None
    logs = [*fixture_logs, *logs]
    artifacts: list[str] = list(logs)
    counts = dict(_EMPTY_COUNTS)
    outcome_case_ids = {"failed": [], "skipped": []}
    display = "FAIL"
    if process is not None and not {
        "process_tree_unavailable",
        "child_start_failed",
        "child_runtime_failed",
    }.intersection(errors) and result_file.is_file():
        try:
            result = _load_result(
                result_file,
                sensitive_values=sensitive_values,
            )
            if result.test_id != case.id or result.run_id != run_id:
                raise ProtocolError("result run/test identity does not match child")
            artifacts.extend(
                _artifact_paths(
                    result,
                    artifact_dir,
                    child_root=child_root,
                    sensitive_values=sensitive_values,
                ),
            )
            counts = dict(result.counts)
            outcome_case_ids = {
                "failed": [item["case_id"] for item in result.failures],
                "skipped": [item["case_id"] for item in result.skip_reasons],
            }
        except ProtocolError:
            errors.append("result_protocol")
    elif process is not None and not errors:
        errors.append("result_protocol")

    if result is not None and "result_protocol" not in errors:
        expected_returncode = {
            "pass": 0,
            "pass_with_skips": 0,
            "skip": 77,
            "fail": 1,
        }[result.status]
        if returncode != expected_returncode:
            errors.append("result_protocol")
        elif result.status == "pass":
            display = "PASS"
        elif result.status == "pass_with_skips":
            display = "PASS_WITH_SKIPS"
        elif result.status == "skip":
            display = "SKIP"
        else:
            display = "FAIL"
            errors.append("child_failed")
        if "result_protocol" not in errors:
            errors.extend(_skip_policy_errors(case, lane, result))
    if (
        process is not None
        and not timed_out
        and returncode not in {0, 77}
        and "child_failed" not in errors
        and not errors
    ):
        errors.append("child_failed")

    errors = list(dict.fromkeys(errors))
    return {
        "id": case.id,
        "status": display,
        "returncode": returncode,
        "errors": errors,
        "counts": counts,
        "outcome_case_ids": outcome_case_ids,
        "artifact_dir": str(artifact_dir.absolute()),
        "artifacts": artifacts,
        "logs": logs,
        "diagnostic": diagnostic,
        "result_file": str(result_file.absolute()),
        "profile": case.profile,
        "timeout_s": case.timeout_s,
    }


def _result_exit_code(
    results: Sequence[Mapping[str, Any]],
    *,
    stale: Staleness,
    stale_policy: str,
) -> int:
    if stale.stale and stale_policy == "error":
        return 3
    if any(item["errors"] for item in results):
        return 1
    return 0


def _journey_report_artifacts(
    results: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    journey = next(
        (item for item in results if item.get("id") == "e2e_journeys"),
        None,
    )
    if journey is None:
        raise RunnerArgumentError("canonical selection has no journeys result")
    found: dict[str, Path] = {}
    for raw in journey.get("artifacts", ()):
        path = Path(str(raw))
        if path.name in {"journeys_report.json", "journeys_report.md"}:
            if path.is_symlink():
                raise RunnerArgumentError("journey report artifact is a symlink")
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RunnerArgumentError(
                    "journey report artifact cannot be resolved",
                ) from exc
            if (
                not resolved.is_file()
                or resolved.stat().st_size > MAX_JOURNEY_REPORT_BYTES
            ):
                raise RunnerArgumentError("journey report artifact is invalid")
            found[path.suffix] = resolved
    if set(found) != {".json", ".md"}:
        raise RunnerArgumentError("journey report artifact pair is incomplete")
    return found[".json"], found[".md"]


def _aggregate_journey_shard_results(
    *,
    case: E2ECase,
    shard_results: Sequence[Mapping[str, Any]],
    expected_ids: Sequence[str],
    run_root: Path,
    run_id: str,
    provider: str | None,
    model: str | None,
    canonical_metadata: str = "",
) -> dict[str, Any]:
    """Merge sequential journey children without weakening the child protocol."""
    rows: list[dict[str, Any]] = []
    duration_s = 0.0
    locks: list[Mapping[str, Any]] = []
    child_errors: list[str] = []
    logs: list[str] = []
    diagnostics: list[str] = []
    for shard in shard_results:
        child_errors.extend(str(item) for item in shard.get("errors", ()))
        logs.extend(str(item) for item in shard.get("logs", ()))
        diagnostic = str(shard.get("diagnostic") or "")
        if diagnostic:
            diagnostics.append(diagnostic)
        json_path, _markdown_path = _journey_report_artifacts([{
            **shard,
            "id": "e2e_journeys",
        }])
        try:
            payload = strict_json_loads(_read_journey_report_text(json_path))
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ManifestError,
        ) as exc:
            raise RunnerArgumentError(
                "journey shard report cannot be parsed",
            ) from exc
        if not isinstance(payload, Mapping):
            raise RunnerArgumentError("journey shard report must be an object")
        report_rows = payload.get("journeys")
        lock = payload.get("provider_lock")
        if not isinstance(report_rows, list) or not isinstance(lock, Mapping):
            raise RunnerArgumentError("journey shard report is incomplete")
        rows.extend(
            dict(row)
            for row in report_rows
            if isinstance(row, Mapping)
        )
        if len(report_rows) != sum(
            1 for row in report_rows if isinstance(row, Mapping)
        ):
            raise RunnerArgumentError("journey shard rows are invalid")
        duration_s += float(payload.get("duration_s") or 0.0)
        locks.append(lock)

    expected = tuple(expected_ids)
    by_id = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row.get("id"), str)
    }
    if (
        len(by_id) != len(rows)
        or set(by_id) != set(expected)
        or len(set(expected)) != len(expected)
    ):
        raise RunnerArgumentError("journey shard corpus does not match")
    ordered_rows = [by_id[journey_id] for journey_id in expected]

    drifts = [
        dict(drift)
        for lock in locks
        for drift in (lock.get("drifts") or ())
        if isinstance(drift, Mapping)
    ]
    lock_provider = next(
        (
            str(lock.get("provider"))
            for lock in locks
            if isinstance(lock.get("provider"), str)
            and lock.get("provider")
        ),
        str(provider or ""),
    )
    lock_summary = {
        "provider": lock_provider,
        "locked": bool(locks) and all(bool(lock.get("locked")) for lock in locks),
        "drift_detected": bool(drifts) or any(
            bool(lock.get("drift_detected")) for lock in locks
        ),
        "drifts": drifts,
    }

    metadata: dict[str, Any] = {}
    if canonical_metadata:
        try:
            parsed = strict_json_loads(canonical_metadata)
        except (TypeError, json.JSONDecodeError, ManifestError) as exc:
            raise RunnerArgumentError(
                "canonical shard metadata is invalid",
            ) from exc
        if not isinstance(parsed, Mapping):
            raise RunnerArgumentError("canonical shard metadata must be an object")
        metadata.update(parsed)
    metadata.update({
        "run_id": run_id,
        "model": model or "",
        "report_scope": (
            "canonical_candidate"
            if canonical_metadata
            else "non_canonical_artifact"
        ),
        "scope": {
            "full": True,
            "journey_filters": {
                "ids": [],
                "suites": [],
                "lanes": [],
                "levels": [],
                "other": [],
            },
            "declared": len(expected),
            "selected": len(expected),
        },
    })

    from e2e_journeys import build_report_rows  # noqa: PLC0415

    data, markdown = build_report_rows(
        ordered_rows,
        lock_provider,
        "",
        duration_s,
        lock_summary,
        metadata=metadata,
    )
    artifact_dir = Path(run_root) / case.id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "journeys_report.json"
    markdown_path = artifact_dir / "journeys_report.md"
    atomic_write_report_pair(json_path, markdown_path, data, markdown)

    counts = data["counts"]
    aggregate_counts = {
        "selected": int(counts["selected"]),
        "executed": int(counts["executed"]),
        "passed": int(counts["pass"]),
        "failed": int(counts["fail"]),
        "skipped": int(counts["skip"]),
    }
    failed_ids = [
        journey_result_case_id(str(row["id"]))
        for row in ordered_rows
        if row.get("status") == "fail"
    ]
    skipped_ids = [
        journey_result_case_id(str(row["id"]))
        for row in ordered_rows
        if row.get("status") == "skip"
    ]
    errors = list(dict.fromkeys(child_errors))
    if failed_ids and "child_failed" not in errors:
        errors.append("child_failed")
    display = (
        "FAIL"
        if errors or failed_ids
        else "PASS_WITH_SKIPS"
        if skipped_ids
        else "PASS"
    )
    return {
        "id": case.id,
        "status": display,
        "returncode": 1 if display == "FAIL" else 0,
        "errors": errors,
        "counts": aggregate_counts,
        "outcome_case_ids": {
            "failed": failed_ids,
            "skipped": skipped_ids,
        },
        "artifact_dir": str(artifact_dir.absolute()),
        "artifacts": [*logs, str(json_path.absolute()), str(markdown_path.absolute())],
        "logs": logs,
        "diagnostic": "\n".join(diagnostics)[-MAX_DIAGNOSTIC_BYTES:],
        "result_file": "",
        "profile": case.profile,
        "timeout_s": case.timeout_s,
    }


def _run_signed_journey_shards(
    case: E2ECase,
    *,
    repo_root: Path,
    run_root: Path,
    run_id: str,
    lane: str | None,
    provider: str | None,
    model: str | None,
    source_env: Mapping[str, str],
    lease: IdentityStackLease,
    bundle_root: Path,
) -> dict[str, Any]:
    """Run bounded journey shards sequentially with just-in-time credentials."""
    expected_ids = tuple(canonical_journey_contract(repo_root))
    shards = _partition_journey_ids(expected_ids)
    if len(shards) < 2:
        raise RunnerArgumentError("journey sharding requires multiple shards")
    shard_results: list[dict[str, Any]] = []
    for index, journey_ids in enumerate(shards, 1):
        shard_id = f"e2e_journeys_shard_{index:02d}"
        shard_case = replace(
            case,
            id=shard_id,
            command=(
                *case.command,
                "--id",
                ",".join(journey_ids),
            ),
        )
        user_id = f"{run_id}-{shard_id}"
        bundle = write_token_bundle(
            root=bundle_root,
            lease_id=lease.lease_id,
            case_id=shard_id,
            run_id=run_id,
            user_id=user_id,
            vehicle_id=source_env.get("VEHICLE_ID", "v1"),
            timeout_s=case.timeout_s,
            secret=lease.secret,
            memory_sessions=_memory_sessions_for(case, lane),
        )
        bundle = _presign_memory_bundle(
            bundle,
            expected_bundle_root=bundle_root,
            secret=lease.secret,
            run_id=run_id,
            user_id=user_id,
            timeout_s=case.timeout_s,
            memory_sessions=_memory_sessions_for(case, lane),
        )
        child_source = load_child_bundle(
            bundle,
            bundle_root=bundle_root,
            lease_id=lease.lease_id,
            inherited={
                key: value
                for key, value in source_env.items()
                if key not in {
                    "E2E_IDENTITY_ENABLED",
                    "E2E_IDENTITY_SECRET",
                    "E2E_CAPABILITY_ENABLED",
                    "E2E_CAPABILITY_SECRET",
                    "E2E_NAMESPACE_ADMIN_ENABLED",
                    "E2E_NAMESPACE_ADMIN_SECRET",
                }
            },
        )
        _prove_journey_shard_owner(
            lease=lease,
            ws_base=source_env.get(
                "WS_URL",
                "ws://127.0.0.1:8090/ws",
            ),
            token=child_source["E2E_IDENTITY_TOKEN"],
            run_id=run_id,
            user_id=user_id,
            vehicle_id=source_env.get("VEHICLE_ID", "v1"),
        )
        shard_results.append(_run_child(
            shard_case,
            repo_root=repo_root,
            run_root=run_root / "journey-shards" / f"{index:02d}",
            run_id=run_id,
            lane=lane,
            provider=provider,
            model=model,
            environ=child_source,
        ))
    return _aggregate_journey_shard_results(
        case=case,
        shard_results=shard_results,
        expected_ids=expected_ids,
        run_root=run_root,
        run_id=run_id,
        provider=provider,
        model=model,
        canonical_metadata=str(
            source_env.get("E2E_CANONICAL_METADATA") or ""
        ),
    )


def _prove_journey_shard_owner(
    *,
    lease: IdentityStackLease,
    ws_base: str,
    token: str,
    run_id: str,
    user_id: str,
    vehicle_id: str,
) -> None:
    """Reassert a drifting shared gate once; never fall back to anonymous."""
    arguments = {
        "ws_base": ws_base,
        "token": token,
        "run_id": run_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
    }
    try:
        prove_identity_owner(**arguments)
        return
    except IdentityTokenError:
        pass
    try:
        lease.compose(lease.environ)
        if not lease.ready():
            raise RuntimeError("identity services did not recover")
        prove_identity_owner(**arguments)
    except (IdentityTokenError, OSError, RuntimeError) as exc:
        raise StackLeaseProtocolError(
            "journey shard owner proof failed closed",
        ) from exc


def _runtime_revision_view(runtime: Mapping[str, str]) -> dict[str, str]:
    return {
        key: runtime[key]
        for key in (
            "provider_revision",
            "capability_revision",
            "capability_source",
            "non_secret_config",
        )
    }


def _read_journey_report_text(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_JOURNEY_REPORT_BYTES + 1)
    if len(raw) > MAX_JOURNEY_REPORT_BYTES:
        raise RunnerArgumentError("journey report artifact exceeds size limit")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise RunnerArgumentError("journey report artifact is not UTF-8") from exc


def _canonical_report_binding_reasons(
    repo_root: Path,
    payload: Mapping[str, Any],
    journey_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Bind child claims to the tracked corpus and structured result protocol."""

    reasons: list[str] = []
    declarations = canonical_journey_contract(repo_root)
    report_rows = payload.get("journeys")
    report_by_id = {
        str(row.get("id")): row
        for row in report_rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    } if isinstance(report_rows, list) else {}
    if set(report_by_id) != set(declarations):
        reasons.append("report_corpus_mismatch")
    elif any(
        any(row.get(key) != expected[key] for key in (
            "level",
            "lane",
            "suite",
            "tags",
        ))
        for journey_id, expected in declarations.items()
        for row in (report_by_id[journey_id],)
    ):
        reasons.append("report_corpus_mismatch")

    if not isinstance(journey_result, Mapping):
        return tuple([*reasons, "report_child_outcomes_mismatch"])
    outcome_case_ids = journey_result.get("outcome_case_ids")
    actual_failed = (
        set(outcome_case_ids.get("failed", ()))
        if isinstance(outcome_case_ids, Mapping)
        else set()
    )
    actual_skipped = (
        set(outcome_case_ids.get("skipped", ()))
        if isinstance(outcome_case_ids, Mapping)
        else set()
    )
    expected_failed = {
        journey_result_case_id(journey_id)
        for journey_id, row in report_by_id.items()
        if row.get("status") == "fail"
    }
    expected_skipped = {
        journey_result_case_id(journey_id)
        for journey_id, row in report_by_id.items()
        if row.get("status") == "skip"
    }
    if actual_failed != expected_failed or actual_skipped != expected_skipped:
        reasons.append("report_child_outcomes_mismatch")
    return tuple(dict.fromkeys(reasons))


def _promote_canonical_report(
    *,
    repo_root: Path,
    results: Sequence[Mapping[str, Any]],
    state: CanonicalSnapshot,
    runtime_before: Mapping[str, str],
    runtime_after: Mapping[str, str],
    provider: str,
    model: str,
    manifest: E2EManifest,
    environ: Mapping[str, str],
    run_id: str,
) -> tuple[Staleness, tuple[str, ...]]:
    canonical_dir = repo_root / "docs" / "reviews" / "eval"
    recover_report_transaction(
        canonical_dir / "journeys_report.json",
        canonical_dir / "journeys_report.md",
    )
    json_artifact, markdown_artifact = _journey_report_artifacts(results)
    try:
        payload = strict_json_loads(_read_journey_report_text(json_artifact))
        markdown = _read_journey_report_text(markdown_artifact)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ManifestError,
    ) as exc:
        raise RunnerArgumentError("journey report artifact cannot be parsed") from exc
    if not isinstance(payload, Mapping):
        raise RunnerArgumentError("journey report artifact must be an object")
    scope = payload.get("scope")
    filters = scope.get("journey_filters") if isinstance(scope, Mapping) else {}
    reasons = list(canonical_write_reasons(
        state,
        selection=payload.get("selection", {}),
        journey_filters=filters if isinstance(filters, Mapping) else {},
        scope=scope if isinstance(scope, Mapping) else {},
        provider=provider,
        model=model,
        runtime_before=_runtime_revision_view(runtime_before),
        runtime_after=_runtime_revision_view(runtime_after),
        provider_lock=payload.get("provider_lock", {}),
    ))
    reasons.extend(canonical_report_count_reasons(payload))
    reasons.extend(canonical_report_detail_reasons(payload))
    journey_result = next(
        (item for item in results if item.get("id") == "e2e_journeys"),
        None,
    )
    reasons.extend(_canonical_report_binding_reasons(
        repo_root,
        payload,
        journey_result,
    ))
    child_counts = (
        journey_result.get("counts")
        if isinstance(journey_result, Mapping)
        else None
    )
    report_counts = payload.get("counts")
    if (
        not isinstance(child_counts, Mapping)
        or not isinstance(report_counts, Mapping)
        or {
            "selected": child_counts.get("selected"),
            "executed": child_counts.get("executed"),
            "pass": child_counts.get("passed"),
            "fail": child_counts.get("failed"),
            "skip": child_counts.get("skipped"),
        } != dict(report_counts)
    ):
        reasons.append("report_child_counts_mismatch")
    expected_input_state = {
        "dirty": state.dirty,
        "dirty_paths": list(state.dirty_paths),
        "untracked_input_paths": list(state.untracked_input_paths),
    }
    expected_runtime = {
        "provider": provider,
        "model": model,
        "provider_revision": runtime_before["provider_revision"],
        "capability_revision": runtime_before["capability_revision"],
        "capability_source": runtime_before["capability_source"],
    }
    if any(payload.get(key) != value for key, value in expected_runtime.items()):
        reasons.append("report_runtime_metadata_mismatch")
    if payload.get("run_id") != run_id:
        reasons.append("report_run_id_mismatch")
    if payload.get("code_sha") != _code_sha(repo_root):
        reasons.append("report_code_sha_mismatch")
    if payload.get("report_scope") != "canonical_candidate":
        reasons.append("report_scope_mismatch")
    if (
        payload.get("digests")
        != {"algorithm": "sha256", **dict(state.digests)}
        or payload.get("tracked_input_count") != state.tracked_input_count
        or payload.get("canonical_input_state") != expected_input_state
    ):
        reasons.append("report_digest_metadata_mismatch")
    run_id = payload.get("run_id")
    summary = payload.get("summary")
    if (
        not isinstance(run_id, str)
        or not isinstance(summary, str)
        or run_id not in markdown
        or summary not in markdown
    ):
        reasons.append("report_pair_mismatch")
    canonical_json = canonical_dir / "journeys_report.json"
    canonical_markdown = canonical_dir / "journeys_report.md"
    with tempfile.TemporaryDirectory(
        prefix=".e2e-canonical-candidate-",
        dir=repo_root,
    ) as candidate_dir:
        candidate_root = Path(candidate_dir)
        candidate_json = candidate_root / "journeys_report.json"
        candidate_markdown = candidate_root / "journeys_report.md"
        atomic_write_report_pair(
            candidate_json,
            candidate_markdown,
            payload,
            markdown,
        )
        candidate_freshness = evaluate_canonical_freshness(
            repo_root,
            state=state,
            report_path=candidate_json,
            markdown_path=candidate_markdown,
            milestone=True,
            runtime_current=runtime_after,
        )
    reasons.extend(candidate_freshness.reasons)
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return (
            Staleness(
                True,
                tuple(reasons),
                candidate_freshness.runtime_freshness,
            ),
            tuple(reasons),
        )

    old_json = (
        _read_bounded_report_bytes(canonical_json)
        if canonical_json.is_file()
        else None
    )
    old_markdown = (
        _read_bounded_report_bytes(canonical_markdown)
        if canonical_markdown.is_file()
        else None
    )
    atomic_write_report_pair(
        canonical_json,
        canonical_markdown,
        payload,
        markdown,
    )
    post_write_state = _canonical_snapshot(repo_root, manifest, environ)
    freshness = evaluate_canonical_freshness(
        repo_root,
        state=post_write_state,
        report_path=canonical_json,
        markdown_path=canonical_markdown,
        milestone=True,
        runtime_current=runtime_after,
    )
    if freshness.stale:
        if old_json is None or old_markdown is None:
            canonical_json.unlink(missing_ok=True)
            canonical_markdown.unlink(missing_ok=True)
        else:
            atomic_write_report_bytes_pair(
                canonical_json,
                canonical_markdown,
                old_json,
                old_markdown,
            )
        return (
            Staleness(
                True,
                freshness.reasons,
                freshness.runtime_freshness,
            ),
            freshness.reasons,
        )
    return (
        Staleness(
            freshness.stale,
            freshness.reasons,
            freshness.runtime_freshness,
        ),
        (),
    )


def _synthetic_subrun_result(
    case: E2ECase,
    *,
    error: str,
    diagnostic: str = "",
    returncode: int | None = None,
) -> dict[str, Any]:
    return {
        "id": case.id,
        "status": "FAIL",
        "returncode": returncode,
        "errors": [error],
        "counts": dict(_EMPTY_COUNTS),
        "outcome_case_ids": {"failed": [], "skipped": []},
        "artifact_dir": "",
        "artifacts": [],
        "logs": [],
        "diagnostic": diagnostic[-MAX_DIAGNOSTIC_BYTES:],
        "result_file": "",
        "profile": case.profile,
        "timeout_s": case.timeout_s,
    }


def _parse_subrun_summary(
    case: E2ECase,
    stdout_text: str,
    *,
    returncode: int,
    sensitive_values: Sequence[str],
) -> dict[str, Any]:
    safe_output = _redact_text(stdout_text, sensitive_values)
    for line in reversed(safe_output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, ProtocolError):
            continue
        results = payload.get("results") if isinstance(payload, Mapping) else None
        if (
            isinstance(results, list)
            and len(results) == 1
            and isinstance(results[0], Mapping)
            and results[0].get("id") == case.id
        ):
            result = dict(results[0])
            if returncode != payload.get("exit_code"):
                result["errors"] = list(dict.fromkeys(
                    [*result.get("errors", []), "result_protocol"],
                ))
                result["status"] = "FAIL"
            return result
    return _synthetic_subrun_result(
        case,
        error="result_protocol",
        diagnostic=safe_output,
        returncode=returncode,
    )


def _run_parallel_isolated(
    selected: Sequence[E2ECase],
    *,
    repo_root: Path,
    run_root: Path,
    run_id: str,
    lane: str,
    milestone: str,
    source_env: Mapping[str, str],
    lease: IdentityStackLease,
) -> list[dict[str, Any]]:
    launches: list[dict[str, Any]] = []
    bundle_root = run_root / "lease-bundles"
    stopped: set[int] = set()
    results: list[dict[str, Any]] = []
    interrupted: KeyboardInterrupt | None = None
    abort_launch = False

    def stop_launch(launch: Mapping[str, Any]) -> None:
        process = launch.get("process")
        process_tree = launch.get("tree")
        if process is None or process_tree is None or id(process) in stopped:
            return
        stopped.add(id(process))
        _terminate_and_reap(process_tree, process)

    def stop_all() -> None:
        for launch in launches:
            stop_launch(launch)

    def finalize_launch(
        launch: dict[str, Any],
        *,
        error: str = "",
    ) -> dict[str, Any]:
        existing = launch.get("result")
        if isinstance(existing, dict):
            return existing
        case = launch["case"]
        process = launch.get("process")
        capture = launch.get("capture")
        stdout_text = ""
        stderr_text = ""
        logs: list[str] = []
        if capture is not None:
            stdout_text, stderr_text, logs = capture.finish()
        returncode = launch.get("returncode")
        if not isinstance(returncode, int):
            returncode = getattr(process, "returncode", None)
        diagnostic = "\n".join(
            item for item in (stderr_text, stdout_text) if item
        )[-MAX_DIAGNOSTIC_BYTES:]
        if error:
            result = _synthetic_subrun_result(
                case,
                error=error,
                diagnostic=diagnostic,
                returncode=returncode,
            )
        else:
            result = _parse_subrun_summary(
                case,
                stdout_text,
                returncode=returncode if isinstance(returncode, int) else 1,
                sensitive_values=launch["sensitive"],
            )
            if stderr_text and not result.get("diagnostic"):
                result["diagnostic"] = stderr_text[-MAX_DIAGNOSTIC_BYTES:]
        result["logs"] = list(dict.fromkeys([
            *result.get("logs", []),
            *launch.get("fixture_logs", []),
            *logs,
        ]))
        launch["result"] = result
        return result

    try:
        for case in selected:
            if abort_launch:
                launches.append({
                    "case": case,
                    "process": None,
                    "tree": None,
                    "sensitive": (),
                    "error": "child_start_failed",
                })
                continue
            process_tree: _ProcessTree | None = None
            launch: dict[str, Any] = {
                "case": case,
                "process": None,
                "tree": None,
                "capture": None,
                "deadline": None,
                "returncode": None,
                "sensitive": (),
                "error": "",
                "result": None,
                "fixture_logs": [],
            }
            launches.append(launch)
            case_source, fixture_result = _prepared_fixture_environment(
                case,
                repo_root=repo_root,
                run_root=run_root,
                environ=source_env,
                sensitive_values=_runner_sensitive_values(source_env),
            )
            if fixture_result is not None:
                launch["fixture_logs"] = list(fixture_result.get("logs", []))
                if not fixture_result["ok"]:
                    launch["result"] = _fixture_failure_result(
                        case,
                        artifact_dir=run_root / case.id / "artifacts",
                        fixture_result=fixture_result,
                    )
                    continue
            try:
                process_tree = _ProcessTree()
                launch["tree"] = process_tree
                if not process_tree.available:
                    launch["error"] = "process_tree_unavailable"
                    abort_launch = True
                    stop_all()
                    continue
                bundle = write_token_bundle(
                    root=bundle_root,
                    lease_id=lease.lease_id,
                    case_id=case.id,
                    run_id=run_id,
                    user_id=f"{run_id}-{case.id}",
                    vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                    timeout_s=case.timeout_s,
                    secret=lease.secret,
                    memory_sessions=_memory_sessions_for(case, lane),
                    **_fixture_bundle_arguments(fixture_result),
                )
                bundle = _presign_memory_bundle(
                    bundle,
                    expected_bundle_root=bundle_root,
                    secret=lease.secret,
                    run_id=run_id,
                    user_id=f"{run_id}-{case.id}",
                    timeout_s=case.timeout_s,
                    memory_sessions=_memory_sessions_for(case, lane),
                )
                child_env = load_child_bundle(
                    bundle,
                    bundle_root=bundle_root,
                    lease_id=lease.lease_id,
                    inherited={
                        key: value
                        for key, value in case_source.items()
                        if key not in {
                            "E2E_IDENTITY_ENABLED",
                            "E2E_IDENTITY_SECRET",
                            "E2E_CAPABILITY_ENABLED",
                            "E2E_CAPABILITY_SECRET",
                            "E2E_NAMESPACE_ADMIN_ENABLED",
                            "E2E_NAMESPACE_ADMIN_SECRET",
                        }
                    },
                    fixture_required=case.fixture_pre_step is not None,
                )
                if (
                    child_env.get("E2E_IDENTITY_SECRET")
                    or child_env.get("E2E_CAPABILITY_SECRET")
                    or child_env.get("E2E_NAMESPACE_ADMIN_SECRET")
                ):
                    raise StackLeaseProtocolError(
                        "parallel child inherited owner secret",
                    )
                try:
                    prove_identity_owner(
                        ws_base=source_env.get(
                            "WS_URL",
                            "ws://127.0.0.1:8090/ws",
                        ),
                        token=child_env["E2E_IDENTITY_TOKEN"],
                        run_id=run_id,
                        user_id=f"{run_id}-{case.id}",
                        vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                    )
                except IdentityTokenError:
                    launch["error"] = "identity_owner_proof"
                    abort_launch = True
                    stop_all()
                    continue
                argv = parallel_subrun_argv(
                    repo_root=repo_root,
                    case_id=case.id,
                    lane=lane,
                    milestone=milestone,
                    lease_id=lease.lease_id,
                    token_bundle=bundle,
                )
                sensitive = _runner_sensitive_values(child_env)
                launch["sensitive"] = sensitive
                creationflags = 0
                if os.name == "nt":
                    creationflags = (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | _WINDOWS_CREATE_SUSPENDED
                    )
                process = subprocess.Popen(
                    argv,
                    cwd=repo_root,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=(os.name != "nt"),
                    creationflags=creationflags,
                )
                launch["process"] = process
                child_log_root = run_root / "parallel-subruns" / case.id
                child_log_root.mkdir(parents=True, exist_ok=True)
                launch["capture"] = _BoundedPipeCapture(
                    process,
                    stdout_path=child_log_root / "runner_stdout.log",
                    stderr_path=child_log_root / "runner_stderr.log",
                    sensitive_values=sensitive,
                )
                launch["deadline"] = (
                    time.monotonic() + case.timeout_s + 30
                )
                if not process_tree.attach(process):
                    launch["error"] = "process_tree_unavailable"
                    abort_launch = True
                    stop_all()
                elif not process_tree.resume(process):
                    launch["error"] = "process_tree_unavailable"
                    abort_launch = True
                    stop_all()
            except StackLeaseProtocolError:
                abort_launch = True
                stop_all()
                raise
            except KeyboardInterrupt as exc:
                interrupted = exc
                launch["error"] = "child_start_failed"
                abort_launch = True
                stop_all()
            except Exception:
                launch["error"] = "child_start_failed"
                abort_launch = True
                stop_all()

        if interrupted is not None:
            raise interrupted

        if abort_launch:
            stop_all()
            for launch in launches:
                if not launch["error"]:
                    launch["error"] = (
                        "child_runtime_failed"
                        if launch["process"] is not None
                        else "child_start_failed"
                    )
        else:
            pending = [
                launch
                for launch in launches
                if launch["process"] is not None and not launch["error"]
            ]
            while pending:
                progressed = False
                now = time.monotonic()
                for launch in list(pending):
                    process = launch["process"]
                    try:
                        returncode = process.poll()
                    except KeyboardInterrupt as exc:
                        interrupted = exc
                        stop_all()
                        break
                    except Exception:
                        launch["error"] = "child_runtime_failed"
                        stop_launch(launch)
                        finalize_launch(
                            launch,
                            error="child_runtime_failed",
                        )
                        pending.remove(launch)
                        progressed = True
                        continue
                    if returncode is not None:
                        launch["returncode"] = returncode
                        stop_launch(launch)
                        finalize_launch(launch)
                        pending.remove(launch)
                        progressed = True
                        continue
                    deadline = launch["deadline"]
                    if isinstance(deadline, (int, float)) and now >= deadline:
                        launch["error"] = "timeout"
                        stop_launch(launch)
                        finalize_launch(
                            launch,
                            error="timeout",
                        )
                        pending.remove(launch)
                        progressed = True
                        continue
                if interrupted is not None:
                    break
                if pending and not progressed:
                    time.sleep(0.02)

        if interrupted is not None:
            raise interrupted
        for launch in launches:
            error = launch["error"]
            if isinstance(launch.get("result"), dict):
                result = launch["result"]
            elif launch["process"] is None:
                result = _synthetic_subrun_result(
                    launch["case"],
                    error=error or "child_start_failed",
                )
                launch["result"] = result
            elif launch.get("result") is None:
                result = finalize_launch(
                    launch,
                    error=error,
                )
            else:  # pragma: no cover - guarded by the first branch
                result = launch["result"]
            results.append(result)
        return results
    finally:
        stop_all()
        for launch in launches:
            if (
                launch.get("process") is not None
                and launch.get("result") is None
            ):
                try:
                    finalize_launch(
                        launch,
                        error=launch.get("error") or "child_runtime_failed",
                    )
                except BaseException:
                    pass
        for launch in launches:
            process_tree = launch.get("tree")
            if process_tree is not None:
                try:
                    process_tree.close()
                except BaseException:
                    pass


def _run_profile_epochs(
    selected: Sequence[E2ECase],
    *,
    repo_root: Path,
    stack_root: Path | None = None,
    run_root: Path,
    run_id: str,
    lane: str | None,
    full: bool,
    provider: str | None,
    model: str | None,
    source_env: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run fixed serial epochs under one externally composed identity lease."""

    operational_root = repo_root if stack_root is None else stack_root
    results: list[dict[str, Any]] = []
    summary_errors: list[str] = []
    capability_secret = generate_secret()
    capability_state = {"enabled": False}
    namespace_admin_state = {"services": ()}

    def profile_lease_factory(**kwargs):
        owner_env = kwargs.get("environ")
        if not isinstance(owner_env, MutableMapping):
            raise StackLeaseProtocolError(
                "profile owner environment is not mutable",
            )
        _configure_capability_environment(
            owner_env,
            secret=capability_secret,
            enabled=capability_state["enabled"],
        )
        kwargs["secret_factory"] = lambda: capability_secret
        return IdentityStackLease(**kwargs)

    profile_compose = _profile_compose_with_capability(
        _profile_compose_runner(
            operational_root,
            build=(operational_root == repo_root),
            source_root=repo_root,
        ),
        secret=capability_secret,
        enabled=lambda: capability_state["enabled"],
        admin_services=lambda: namespace_admin_state["services"],
    )
    coordinator = ProfileCoordinator(
        repo_root=operational_root,
        environ=source_env,
        run_id=run_id,
        selected=selected,
        compose=profile_compose,
        cert_generator=_profile_cert_generator(operational_root, source_env),
        lease_factory=profile_lease_factory,
    )
    bundle_root = run_root / "lease-bundles"
    startup_error = ""

    def active_lease_environment() -> MutableMapping[str, str] | None:
        lease = coordinator.lease
        owner_env = getattr(lease, "environ", None)
        return owner_env if isinstance(owner_env, MutableMapping) else None

    try:
        for epoch in coordinator.epochs:
            capability_state["enabled"] = any(
                _memory_sessions_for(case, lane) > 0
                for case in epoch.cases
            )
            namespace_admin_state["services"] = _namespace_admin_services(
                epoch.cases,
            )
            owner_env = active_lease_environment()
            if owner_env is not None:
                _configure_capability_environment(
                    owner_env,
                    secret=capability_secret,
                    enabled=capability_state["enabled"],
                )
            if startup_error:
                results.extend(
                    _synthetic_subrun_result(case, error=startup_error)
                    for case in epoch.cases
                )
                continue
            try:
                coordinator.activate(epoch)
            except ProfilePreflightError:
                startup_error = "profile_preflight"
                results.extend(
                    _synthetic_subrun_result(case, error=startup_error)
                    for case in epoch.cases
                )
                continue
            except ProfileStartError:
                startup_error = "profile_start"
                results.extend(
                    _synthetic_subrun_result(case, error=startup_error)
                    for case in epoch.cases
                )
                continue
            except ProfileEnableError:
                startup_error = "profile_enable"
                results.extend(
                    _synthetic_subrun_result(case, error=startup_error)
                    for case in epoch.cases
                )
                continue

            for case in epoch.cases:
                if case.profile == "real":
                    preflight = coordinator.preflight_entry(case)
                    if not preflight.ok:
                        results.append(_synthetic_subrun_result(
                            case,
                            error=preflight.error or "entry_preflight",
                            diagnostic=preflight.diagnostic(),
                        ))
                        continue
                child_source = coordinator.child_environment(case)
                if operational_root != repo_root:
                    child_source["E2E_STACK_ROOT"] = str(operational_root)
                if case.fixture_pre_step is not None:
                    audio_origin = source_env.get(
                        "E2E_AUDIO_API_ORIGIN",
                        "",
                    )
                    if audio_origin:
                        child_source["E2E_AUDIO_API_ORIGIN"] = audio_origin
                child_source, fixture_result = _prepared_fixture_environment(
                    case,
                    repo_root=repo_root,
                    run_root=run_root,
                    environ=child_source,
                    sensitive_values=_runner_sensitive_values(child_source),
                )
                if fixture_result is not None and not fixture_result["ok"]:
                    results.append(_fixture_failure_result(
                        case,
                        artifact_dir=run_root / case.id / "artifacts",
                        fixture_result=fixture_result,
                    ))
                    continue
                journey_count = (
                    len(canonical_journey_contract(repo_root))
                    if (
                        case.id == "e2e_journeys"
                        and lane == "milestone"
                        and full
                    )
                    else 0
                )
                if _should_shard_journey_case(
                    case_id=case.id,
                    lane=lane,
                    lease_child=False,
                    has_lease=coordinator.lease is not None,
                    full=full,
                    journey_count=journey_count,
                ):
                    lease = coordinator.lease
                    if (
                        lease is None
                        or not lease.lease_id
                        or not lease.secret
                    ):
                        raise StackLeaseProtocolError(
                            "profile owner has no identity lease",
                        )
                    results.append(_run_signed_journey_shards(
                        case,
                        repo_root=repo_root,
                        run_root=run_root,
                        run_id=run_id,
                        lane=lane,
                        provider=provider,
                        model=model,
                        source_env=child_source,
                        lease=lease,
                        bundle_root=bundle_root,
                    ))
                    continue
                if case.signed_identity:
                    lease = coordinator.lease
                    if (
                        lease is None
                        or not lease.lease_id
                        or not lease.secret
                    ):
                        raise StackLeaseProtocolError(
                            "profile owner has no identity lease",
                        )
                    bundle = write_token_bundle(
                        root=bundle_root,
                        lease_id=lease.lease_id,
                        case_id=case.id,
                        run_id=run_id,
                        user_id=f"{run_id}-{case.id}",
                        vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                        timeout_s=case.timeout_s,
                        secret=lease.secret,
                        memory_sessions=_memory_sessions_for(case, lane),
                        **_fixture_bundle_arguments(fixture_result),
                    )
                    bundle = _presign_memory_bundle(
                        bundle,
                        expected_bundle_root=bundle_root,
                        secret=lease.secret,
                        run_id=run_id,
                        user_id=f"{run_id}-{case.id}",
                        timeout_s=case.timeout_s,
                        memory_sessions=_memory_sessions_for(case, lane),
                    )
                    child_source = load_child_bundle(
                        bundle,
                        bundle_root=bundle_root,
                        lease_id=lease.lease_id,
                        inherited={
                            key: value
                            for key, value in child_source.items()
                            if key not in {
                                "E2E_IDENTITY_ENABLED",
                                "E2E_IDENTITY_SECRET",
                                "E2E_CAPABILITY_ENABLED",
                                "E2E_CAPABILITY_SECRET",
                                "E2E_NAMESPACE_ADMIN_ENABLED",
                                "E2E_NAMESPACE_ADMIN_SECRET",
                            }
                        },
                        fixture_required=case.fixture_pre_step is not None,
                    )
                    try:
                        prove_identity_owner(
                            ws_base=source_env.get(
                                "WS_URL",
                                "ws://127.0.0.1:8090/ws",
                            ),
                            token=child_source["E2E_IDENTITY_TOKEN"],
                            run_id=run_id,
                            user_id=f"{run_id}-{case.id}",
                            vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                        )
                    except IdentityTokenError:
                        results.append(_synthetic_subrun_result(
                            case,
                            error="identity_owner_proof",
                        ))
                        continue
                results.append(
                    _run_child(
                        case,
                        repo_root=repo_root,
                        run_root=run_root,
                        run_id=run_id,
                        lane=lane,
                        provider=provider,
                        model=model,
                        environ=child_source,
                        fixture_result=fixture_result,
                    ),
                )
    except StackLeaseProtocolError:
        completed = {result["id"] for result in results}
        results.extend(
            _synthetic_subrun_result(case, error="lease_protocol")
            for case in selected
            if case.id not in completed
        )
    finally:
        capability_state["enabled"] = False
        namespace_admin_state["services"] = ()
        owner_env = active_lease_environment()
        if owner_env is not None:
            _configure_capability_environment(
                owner_env,
                secret=capability_secret,
                enabled=False,
            )
        if not _restore_profile_coordinator(coordinator):
            summary_errors.append("profile_restore")
        owner_env = active_lease_environment()
        if owner_env is not None:
            _clear_capability_environment(owner_env)

    if summary_errors:
        if not results:
            results = [
                _synthetic_subrun_result(case, error="profile_restore")
                for case in selected
            ]
        else:
            for result in results:
                result["errors"] = list(dict.fromkeys(
                    [*result.get("errors", []), "profile_restore"],
                ))
                result["status"] = "FAIL"
    return results, summary_errors


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path | str = REPO_ROOT,
    manifest_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    staleness_evaluator: Callable[[Path], Any] | None = None,
) -> int:
    """Run the selected manifest children and return the runner exit code."""

    output = sys.stdout if stdout is None else stdout
    root = Path(repo_root).resolve()
    manifest_file = (
        root / "test" / "e2e_manifest.yaml"
        if manifest_path is None
        else Path(manifest_path).resolve()
    )
    source_env: Mapping[str, str] = (
        os.environ if environ is None else dict(environ)
    )
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        _preflight(args)
    except RunnerArgumentError:
        summary = _base_summary(mode="preflight", args=args)
        summary["errors"] = ["preflight"]
        summary["exit_code"] = 2
        _emit(output, summary, human_lines=("E2E preflight: ERROR",))
        return 2

    try:
        stack_root = _resolve_stack_root(root, source_env)
        source_env = _load_stack_environment(stack_root, source_env)
    except RunnerArgumentError:
        summary = _base_summary(mode="preflight", args=args)
        summary["errors"] = ["preflight"]
        summary["exit_code"] = 2
        _emit(output, summary, human_lines=("E2E stack root: ERROR",))
        return 2

    mode = "check" if args.check else "dry_run" if args.dry_run else "run"
    summary = _base_summary(mode=mode, args=args)
    try:
        manifest = load_manifest(manifest_file, repo_root=root)
    except ManifestError:
        summary["errors"] = ["manifest"]
        summary["exit_code"] = 2
        _emit(output, summary, human_lines=("E2E manifest: ERROR",))
        return 2

    try:
        selected, effective_full = _select(manifest, args)
        if args.lease_child:
            try:
                source_env = load_child_bundle(
                    Path(args.token_bundle),
                    bundle_root=Path(args.token_bundle_root),
                    lease_id=args.lease_id,
                    inherited=source_env,
                    fixture_required=(
                        selected[0].fixture_pre_step is not None
                    ),
                )
            except StackLeaseProtocolError:
                summary["errors"] = ["lease_protocol"]
                summary["exit_code"] = 2
                _emit(
                    output,
                    summary,
                    human_lines=("E2E lease protocol: ERROR",),
                )
                return 2
        if (
            args.lease_child
            and source_env.get("E2E_TEST_ID") != selected[0].id
        ):
            raise RunnerArgumentError("token bundle case does not match selection")
        if args.provider and not any(
            case.id == "e2e_journeys" for case in selected
        ):
            raise RunnerArgumentError(
                "--provider/--model require e2e_journeys in the selection",
            )
        if any(case.fixture_pre_step is not None for case in selected):
            normalized_audio_origin = _normalize_audio_api_origin(
                source_env.get(
                    "E2E_AUDIO_API_ORIGIN",
                    "http://localhost:50059",
                ),
            )
            source_env = {
                key: value
                for key, value in source_env.items()
                if key != "AUDIO_API_URL"
            }
            source_env["E2E_AUDIO_API_ORIGIN"] = normalized_audio_origin
    except RunnerArgumentError as exc:
        summary["errors"] = [
            "selection_empty" if "empty" in str(exc) else "preflight",
        ]
        summary["exit_code"] = 2
        _emit(output, summary, human_lines=("E2E selection: ERROR",))
        return 2
    summary["full"] = effective_full
    summary["selection"] = _selection_summary(
        selected,
        args.lane,
        args.provider,
        canonical=args.canonical,
    )
    explicit_profile_scope = any((
        args.group is not None,
        args.lane is not None,
        bool(args.ids),
        args.profile is not None,
        args.full,
    ))
    profile_path = (
        _uses_profile_coordinator(selected)
        and explicit_profile_scope
        and not args.lease_child
    )
    summary["epochs"] = _profile_plan_summary(selected)
    summary["profile_restore"] = {
        "count": 1 if profile_path else 0,
        "profile": "default",
    }

    evaluator = evaluate_staleness if staleness_evaluator is None else staleness_evaluator
    try:
        stale = _normalize_staleness(evaluator(root))
    except (OSError, RunnerArgumentError, ValueError, TypeError):
        summary["errors"] = ["preflight"]
        summary["exit_code"] = 2
        _emit(output, summary, human_lines=("E2E staleness check: ERROR",))
        return 2
    if (
        staleness_evaluator is None
        and args.milestone is not None
        and stale.runtime_freshness == "unverified"
        and "runtime_unverified" not in stale.reasons
    ):
        stale = Staleness(
            True,
            (*stale.reasons, "runtime_unverified"),
            "unverified",
        )
    summary["stale"] = stale.to_dict()
    summary["runtime_freshness"] = stale.runtime_freshness
    if (
        staleness_evaluator is None
        and stale.runtime_freshness == "unverified"
        and args.milestone is None
    ):
        summary["warnings"].append("runtime_unverified")
    if stale.stale and args.stale_policy == "warn":
        summary["warnings"].append("stale_warning")

    if args.check or args.dry_run:
        exit_code = (
            3
            if stale.stale and args.stale_policy == "error"
            else 0
        )
        summary["exit_code"] = exit_code
        label = "CHECK" if args.check else "DRY_RUN"
        _emit(
            output,
            summary,
            human_lines=(f"E2E {label}: {'STALE' if exit_code == 3 else 'OK'}",),
        )
        return exit_code

    canonical_state: CanonicalSnapshot | None = None
    runtime_before: dict[str, str] | None = None
    if args.canonical:
        try:
            canonical_state = _canonical_snapshot(root, manifest, source_env)
            runtime_before = _runtime_state(
                provider=args.provider,
                model=args.model,
                non_secret_config=canonical_state.digests["non_secret_config"],
                planner_toolcall_enabled=_planner_toolcall_enabled(source_env),
            )
            metadata = _canonical_metadata(
                repo_root=root,
                args=args,
                state=canonical_state,
                runtime=runtime_before,
            )
        except (ManifestError, OSError, RunnerArgumentError, ValueError):
            summary["errors"] = ["canonical_preflight"]
            summary["exit_code"] = 2
            _emit(output, summary, human_lines=("E2E canonical preflight: ERROR",))
            return 2
        source_env = dict(source_env)
        source_env["E2E_CANONICAL_METADATA"] = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    run_id = (
        source_env["E2E_RUN_ID"]
        if args.lease_child
        else _new_run_id()
    )
    run_root = (
        Path(args.token_bundle_root).resolve(strict=True).parent
        if args.lease_child
        else Path(tempfile.mkdtemp(prefix=f"{run_id}-"))
    )
    results: list[dict[str, Any]] = []
    lease: IdentityStackLease | None = None
    lease_owner_env: MutableMapping[str, str] | None = None
    cleanup_failed = False
    profile_summary_errors: list[str] = []
    if (
        any(case.signed_identity for case in selected)
        and not args.lease_child
        and not profile_path
    ):
        mutable_env = (
            source_env
            if isinstance(source_env, MutableMapping)
            else dict(source_env)
        )
        lease_owner_env = mutable_env
        lease = _identity_capability_lease(
            repo_root=stack_root,
            source_root=root,
            environ=mutable_env,
            capability_enabled=any(
                _memory_sessions_for(case, args.lane) > 0
                for case in selected
            ),
            extra_services=_namespace_admin_services(selected),
            lease_factory=IdentityStackLease,
        )
        source_env = mutable_env
        try:
            lease.enable()
        except IdentityCleanupError:
            restored = _restore_identity_lease(lease)
            _clear_capability_environment(mutable_env)
            summary["run_id"] = run_id
            summary["errors"] = [
                "identity_enable" if restored else "identity_cleanup",
            ]
            summary["exit_code"] = 1
            _emit(
                output,
                summary,
                human_lines=(
                    "E2E identity stack: FAIL"
                    if restored
                    else "E2E identity cleanup: FAIL",
                ),
            )
            return 1
        except IdentityEnableError:
            _clear_capability_environment(mutable_env)
            summary["run_id"] = run_id
            summary["errors"] = ["identity_enable"]
            summary["exit_code"] = 1
            _emit(output, summary, human_lines=("E2E identity stack: FAIL",))
            return 1
        except BaseException:
            _clear_capability_environment(mutable_env)
            raise
    try:
        if profile_path:
            results, profile_summary_errors = _run_profile_epochs(
                selected,
                repo_root=root,
                stack_root=stack_root,
                run_root=run_root,
                run_id=run_id,
                lane=args.lane,
                full=args.full,
                provider=args.provider,
                model=args.model,
                source_env=source_env,
            )
        elif args.parallel_isolation is not None:
            if lease is None:
                raise StackLeaseProtocolError("parallel owner has no identity lease")
            results = _run_parallel_isolated(
                selected,
                repo_root=root,
                run_root=run_root,
                run_id=run_id,
                lane=args.lane,
                milestone=args.milestone,
                source_env=source_env,
                lease=lease,
            )
        else:
            bundle_root = run_root / "lease-bundles"
            for case in selected:
                journey_count = 0
                if (
                    case.id == "e2e_journeys"
                    and args.lane == "milestone"
                    and not args.lease_child
                    and lease is not None
                    and args.full
                ):
                    journey_count = len(canonical_journey_contract(root))
                if _should_shard_journey_case(
                    case_id=case.id,
                    lane=args.lane,
                    lease_child=args.lease_child,
                    has_lease=lease is not None,
                    full=args.full,
                    journey_count=journey_count,
                ):
                    results.append(_run_signed_journey_shards(
                        case,
                        repo_root=root,
                        run_root=run_root,
                        run_id=run_id,
                        lane=args.lane,
                        provider=args.provider,
                        model=args.model,
                        source_env=source_env,
                        lease=lease,
                        bundle_root=bundle_root,
                    ))
                    continue
                if args.lease_child:
                    child_source = dict(source_env)
                    fixture_result = _attested_fixture_result(
                        case,
                        child_source,
                    )
                else:
                    (
                        child_source,
                        fixture_result,
                    ) = _prepared_fixture_environment(
                        case,
                        repo_root=root,
                        run_root=run_root,
                        environ=source_env,
                        sensitive_values=_runner_sensitive_values(source_env),
                    )
                if fixture_result is not None and not fixture_result["ok"]:
                    results.append(_fixture_failure_result(
                        case,
                        artifact_dir=run_root / case.id / "artifacts",
                        fixture_result=fixture_result,
                    ))
                    continue
                if case.signed_identity and lease is not None:
                    bundle = write_token_bundle(
                        root=bundle_root,
                        lease_id=lease.lease_id,
                        case_id=case.id,
                        run_id=run_id,
                        user_id=f"{run_id}-{case.id}",
                        vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                        timeout_s=case.timeout_s,
                        secret=lease.secret,
                        memory_sessions=_memory_sessions_for(case, args.lane),
                        **_fixture_bundle_arguments(fixture_result),
                    )
                    bundle = _presign_memory_bundle(
                        bundle,
                        expected_bundle_root=bundle_root,
                        secret=lease.secret,
                        run_id=run_id,
                        user_id=f"{run_id}-{case.id}",
                        timeout_s=case.timeout_s,
                        memory_sessions=_memory_sessions_for(case, args.lane),
                    )
                    child_source = load_child_bundle(
                        bundle,
                        bundle_root=bundle_root,
                        lease_id=lease.lease_id,
                        inherited={
                            key: value
                            for key, value in child_source.items()
                            if key not in {
                                "E2E_IDENTITY_ENABLED",
                                "E2E_IDENTITY_SECRET",
                                "E2E_CAPABILITY_ENABLED",
                                "E2E_CAPABILITY_SECRET",
                                "E2E_NAMESPACE_ADMIN_ENABLED",
                                "E2E_NAMESPACE_ADMIN_SECRET",
                            }
                        },
                        fixture_required=case.fixture_pre_step is not None,
                    )
                    try:
                        prove_identity_owner(
                            ws_base=source_env.get(
                                "WS_URL",
                                "ws://127.0.0.1:8090/ws",
                            ),
                            token=child_source["E2E_IDENTITY_TOKEN"],
                            run_id=run_id,
                            user_id=f"{run_id}-{case.id}",
                            vehicle_id=source_env.get("VEHICLE_ID", "v1"),
                        )
                    except IdentityTokenError:
                        results.append(_synthetic_subrun_result(
                            case,
                            error="identity_owner_proof",
                        ))
                        continue
                elif case.signed_identity:
                    if (
                        source_env.get("E2E_STACK_LEASE_ROLE") != "child"
                        or not source_env.get("E2E_IDENTITY_TOKEN")
                        or source_env.get("E2E_IDENTITY_SECRET")
                        or source_env.get("E2E_CAPABILITY_SECRET")
                        or source_env.get("E2E_NAMESPACE_ADMIN_SECRET")
                    ):
                        raise StackLeaseProtocolError(
                            "signed child lacks a secret-free lease bundle",
                        )
                results.append(
                    _run_child(
                        case,
                        repo_root=root,
                        run_root=run_root,
                        run_id=run_id,
                        lane=args.lane,
                        provider=args.provider,
                        model=args.model,
                        environ=child_source,
                        fixture_result=fixture_result,
                    ),
                )
    except StackLeaseProtocolError:
        results = [
            _synthetic_subrun_result(case, error="lease_protocol")
            for case in selected
        ]
    finally:
        if lease is not None:
            if not _restore_identity_lease(lease):
                cleanup_failed = True
            if lease_owner_env is not None:
                _clear_capability_environment(lease_owner_env)
    if cleanup_failed:
        summary["errors"].append("identity_cleanup")
        if not results:
            results = [
                _synthetic_subrun_result(case, error="identity_cleanup")
                for case in selected
            ]
        else:
            for result in results:
                result["errors"] = list(dict.fromkeys(
                    [*result.get("errors", []), "identity_cleanup"],
                ))
                result["status"] = "FAIL"
    summary["errors"].extend(profile_summary_errors)
    summary["run_id"] = run_id
    summary["results"] = results
    if (
        args.canonical
        and canonical_state is not None
        and runtime_before is not None
        and not summary["errors"]
        and all(
            not item.get("errors")
            or (
                item.get("id") == "e2e_journeys"
                and set(item.get("errors", ())) == {"child_failed"}
            )
            for item in results
        )
    ):
        try:
            current_state = _canonical_snapshot(root, manifest, source_env)
            runtime_after = _runtime_state(
                provider=args.provider,
                model=args.model,
                non_secret_config=current_state.digests["non_secret_config"],
                planner_toolcall_enabled=_planner_toolcall_enabled(source_env),
            )
            promoted_freshness, rejection_reasons = _promote_canonical_report(
                repo_root=root,
                results=results,
                state=current_state,
                runtime_before=runtime_before,
                runtime_after=runtime_after,
                provider=args.provider,
                model=args.model,
                manifest=manifest,
                environ=source_env,
                run_id=run_id,
            )
        except (ManifestError, OSError, RunnerArgumentError, ValueError):
            promoted_freshness = Staleness(
                True,
                ("canonical_promotion_failed",),
                "stale",
            )
            rejection_reasons = promoted_freshness.reasons
        stale = promoted_freshness
        summary["stale"] = stale.to_dict()
        summary["runtime_freshness"] = stale.runtime_freshness
        summary["canonical_rejection_reasons"] = list(rejection_reasons)
        if rejection_reasons or stale.stale:
            summary["errors"].append("canonical_write")
            if not summary["canonical_rejection_reasons"]:
                summary["canonical_rejection_reasons"] = list(stale.reasons)
        else:
            summary["canonical_promoted"] = True
    exit_code = _result_exit_code(
        results,
        stale=stale,
        stale_policy=args.stale_policy,
    )
    if cleanup_failed:
        exit_code = 1
    elif summary["errors"] and exit_code == 0:
        exit_code = 1
    summary["exit_code"] = exit_code
    human = ["E2E summary:"]
    human.extend(
        f"  {item['id']}: {item['status']}"
        + (f" ({','.join(item['errors'])})" if item["errors"] else "")
        for item in results
    )
    _emit(output, summary, human_lines=human)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

"""Serial, secret-safe runtime profile epochs for manifest-driven E2E runs."""

from __future__ import annotations

import os
import re
import secrets
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from scripts.e2e_stack_lease import (
    IdentityCleanupError,
    IdentityEnableError,
    IdentityStackLease,
)


AUTH_SCOPES = (
    "vehicle.control",
    "media.control",
    "navigation",
    "food.ordering",
    "location.read",
    "navigation.control",
    "network.external",
    "payment.invoke",
)
IDENTITY_SERVICES = ("edge-gateway", "llm-gateway", "memory")
AUTH_SERVICES = (
    "edge-gateway",
    "cloud-gateway",
    "edge-orchestrator",
    "cloud-planner",
)
MTLS_CERTIFICATES = ("ca.crt", "server.crt", "server.key")
_PROFILE_ENV_NAMES = frozenset({
    "AUTH_REQUIRED",
    "AUTH_TOKENS",
    "PERMISSIONS_FAIL_OPEN",
    "CLOUD_CHANNEL_TOKEN",
    "CLOUD_CHANNEL_TOKENS",
    "GRPC_TLS",
    "WS_TOKEN",
})
_PREFLIGHT_ERRORS = frozenset({
    "credential_preflight",
    "provider_preflight",
    "entry_preflight",
})
_PREFLIGHT_FAMILY_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_PREFLIGHT_SENSITIVE_RE = re.compile(
    r"(?:secret|token|password|credential|authorization|cookie)",
    re.IGNORECASE,
)
_MAX_PREFLIGHT_FAMILIES = 8
_MAX_CERT_BYTES = 1024 * 1024
_S2S_CASE_IDS = frozenset({
    "e2e_s2s",
    "e2e_s2s_probe",
    "e2e_s2s_resilience",
})
_COMPOSE_BASE = (
    "docker",
    "compose",
    "-f",
    "compose.yaml",
    "up",
    "-d",
    "--build",
)
_READY_HTTP = (
    "http://127.0.0.1:8090/healthz",
    "http://127.0.0.1:50059/api/health",
)
_READY_TCP = (
    ("127.0.0.1", 50051),
    ("127.0.0.1", 50053),
    ("127.0.0.1", 50054),
    ("127.0.0.1", 50070),
    ("127.0.0.1", 8080),
)


def _is_profile_temporary_env(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("E2E_") or upper in _PROFILE_ENV_NAMES


class ProfileError(RuntimeError):
    """Base class for profile orchestration failures."""


class ProfilePreflightError(ProfileError):
    """A profile prerequisite is absent or unsafe."""


class ProfileEnableError(ProfileError):
    """A profile stack could not be rebuilt and proven ready."""


class ProfileStartError(ProfileEnableError):
    """An epoch stack was not ready, whether or not it required a rebuild."""


class ProfileRestoreError(ProfileError):
    """The root default stack could not be restored."""


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


def profile_services_ready(
    *,
    http_ready: Callable[[str], bool] = _http_ready,
    tcp_ready: Callable[[str, int], bool] = _tcp_ready,
) -> bool:
    """Probe every rebuilt auth service plus the shared identity mesh."""

    return (
        all(http_ready(url) for url in _READY_HTTP)
        and all(tcp_ready(host, port) for host, port in _READY_TCP)
    )


def wait_profile_services(timeout_s: int = 90) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if profile_services_ready():
            return True
        time.sleep(0.5)
    return False


@dataclass(frozen=True)
class ProfileEpoch:
    """One serial stack configuration containing manifest-order entries."""

    name: str
    profiles: tuple[str, ...]
    cases: tuple[Any, ...]

    @property
    def identity_enabled(self) -> bool:
        return any(bool(case.signed_identity) for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "profiles": list(self.profiles),
            "entries": [
                {"id": case.id, "profile": case.profile}
                for case in self.cases
            ],
            "restore": False,
        }


@dataclass(frozen=True)
class EntryPreflightResult:
    """Secret-free authorization result for one real-profile child."""

    ok: bool
    error: str = ""
    missing: tuple[str, ...] = ()

    def diagnostic(self) -> str:
        normalized = _normalize_entry_preflight(self)
        if normalized is None:
            return "entry preflight failed"
        ok, error, missing = normalized
        if ok:
            return ""
        if error == "credential_preflight" and missing:
            return (
                "credential preflight failed; missing families: "
                + ",".join(missing)
            )
        return "entry preflight failed"


def _normalize_entry_preflight(
    result: Any,
) -> tuple[bool, str, tuple[str, ...]] | None:
    """Validate hook output without hashing or stringifying untrusted fields."""

    try:
        if type(result) is not EntryPreflightResult:
            return None
        ok = result.ok
        error = result.error
        missing = result.missing
        if type(ok) is not bool:
            return None
        if type(error) is not str:
            return None
        if type(missing) not in {tuple, list}:
            return None
        if len(missing) > _MAX_PREFLIGHT_FAMILIES:
            return None
        normalized_missing: list[str] = []
        for family in missing:
            if (
                type(family) is not str
                or _PREFLIGHT_FAMILY_RE.fullmatch(family) is None
                or _PREFLIGHT_SENSITIVE_RE.search(family) is not None
            ):
                return None
            normalized_missing.append(family)
        families = tuple(normalized_missing)
        if ok:
            if error or families:
                return None
            return True, "", ()
        if error not in _PREFLIGHT_ERRORS:
            return None
        return False, error, families
    except BaseException:
        return None


EntryPreflightHook = Callable[
    [Any, frozenset[str]],
    EntryPreflightResult,
]


def plan_profile_epochs(selected: Sequence[Any]) -> tuple[ProfileEpoch, ...]:
    """Partition selection into fixed serial epochs while preserving case order."""

    buckets = {
        "default": [],
        "auth": [],
        "mtls": [],
    }
    for case in selected:
        if case.profile in {"root", "real"}:
            buckets["default"].append(case)
        elif case.profile == "auth":
            buckets["auth"].append(case)
        elif case.profile == "mtls":
            buckets["mtls"].append(case)
        else:
            raise ProfilePreflightError("unsupported_profile")
    epochs = []
    if buckets["default"]:
        present = {case.profile for case in buckets["default"]}
        profiles = tuple(
            profile for profile in ("root", "real") if profile in present
        )
        epochs.append(ProfileEpoch(
            name="default",
            profiles=profiles,
            cases=tuple(buckets["default"]),
        ))
    if buckets["auth"]:
        epochs.append(ProfileEpoch(
            name="auth",
            profiles=("auth",),
            cases=tuple(buckets["auth"]),
        ))
    if buckets["mtls"]:
        epochs.append(ProfileEpoch(
            name="mtls",
            profiles=("mtls",),
            cases=tuple(buckets["mtls"]),
        ))
    return tuple(epochs)


def _default_compose(repo_root: Path) -> Callable[[Sequence[str], Mapping[str, str]], None]:
    def invoke(argv: Sequence[str], environ: Mapping[str, str]) -> None:
        subprocess.run(
            list(argv),
            cwd=repo_root,
            env=dict(environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=900,
            check=True,
        )

    return invoke


def _default_cert_generator(repo_root: Path) -> Callable[[], bool]:
    def generate() -> bool:
        try:
            completed = subprocess.run(
                ["powershell", "-File", "scripts/gen-certs.ps1"],
                cwd=repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    return generate


def _default_cert_tracked(repo_root: Path) -> Callable[[Path], bool]:
    def tracked(path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return True
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    relative,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return completed.returncode == 0

    return tracked


class ProfileCoordinator:
    """Own every real Compose transition for a multi-profile runner path.

    The injected ``IdentityStackLease`` owns only the common-repository OS lock
    and the per-run signing secret. Its Compose callback is deliberately a
    no-op here; this coordinator performs the one real default-stack restore
    before releasing that lease.
    """

    RESTORE_LABEL = "default"

    def __init__(
        self,
        *,
        repo_root: Path,
        environ: Mapping[str, str],
        run_id: str,
        selected: Sequence[Any],
        compose: Callable[[Sequence[str], Mapping[str, str]], Any] | None = None,
        ready: Callable[[], bool] = wait_profile_services,
        cert_generator: Callable[[], bool] | None = None,
        cert_tracked: Callable[[Path], bool] | None = None,
        token_factory: Callable[[], str] | None = None,
        lease_factory: Callable[..., Any] = IdentityStackLease,
        entry_preflights: Mapping[str, EntryPreflightHook] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.run_id = run_id
        self.selected = tuple(selected)
        self.epochs = plan_profile_epochs(self.selected)
        self._base_env = {
            key: value
            for key, value in environ.items()
            if (
                isinstance(key, str)
                and isinstance(value, str)
                and not _is_profile_temporary_env(key)
            )
        }
        self._compose = compose or _default_compose(self.repo_root)
        self._ready = ready
        self._cert_generator = (
            cert_generator or _default_cert_generator(self.repo_root)
        )
        self._cert_tracked = (
            cert_tracked or _default_cert_tracked(self.repo_root)
        )
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._lease_factory = lease_factory
        self._entry_preflights = {
            "e2e_real_providers": self._real_providers_preflight,
            **{
                case_id: self._s2s_preflight
                for case_id in _S2S_CASE_IDS
            },
            **({} if entry_preflights is None else entry_preflights),
        }
        self._owner_env: MutableMapping[str, str] = dict(self._base_env)
        self.lease: Any | None = None
        self._active_epoch: ProfileEpoch | None = None
        self._activated: set[str] = set()
        self._auth_token = ""
        self._auth_case_id = ""
        self._mutated = False
        self._restored = False

    _CREDENTIAL_NAMES = frozenset({
        "AMAP_KEY",
        "QWEATHER_PROJECT_ID",
        "QWEATHER_KEY_ID",
        "QWEATHER_PRIVATE_KEY",
        "QWEATHER_PRIVATE_KEY_PATH",
        "QWEATHER_KEY",
        "ANYSEARCH_API_KEY",
        "BING_SEARCH_KEY",
        "SERPAPI_API_KEY",
        "TUSHARE_TOKEN",
        "S2S_API_KEY",
        "DASHSCOPE_ASR_KEY",
        "LLM_EMBED_API_KEY",
    })

    def _configured_credential_names(self) -> frozenset[str]:
        """Resolve only credential presence, honoring child dotenv precedence."""

        configured = {
            name
            for name in self._CREDENTIAL_NAMES
            if name in self._base_env and self._base_env[name].strip()
        }
        unresolved = self._CREDENTIAL_NAMES.difference(self._base_env)
        dotenv = self.repo_root / ".env"
        if unresolved and dotenv.is_file():
            try:
                lines = dotenv.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
            except OSError:
                lines = []
            for line in lines:
                stripped = line.strip()
                if (
                    not stripped
                    or stripped.startswith("#")
                    or "=" not in stripped
                ):
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                if key in unresolved and value.strip():
                    configured.add(key)
        return frozenset(configured)

    @staticmethod
    def _real_providers_preflight(
        _case: Any,
        configured: frozenset[str],
    ) -> EntryPreflightResult:
        """Mirror the five skip guards in test/e2e_real_providers.py."""

        qweather_jwt = ({
            "QWEATHER_PROJECT_ID",
            "QWEATHER_KEY_ID",
        }.issubset(configured) and (
            "QWEATHER_PRIVATE_KEY" in configured
            or "QWEATHER_PRIVATE_KEY_PATH" in configured
        ))
        requirements = (
            ("amap", "AMAP_KEY" in configured),
            (
                "qweather",
                "QWEATHER_KEY" in configured or qweather_jwt,
            ),
            (
                "search",
                bool({
                    "ANYSEARCH_API_KEY",
                    "BING_SEARCH_KEY",
                }.intersection(configured)),
            ),
            ("news", "SERPAPI_API_KEY" in configured),
            ("stock", "TUSHARE_TOKEN" in configured),
        )
        missing = tuple(
            family for family, present in requirements if not present
        )
        return EntryPreflightResult(
            ok=not missing,
            error="" if not missing else "credential_preflight",
            missing=missing,
        )

    @staticmethod
    def _s2s_preflight(
        _case: Any,
        configured: frozenset[str],
    ) -> EntryPreflightResult:
        available = bool({
            "S2S_API_KEY",
            "DASHSCOPE_ASR_KEY",
            "LLM_EMBED_API_KEY",
        }.intersection(configured))
        return EntryPreflightResult(
            ok=available,
            error="" if available else "credential_preflight",
            missing=() if available else ("s2s",),
        )

    def preflight_entry(self, case: Any) -> EntryPreflightResult:
        """Run the selected real entry's hook before any child authorization."""

        epoch = self._active_epoch
        if epoch is None or case not in epoch.cases:
            raise ProfileEnableError("profile_enable")
        if case.profile != "real":
            return EntryPreflightResult(ok=True)
        hook = self._entry_preflights.get(case.id)
        if hook is None:
            return EntryPreflightResult(ok=True)
        try:
            result = hook(case, self._configured_credential_names())
        except Exception:
            return EntryPreflightResult(
                ok=False,
                error="entry_preflight",
            )
        normalized = _normalize_entry_preflight(result)
        if normalized is None:
            return EntryPreflightResult(
                ok=False,
                error="entry_preflight",
            )
        ok, error, missing = normalized
        if ok:
            return EntryPreflightResult(ok=True)
        return EntryPreflightResult(
            ok=False,
            error=error,
            missing=missing,
        )

    def _ensure_lease(self) -> None:
        if self.lease is not None:
            return
        lease = None
        try:
            lease = self._lease_factory(
                repo_root=self.repo_root,
                environ=self._owner_env,
                compose=lambda _env: None,
                ready=lambda: True,
            )
            self.lease = lease
            lease.enable()
        except (IdentityEnableError, IdentityCleanupError) as exc:
            raise ProfileEnableError("profile_enable") from exc
        except ProfileError:
            raise
        except Exception as exc:
            raise ProfileEnableError("profile_enable") from exc
        self.lease = lease

    def _identity_env(self, target: dict[str, str]) -> None:
        secret = self._owner_env.get("E2E_IDENTITY_SECRET")
        if (
            self.lease is None
            or not isinstance(secret, str)
            or not secret
        ):
            raise ProfileEnableError("profile_enable")
        target["E2E_IDENTITY_ENABLED"] = "true"
        target["E2E_IDENTITY_SECRET"] = secret

    def _auth_env(self) -> dict[str, str]:
        cases = [
            case for case in self.selected if case.profile == "auth"
        ]
        if not cases:
            raise ProfilePreflightError("auth_profile")
        if not self._auth_token:
            auth_token = self._token_factory()
            channel_token = self._token_factory()
            if (
                not isinstance(auth_token, str)
                or not auth_token
                or not isinstance(channel_token, str)
                or not channel_token
                or auth_token == channel_token
            ):
                raise ProfilePreflightError("auth_profile")
            self._auth_token = auth_token
            self._auth_case_id = cases[0].id
            self._channel_token = channel_token
        user_id = f"{self.run_id}-{self._auth_case_id}"
        vehicle_id = self._base_env.get("VEHICLE_ID", "v1")
        env = dict(self._base_env)
        env.update({
            "AUTH_REQUIRED": "true",
            "PERMISSIONS_FAIL_OPEN": "false",
            "AUTH_TOKENS": (
                f"{self._auth_token}:{user_id}:{vehicle_id}:"
                + ",".join(AUTH_SCOPES)
            ),
            "CLOUD_CHANNEL_TOKEN": self._channel_token,
            "CLOUD_CHANNEL_TOKENS": self._channel_token,
        })
        return env

    @staticmethod
    def _is_link_or_reparse(path: Path, metadata: os.stat_result) -> bool:
        reparse_flag = getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        )
        return (
            path.is_symlink()
            or (
                hasattr(os.path, "isjunction")
                and os.path.isjunction(path)
            )
            or bool(
                getattr(metadata, "st_file_attributes", 0)
                & reparse_flag
            )
        )

    def _mtls_material_valid(self, paths: Sequence[Path]) -> bool:
        """Validate filesystem safety, PEM material, key match, and CA chain."""

        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization

            cert_root = self.repo_root / "certs"
            root_metadata = cert_root.lstat()
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or self._is_link_or_reparse(cert_root, root_metadata)
            ):
                return False
            resolved_root = cert_root.resolve(strict=True)
            if resolved_root.parent != self.repo_root:
                return False

            material: dict[str, bytes] = {}
            for path in paths:
                if path.parent != cert_root:
                    return False
                metadata = path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or self._is_link_or_reparse(path, metadata)
                    or metadata.st_size <= 0
                    or metadata.st_size > _MAX_CERT_BYTES
                    or path.resolve(strict=True).parent != resolved_root
                ):
                    return False
                if (
                    path.name == "server.key"
                    and os.name != "nt"
                    and stat.S_IMODE(metadata.st_mode)
                    & (stat.S_IRGRP | stat.S_IROTH)
                ):
                    return False
                material[path.name] = path.read_bytes()

            ca_cert = x509.load_pem_x509_certificate(material["ca.crt"])
            server_cert = x509.load_pem_x509_certificate(
                material["server.crt"],
            )
            private_key = serialization.load_pem_private_key(
                material["server.key"],
                password=None,
            )
            ca_constraints = ca_cert.extensions.get_extension_for_class(
                x509.BasicConstraints,
            ).value
            if not ca_constraints.ca:
                return False
            server_constraints = server_cert.extensions.get_extension_for_class(
                x509.BasicConstraints,
            ).value
            if server_constraints.ca:
                return False
            if server_cert.issuer != ca_cert.subject:
                return False
            server_cert.verify_directly_issued_by(ca_cert)
            public_format = serialization.PublicFormat.SubjectPublicKeyInfo
            public_encoding = serialization.Encoding.DER
            return (
                private_key.public_key().public_bytes(
                    public_encoding,
                    public_format,
                )
                == server_cert.public_key().public_bytes(
                    public_encoding,
                    public_format,
                )
            )
        except Exception:
            return False

    def _ensure_mtls_certificates(self) -> None:
        paths = tuple(
            self.repo_root / "certs" / name
            for name in MTLS_CERTIFICATES
        )
        if any(self._cert_tracked(path) for path in paths):
            raise ProfilePreflightError("mtls_certificates")
        if not self._mtls_material_valid(paths):
            try:
                generated = self._cert_generator()
            except Exception as exc:
                raise ProfilePreflightError("mtls_certificates") from exc
            if not generated or not self._mtls_material_valid(paths):
                raise ProfilePreflightError("mtls_certificates")
        if any(self._cert_tracked(path) for path in paths):
            raise ProfilePreflightError("mtls_certificates")

    def _epoch_environment(self, epoch: ProfileEpoch) -> dict[str, str]:
        if epoch.name == "default":
            env = dict(self._base_env)
            if any(case.id in _S2S_CASE_IDS for case in epoch.cases):
                if str(env.get("S2S_PROVIDER") or "").strip().lower() in {
                    "",
                    "off",
                    "none",
                }:
                    env["S2S_PROVIDER"] = "dashscope"
            if epoch.identity_enabled:
                self._identity_env(env)
            return env
        if epoch.name == "auth":
            return self._auth_env()
        if epoch.name == "mtls":
            self._ensure_mtls_certificates()
            env = dict(self._base_env)
            env["GRPC_TLS"] = "on"
            if epoch.identity_enabled:
                self._identity_env(env)
            return env
        raise ProfilePreflightError("unsupported_profile")

    @staticmethod
    def _epoch_command(epoch: ProfileEpoch) -> tuple[str, ...] | None:
        if epoch.name == "default":
            if not epoch.identity_enabled:
                return None
            return _COMPOSE_BASE + IDENTITY_SERVICES
        if epoch.name == "auth":
            return _COMPOSE_BASE + AUTH_SERVICES
        if epoch.name == "mtls":
            return _COMPOSE_BASE
        raise ProfilePreflightError("unsupported_profile")

    def activate(self, epoch: ProfileEpoch) -> dict[str, str]:
        """Rebuild one epoch and return its owner-only Compose environment."""

        if self._restored:
            raise ProfileEnableError("profile_enable")
        if epoch not in self.epochs or epoch.name in self._activated:
            raise ProfileEnableError("profile_enable")
        self._ensure_lease()
        env = self._epoch_environment(epoch)
        command = self._epoch_command(epoch)
        try:
            if command is not None:
                self._mutated = True
                self._compose(command, env)
            if not self._ready():
                raise RuntimeError("profile stack did not become ready")
        except ProfileError:
            raise
        except Exception as exc:
            raise ProfileStartError("profile_start") from exc
        self._active_epoch = epoch
        self._activated.add(epoch.name)
        return env

    def child_environment(self, case: Any) -> dict[str, str]:
        """Return a secret-minimal base environment for one active child."""

        epoch = self._active_epoch
        if epoch is None or case not in epoch.cases:
            raise ProfileEnableError("profile_enable")
        env = dict(self._base_env)
        if case.profile == "auth":
            if case.id != self._auth_case_id or not self._auth_token:
                raise ProfilePreflightError("auth_profile")
            env.update({
                "WS_TOKEN": self._auth_token,
                "E2E_USER_ID": f"{self.run_id}-{case.id}",
            })
        return env

    def restore(self) -> None:
        """Restore defaults before releasing the retryable owner lease."""

        if self._restored:
            return
        if self._mutated:
            try:
                self._compose(_COMPOSE_BASE, self._base_env)
                if not self._ready():
                    raise RuntimeError("default stack did not become ready")
            except Exception as exc:
                raise ProfileRestoreError("profile_restore") from exc
        if self.lease is not None:
            try:
                self.lease.restore()
            except Exception as exc:
                raise ProfileRestoreError("profile_restore") from exc
        self._restored = True

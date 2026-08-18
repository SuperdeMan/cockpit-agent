from __future__ import annotations

import importlib.util
import asyncio
import ctypes
import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "scripts" / "e2e_profiles.py"
RUNNER_PATH = REPO_ROOT / "scripts" / "run_e2e.py"
MANIFEST_PATH = REPO_ROOT / "test" / "e2e_manifest.yaml"

_PROFILES: ModuleType | None = None
_RUNNER: ModuleType | None = None


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _profiles() -> ModuleType:
    global _PROFILES
    if _PROFILES is None:
        if not PROFILE_PATH.is_file():
            pytest.fail("scripts/e2e_profiles.py behavior has not been implemented")
        _PROFILES = _load(PROFILE_PATH, "task5b_e2e_profiles")
    return _PROFILES


def _runner() -> ModuleType:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = _load(RUNNER_PATH, "task5b_run_e2e")
    return _RUNNER


def _case(
    case_id: str,
    profile: str,
    *,
    signed_identity: bool = False,
    fixture_pre_step: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=case_id,
        profile=profile,
        signed_identity=signed_identity,
        timeout_s=30,
        memory_sessions=0,
        fixture_pre_step=fixture_pre_step,
    )


def _write_valid_mtls_chain(root: Path) -> None:
    cert_root = root / "certs"
    cert_root.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    now = datetime.now(UTC)
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "e2e-test-ca"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "cockpit-mesh"),
    ])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (cert_root / "ca.crt").write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM),
    )
    (cert_root / "server.crt").write_bytes(
        server_cert.public_bytes(serialization.Encoding.PEM),
    )
    server_key_path = cert_root / "server.key"
    server_key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    if os.name != "nt":
        server_key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


class _PidProbe:
    """Retain a Windows process handle so PID reuse cannot fake cleanup."""

    def __init__(self, pid_file: Path) -> None:
        self.pid_file = pid_file
        self.pid: int | None = None
        self.handle: int | None = None
        self.thread = threading.Thread(target=self._capture, daemon=True)
        self.thread.start()

    def _capture(self) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.pid = int(self.pid_file.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(0.01)
        if self.pid is None or os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(
            0x00100000 | 0x1000,
            0,
            self.pid,
        )
        if handle:
            self.handle = int(handle)

    def assert_exited(self, *, deadline_s: float = 5) -> None:
        self.thread.join(timeout=9)
        assert self.pid is not None, "descendant PID was not recorded"
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.WaitForSingleObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            try:
                assert kernel32.WaitForSingleObject(
                    self.handle,
                    int(deadline_s * 1000),
                ) == 0
            finally:
                kernel32.CloseHandle(self.handle)
            return
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            try:
                os.kill(self.pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        pytest.fail("descendant PID is still active")


class _FakeLease:
    def __init__(self, *, environ: dict[str, str], events: list[str]) -> None:
        self.environ = environ
        self.events = events
        self.lease_id = "lease-profile-owner"
        self.secret = b"x" * 32

    def enable(self) -> None:
        self.events.append("lease:enable")
        self.environ.update({
            "E2E_IDENTITY_ENABLED": "true",
            "E2E_IDENTITY_SECRET": "owner-secret",
        })

    def restore(self) -> None:
        self.events.append("lease:restore")
        self.environ.pop("E2E_IDENTITY_ENABLED", None)
        self.environ.pop("E2E_IDENTITY_SECRET", None)
        self.secret = b""


def _coordinator(
    tmp_path: Path,
    selected: list[SimpleNamespace],
    *,
    compose: Any,
    ready=lambda: True,
    cert_generator=lambda: True,
    cert_tracked=lambda _path: False,
    token_values: tuple[str, ...] = ("auth-random", "channel-random"),
    events: list[str] | None = None,
    environ: dict[str, str] | None = None,
    entry_preflights: dict[str, Any] | None = None,
):
    module = _profiles()
    event_log = [] if events is None else events
    tokens = iter(token_values)

    def lease_factory(*, environ, **_kwargs):
        return _FakeLease(environ=environ, events=event_log)

    return module.ProfileCoordinator(
        repo_root=tmp_path,
        environ={} if environ is None else environ,
        run_id="e2e-profile-run",
        selected=selected,
        compose=compose,
        ready=ready,
        cert_generator=cert_generator,
        cert_tracked=cert_tracked,
        token_factory=lambda: next(tokens),
        lease_factory=lease_factory,
        entry_preflights=entry_preflights,
    )


def test_manifest_order_is_partitioned_into_three_serial_profile_epochs():
    module = _profiles()
    selected = [
        _case("auth-a", "auth"),
        _case("root-a", "root", signed_identity=True),
        _case("real-a", "real"),
        _case("mtls-a", "mtls", signed_identity=True),
        _case("root-b", "root"),
    ]

    epochs = module.plan_profile_epochs(selected)

    assert [epoch.name for epoch in epochs] == ["default", "auth", "mtls"]
    assert [[case.id for case in epoch.cases] for epoch in epochs] == [
        ["root-a", "real-a", "root-b"],
        ["auth-a"],
        ["mtls-a"],
    ]
    assert epochs[0].profiles == ("root", "real")
    assert epochs[1].profiles == ("auth",)
    assert epochs[2].profiles == ("mtls",)


def test_profile_envs_commands_and_auth_token_scope_are_frozen_and_secret_free_for_children(
    tmp_path: Path,
):
    module = _profiles()
    selected = [
        _case("root-a", "root", signed_identity=True),
        _case("real-a", "real"),
        _case("auth-a", "auth"),
        _case("mtls-a", "mtls", signed_identity=True),
    ]
    calls: list[tuple[list[str], dict[str, str]]] = []
    events: list[str] = []
    inherited = {
        "PATH": os.environ.get("PATH", ""),
        "BUSINESS_REGION": "cn-east-long-lived",
        "AUTH_REQUIRED": "stale-parent-auth",
        "AUTH_TOKENS": "stale-parent-token",
        "PERMISSIONS_FAIL_OPEN": "stale-parent-permissions",
        "CLOUD_CHANNEL_TOKEN": "stale-parent-channel",
        "CLOUD_CHANNEL_TOKENS": "stale-parent-channels",
        "WS_TOKEN": "stale-parent-ws",
        "GRPC_TLS": "stale-parent-tls",
        "E2E_RUN_ID": "stale-parent-run",
        "E2E_USER_ID": "stale-parent-user",
        "E2E_IDENTITY_TOKEN": "stale-parent-identity",
        "E2E_PROVIDER": "stale-parent-provider",
        "E2E_FUTURE_NAMESPACE": "stale-parent-future",
    }

    def compose(argv, env):
        calls.append((list(argv), dict(env)))
        events.append(f"compose:{len(calls)}")

    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=compose,
        events=events,
        environ=inherited,
    )
    _write_valid_mtls_chain(tmp_path)

    epochs = coordinator.epochs
    default_env = coordinator.activate(epochs[0])
    root_child = coordinator.child_environment(selected[0])
    auth_env = coordinator.activate(epochs[1])
    auth_child = coordinator.child_environment(selected[2])
    mtls_env = coordinator.activate(epochs[2])
    mtls_child = coordinator.child_environment(selected[3])
    coordinator.restore()

    assert [event for event in events if event.startswith("compose:")] == [
        "compose:1",
        "compose:2",
        "compose:3",
        "compose:4",
    ]
    assert events[0] == "lease:enable"
    assert events[-1] == "lease:restore"
    assert calls[0][0] == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "up",
        "-d",
        "--build",
        "edge-gateway",
        "llm-gateway",
        "memory",
    ]
    assert calls[1][0][-4:] == [
        "edge-gateway",
        "cloud-gateway",
        "edge-orchestrator",
        "cloud-planner",
    ]
    assert calls[2][0] == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "up",
        "-d",
        "--build",
    ]
    assert calls[3][0] == calls[2][0]
    for _argv, env in calls:
        assert env["BUSINESS_REGION"] == "cn-east-long-lived"
        assert "E2E_RUN_ID" not in env
        assert "E2E_USER_ID" not in env
        assert "E2E_IDENTITY_TOKEN" not in env
        assert "E2E_PROVIDER" not in env
        assert "E2E_FUTURE_NAMESPACE" not in env
        assert "WS_TOKEN" not in env

    auth_compose_env = calls[1][1]
    assert not any(name.startswith("E2E_") for name in auth_compose_env)
    restore_env = calls[3][1]
    assert not any(name.startswith("E2E_") for name in restore_env)
    assert not {
        "AUTH_REQUIRED",
        "AUTH_TOKENS",
        "PERMISSIONS_FAIL_OPEN",
        "CLOUD_CHANNEL_TOKEN",
        "CLOUD_CHANNEL_TOKENS",
        "WS_TOKEN",
        "GRPC_TLS",
    }.intersection(restore_env)

    assert default_env["E2E_IDENTITY_ENABLED"] == "true"
    assert default_env["E2E_IDENTITY_SECRET"] == "owner-secret"
    assert "AUTH_TOKENS" not in default_env
    assert "GRPC_TLS" not in default_env

    expected_scopes = (
        "vehicle.control,media.control,navigation,food.ordering,location.read,"
        "navigation.control,network.external,payment.invoke"
    )
    assert auth_env["AUTH_REQUIRED"] == "true"
    assert auth_env["PERMISSIONS_FAIL_OPEN"] == "false"
    assert auth_env["AUTH_TOKENS"] == (
        "auth-random:e2e-profile-run-auth-a:v1:" + expected_scopes
    )
    assert auth_env["CLOUD_CHANNEL_TOKEN"] == "channel-random"
    assert auth_env["CLOUD_CHANNEL_TOKENS"] == "channel-random"
    assert "E2E_IDENTITY_SECRET" not in auth_env
    assert "E2E_IDENTITY_ENABLED" not in auth_env

    assert mtls_env["GRPC_TLS"] == "on"
    assert mtls_env["E2E_IDENTITY_ENABLED"] == "true"
    assert mtls_env["E2E_IDENTITY_SECRET"] == "owner-secret"

    assert auth_child["WS_TOKEN"] == "auth-random"
    assert auth_child["E2E_USER_ID"] == "e2e-profile-run-auth-a"
    assert "AUTH_TOKENS" not in auth_child
    assert "CLOUD_CHANNEL_TOKEN" not in auth_child
    assert "WS_TOKEN" not in root_child
    assert "WS_TOKEN" not in mtls_child
    assert "E2E_IDENTITY_SECRET" not in root_child
    assert "E2E_IDENTITY_SECRET" not in mtls_child
    assert inherited == {
        "PATH": os.environ.get("PATH", ""),
        "BUSINESS_REGION": "cn-east-long-lived",
        "AUTH_REQUIRED": "stale-parent-auth",
        "AUTH_TOKENS": "stale-parent-token",
        "PERMISSIONS_FAIL_OPEN": "stale-parent-permissions",
        "CLOUD_CHANNEL_TOKEN": "stale-parent-channel",
        "CLOUD_CHANNEL_TOKENS": "stale-parent-channels",
        "WS_TOKEN": "stale-parent-ws",
        "GRPC_TLS": "stale-parent-tls",
        "E2E_RUN_ID": "stale-parent-run",
        "E2E_USER_ID": "stale-parent-user",
        "E2E_IDENTITY_TOKEN": "stale-parent-identity",
        "E2E_PROVIDER": "stale-parent-provider",
        "E2E_FUTURE_NAMESPACE": "stale-parent-future",
    }

    rendered = json.dumps({
        "epochs": [epoch.to_dict() for epoch in epochs],
        "commands": [argv for argv, _env in calls],
    })
    assert "auth-random" not in rendered
    assert "channel-random" not in rendered
    assert "owner-secret" not in rendered
    assert module.ProfileCoordinator.RESTORE_LABEL == "default"


def test_real_s2s_profile_temporarily_enables_dashscope_and_restores_default(
    tmp_path: Path,
):
    selected = [_case("e2e_s2s", "real", signed_identity=True)]
    calls: list[tuple[list[str], dict[str, str]]] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: calls.append((list(argv), dict(env))),
        environ={
            "S2S_PROVIDER": "",
            "S2S_MODEL": "",
            "DASHSCOPE_ASR_KEY": "configured",
        },
    )

    active = coordinator.activate(coordinator.epochs[0])
    preflight = coordinator.preflight_entry(selected[0])
    coordinator.restore()

    assert preflight.ok is True
    assert active["S2S_PROVIDER"] == "dashscope"
    assert calls[0][1]["S2S_PROVIDER"] == "dashscope"
    assert calls[-1][1]["S2S_PROVIDER"] == ""


def test_s2s_entry_reasserts_runtime_after_an_earlier_case_recreated_gateway(
    tmp_path: Path,
):
    """Default-epoch fault tests may recreate llm-gateway from root env."""
    selected = [
        _case("e2e_degrade", "root", signed_identity=True),
        _case("e2e_s2s", "real", signed_identity=True),
    ]
    calls: list[tuple[list[str], dict[str, str]]] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: calls.append((list(argv), dict(env))),
        environ={
            "S2S_PROVIDER": "",
            "DASHSCOPE_ASR_KEY": "configured",
        },
    )

    coordinator.activate(coordinator.epochs[0])
    coordinator.prepare_entry_runtime(selected[1])

    command, runtime_env = calls[-1]
    assert command[-1] == "llm-gateway"
    assert runtime_env["S2S_PROVIDER"] == "dashscope"


def test_proactive_consumers_recreate_global_rate_window_at_case_boundary(
    tmp_path: Path,
):
    selected = [
        _case("e2e_journeys", "root", signed_identity=True),
        _case("e2e_memory", "root", signed_identity=True),
    ]
    calls: list[tuple[list[str], dict[str, str]]] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: calls.append((list(argv), dict(env))),
    )

    coordinator.activate(coordinator.epochs[0])
    coordinator.prepare_entry_runtime(selected[1])

    command, _ = calls[-1]
    assert command[-2:] == ["--force-recreate", "proactive"]


def test_mtls_missing_certificates_are_generated_before_compose_or_fail_preflight(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    compose_calls: list[Any] = []
    generator_calls: list[str] = []

    def generator():
        generator_calls.append("generate")
        _write_valid_mtls_chain(tmp_path)
        return True

    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_generator=generator,
    )
    coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == ["generate"]
    assert len(compose_calls) == 2

    failed_root = tmp_path / "failed"
    failed = _coordinator(
        failed_root,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_generator=lambda: False,
    )
    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        failed.activate(failed.epochs[0])
    failed.restore()
    assert len(compose_calls) == 2


def test_mtls_tracked_certificate_is_a_preflight_failure_without_compose(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    for cert in ("ca.crt", "server.crt", "server.key"):
        path = tmp_path / "certs" / cert
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    compose_calls: list[Any] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_tracked=lambda path: path.name == "server.key",
    )

    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert compose_calls == []


@pytest.mark.parametrize(
    "invalid_kind",
    ["zero", "malformed", "mismatch", "wrong_ca"],
)
def test_mtls_invalid_material_runs_generator_once_then_fails_closed_without_compose(
    tmp_path: Path,
    invalid_kind: str,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    cert_root = tmp_path / "certs"
    cert_root.mkdir(parents=True)
    if invalid_kind == "zero":
        for name in ("ca.crt", "server.crt", "server.key"):
            (cert_root / name).write_bytes(b"")
    elif invalid_kind == "malformed":
        for name in ("ca.crt", "server.crt", "server.key"):
            (cert_root / name).write_text("not a PEM", encoding="utf-8")
    elif invalid_kind == "mismatch":
        _write_valid_mtls_chain(tmp_path)
        wrong_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        (cert_root / "server.key").write_bytes(
            wrong_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ),
        )
    else:
        _write_valid_mtls_chain(tmp_path)
        other_root = tmp_path / "other"
        _write_valid_mtls_chain(other_root)
        (cert_root / "ca.crt").write_bytes(
            (other_root / "certs" / "ca.crt").read_bytes(),
        )
    generator_calls: list[str] = []
    compose_calls: list[Any] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_generator=lambda: generator_calls.append("generate") is None,
    )

    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == ["generate"]
    assert compose_calls == []


def test_mtls_symlinked_material_runs_generator_once_then_fails_closed(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    outside = tmp_path / "outside"
    _write_valid_mtls_chain(outside)
    cert_root = tmp_path / "certs"
    cert_root.mkdir()
    (cert_root / "ca.crt").write_bytes(
        (outside / "certs" / "ca.crt").read_bytes(),
    )
    (cert_root / "server.crt").write_bytes(
        (outside / "certs" / "server.crt").read_bytes(),
    )
    try:
        (cert_root / "server.key").symlink_to(
            outside / "certs" / "server.key",
        )
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    generator_calls: list[str] = []
    compose_calls: list[Any] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_generator=lambda: generator_calls.append("generate") is None,
    )

    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == ["generate"]
    assert compose_calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_mtls_junction_cert_directory_fails_after_one_generator(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    outside = tmp_path / "outside"
    _write_valid_mtls_chain(outside)
    cert_root = tmp_path / "certs"
    completed = subprocess.run(
        [
            "cmd",
            "/c",
            "mklink",
            "/J",
            str(cert_root),
            str(outside / "certs"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0 or not os.path.isjunction(cert_root):
        pytest.skip("junction creation unavailable")
    generator_calls: list[str] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda _argv, _env: pytest.fail("junction reached Compose"),
        cert_generator=lambda: generator_calls.append("generate") is None,
    )

    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == ["generate"]


def test_mtls_valid_chain_skips_generator_and_reaches_compose(
    tmp_path: Path,
):
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    _write_valid_mtls_chain(tmp_path)
    generator_calls: list[str] = []
    compose_calls: list[Any] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        cert_generator=lambda: generator_calls.append("generate") is None,
    )

    coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == []
    assert len(compose_calls) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX private-key mode contract")
def test_mtls_group_readable_private_key_fails_after_one_generator(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("mtls-a", "mtls", signed_identity=True)]
    _write_valid_mtls_chain(tmp_path)
    (tmp_path / "certs" / "server.key").chmod(0o640)
    generator_calls: list[str] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda _argv, _env: pytest.fail("invalid key reached Compose"),
        cert_generator=lambda: generator_calls.append("generate") is None,
    )

    with pytest.raises(module.ProfilePreflightError, match="mtls_certificates"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert generator_calls == ["generate"]


def test_profile_ready_probe_covers_auth_services_and_identity_mesh():
    module = _profiles()
    http_calls: list[str] = []
    tcp_calls: list[tuple[str, int]] = []

    ready = module.profile_services_ready(
        http_ready=lambda url: http_calls.append(url) is None,
        tcp_ready=lambda host, port: tcp_calls.append((host, port)) is None,
    )

    assert ready is True
    assert http_calls == [
        "http://127.0.0.1:8090/healthz",
        "http://127.0.0.1:50059/api/health",
    ]
    assert tcp_calls == [
        ("127.0.0.1", 50051),
        ("127.0.0.1", 50053),
        ("127.0.0.1", 50054),
        ("127.0.0.1", 50070),
        ("127.0.0.1", 8080),
    ]


def test_unsigned_real_epoch_must_probe_ready_and_false_is_profile_start_failure(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("e2e_real_providers", "real")]
    ready_calls: list[str] = []
    compose_calls: list[Any] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda argv, env: compose_calls.append((argv, env)),
        ready=lambda: ready_calls.append("ready") is not None,
    )

    with pytest.raises(module.ProfileStartError, match="profile_start"):
        coordinator.activate(coordinator.epochs[0])
    coordinator.restore()

    assert ready_calls == ["ready"]
    assert compose_calls == []


def test_unsigned_real_epoch_ready_true_authorizes_child_and_merged_root_real_probes_once(
    tmp_path: Path,
):
    module = _profiles()
    selected = [
        _case("root-a", "root"),
        _case("e2e_real_providers", "real"),
    ]
    ready_calls: list[str] = []
    coordinator = _coordinator(
        tmp_path,
        selected,
        compose=lambda _argv, _env: pytest.fail(
            "unsigned default epoch must not rebuild Compose",
        ),
        ready=lambda: ready_calls.append("ready") is None,
    )

    coordinator.activate(coordinator.epochs[0])
    assert coordinator.child_environment(selected[1]) == {}
    coordinator.restore()

    assert ready_calls == ["ready"]


def test_real_provider_preflight_contract_lists_only_missing_credential_families(
    tmp_path: Path,
):
    module = _profiles()
    case = _case("e2e_real_providers", "real")
    coordinator = _coordinator(
        tmp_path,
        [case],
        compose=lambda _argv, _env: None,
        environ={},
    )
    coordinator.activate(coordinator.epochs[0])

    result = coordinator.preflight_entry(case)
    coordinator.restore()

    assert result.ok is False
    assert result.error == "credential_preflight"
    assert result.missing == (
        "amap",
        "qweather",
        "search",
        "news",
        "stock",
    )
    rendered = result.diagnostic()
    assert rendered == (
        "credential preflight failed; missing families: "
        "amap,qweather,search,news,stock"
    )
    assert "KEY" not in rendered
    assert "TOKEN" not in rendered


def test_each_real_entry_dispatches_its_own_hook_and_untrusted_detail_is_not_rendered(
    tmp_path: Path,
):
    module = _profiles()
    cases = [
        _case("real-a", "real"),
        _case("real-b", "real"),
    ]
    calls: list[str] = []

    def fail(case, _configured):
        calls.append(case.id)
        return module.EntryPreflightResult(
            ok=False,
            error="secret-value-must-not-render",
            missing=("secret-value-must-not-render",),
        )

    def pass_(case, _configured):
        calls.append(case.id)
        return module.EntryPreflightResult(ok=True)

    coordinator = _coordinator(
        tmp_path,
        cases,
        compose=lambda _argv, _env: None,
        entry_preflights={"real-a": fail, "real-b": pass_},
    )
    coordinator.activate(coordinator.epochs[0])
    first = coordinator.preflight_entry(cases[0])
    second = coordinator.preflight_entry(cases[1])
    coordinator.restore()

    assert calls == ["real-a", "real-b"]
    assert first.error == "entry_preflight"
    assert first.diagnostic() == "entry preflight failed"
    assert second.ok is True


def test_entry_preflight_boundary_rejects_unhashable_malicious_and_unbounded_fields(
    tmp_path: Path,
):
    module = _profiles()
    case = _case("real-a", "real")

    class Malicious:
        def __hash__(self):
            raise AssertionError("malicious hash executed")

        def __str__(self):
            raise AssertionError("malicious str executed")

    invalid = (
        module.EntryPreflightResult(
            ok=False,
            error="credential_preflight",
            missing=({"secret": "value"},),
        ),
        module.EntryPreflightResult(
            ok=False,
            error=Malicious(),
            missing=(),
        ),
        module.EntryPreflightResult(
            ok=False,
            error="credential_preflight",
            missing=(Malicious(),),
        ),
        module.EntryPreflightResult(
            ok=False,
            error="credential_preflight",
            missing=("a" * 80,),
        ),
        module.EntryPreflightResult(
            ok=False,
            error="credential_preflight",
            missing=("amap",) * 20,
        ),
    )

    for candidate in invalid:
        coordinator = _coordinator(
            tmp_path,
            [case],
            compose=lambda _argv, _env: None,
            entry_preflights={
                case.id: lambda _case, _configured, value=candidate: value,
            },
        )
        coordinator.activate(coordinator.epochs[0])

        normalized = coordinator.preflight_entry(case)
        direct_diagnostic = candidate.diagnostic()
        coordinator.restore()

        assert normalized == module.EntryPreflightResult(
            ok=False,
            error="entry_preflight",
        )
        assert normalized.diagnostic() == "entry preflight failed"
        assert direct_diagnostic == "entry preflight failed"


def test_real_provider_preflight_accepts_legacy_qweather_and_bing_alternatives(
    tmp_path: Path,
):
    module = _profiles()
    case = _case("e2e_real_providers", "real")
    coordinator = _coordinator(
        tmp_path,
        [case],
        compose=lambda _argv, _env: None,
        environ={
            "AMAP_KEY": "configured",
            "QWEATHER_KEY": "configured",
            "BING_SEARCH_KEY": "configured",
            "SERPAPI_API_KEY": "configured",
            "TUSHARE_TOKEN": "configured",
        },
    )
    coordinator.activate(coordinator.epochs[0])

    result = coordinator.preflight_entry(case)
    coordinator.restore()

    assert result == module.EntryPreflightResult(ok=True)


def test_epoch_start_failure_does_not_authorize_child_and_restore_failure_is_distinct(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("auth-a", "auth")]
    child_calls: list[str] = []

    def failing_compose(_argv, _env):
        raise RuntimeError("compose failed with auth-random")

    coordinator = _coordinator(tmp_path, selected, compose=failing_compose)
    with pytest.raises(module.ProfileStartError, match="profile_start"):
        coordinator.activate(coordinator.epochs[0])
    assert child_calls == []

    with pytest.raises(module.ProfileRestoreError, match="profile_restore"):
        coordinator.restore()


def test_profile_restore_failure_keeps_lease_owned_and_can_retry(
    tmp_path: Path,
):
    module = _profiles()
    selected = [_case("auth-a", "auth")]
    compose_calls = 0
    lease_holder: list[Any] = []

    class RetryLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.lease_id = "lease-profile-retry"
            self.secret = b"x" * 32
            self.restore_calls = 0
            lease_holder.append(self)

        def enable(self):
            self.environ.update({
                "E2E_IDENTITY_ENABLED": "true",
                "E2E_IDENTITY_SECRET": "owner-secret",
            })

        def restore(self):
            self.restore_calls += 1
            self.secret = b""

    def compose(_argv, _env):
        nonlocal compose_calls
        compose_calls += 1
        if compose_calls == 2:
            raise RuntimeError("first default restore failed")

    tokens = iter(("auth-token", "channel-token"))
    coordinator = module.ProfileCoordinator(
        repo_root=tmp_path,
        environ={},
        run_id="e2e-profile-retry",
        selected=selected,
        compose=compose,
        ready=lambda: True,
        token_factory=lambda: next(tokens),
        lease_factory=RetryLease,
    )
    coordinator.activate(coordinator.epochs[0])

    with pytest.raises(module.ProfileRestoreError, match="profile_restore"):
        coordinator.restore()
    assert coordinator._restored is False
    assert lease_holder[0].restore_calls == 0
    assert lease_holder[0].secret == b"x" * 32

    coordinator.restore()
    assert coordinator._restored is True
    assert lease_holder[0].restore_calls == 1
    assert lease_holder[0].secret == b""
    assert compose_calls == 3


def test_runner_retries_profile_restore_once():
    runner = _runner()

    class Coordinator:
        calls = 0

        def restore(self):
            self.calls += 1
            if self.calls == 1:
                raise runner.ProfileRestoreError("profile_restore")

    coordinator = Coordinator()
    assert runner._restore_profile_coordinator(coordinator) is True
    assert coordinator.calls == 2


def test_profile_command_runner_discards_four_mib_output_without_deadlock(
    tmp_path: Path,
):
    runner = _runner()
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x' * (4 * 1024 * 1024)); "
        "sys.stderr.write('y' * (4 * 1024 * 1024))",
    ]

    returncode = runner._run_profile_command(
        command,
        cwd=tmp_path,
        environ=os.environ,
        timeout_s=15,
    )

    assert returncode == 0


def test_profile_command_timeout_reaps_grandchild_and_never_leaks_secret(
    tmp_path: Path,
):
    runner = _runner()
    pid_file = tmp_path / "profile-grandchild.pid"
    secret = "profile-command-secret-must-not-render"  # release-secret-fixture
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid),encoding='utf-8'); "
        "print('z' * (4 * 1024 * 1024)); time.sleep(60)"
    )
    probe = _PidProbe(pid_file)

    with pytest.raises(RuntimeError, match="profile_command_failed") as caught:
        runner._run_profile_command(
            [sys.executable, "-c", parent_code, secret],
            cwd=tmp_path,
            environ={**os.environ, "PROFILE_TEST_SECRET": secret},
            timeout_s=1,
        )

    assert secret not in str(caught.value)
    probe.assert_exited()


@pytest.mark.parametrize(
    ("wait_error", "expected"),
    [
        (RuntimeError("internal-secret-detail"), RuntimeError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_profile_command_exception_or_interrupt_always_terminates_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wait_error: BaseException,
    expected: type[BaseException],
):
    runner = _runner()
    events: list[str] = []
    popen_kwargs: dict[str, Any] = {}

    class Process:
        pid = 424242
        returncode = None

        def wait(self, timeout=None):
            if self.returncode is None:
                raise wait_error
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = Process()

    class Tree:
        available = True

        def attach(self, _process):
            return True

        def resume(self, _process):
            return True

        def terminate(self, target):
            events.append("terminate")
            target.kill()

        def close(self):
            events.append("close")

    def popen(*_args, **kwargs):
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(runner, "_ProcessTree", Tree)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)

    with pytest.raises(expected) as caught:
        runner._run_profile_command(
            ["profile-command"],
            cwd=tmp_path,
            environ={"PROFILE_SECRET": "internal-secret-detail"},
            timeout_s=1,
        )

    assert events == ["terminate", "close"]
    assert popen_kwargs["stdout"] is subprocess.DEVNULL
    assert popen_kwargs["stderr"] is subprocess.DEVNULL
    if expected is RuntimeError:
        assert str(caught.value) == "profile_command_failed"


def test_runner_dry_run_exposes_epoch_order_entry_profiles_and_one_restore_without_secrets():
    runner = _runner()
    output = io.StringIO()

    rc = runner.main(
        [
            "--target", "local",
            "--milestone", "M-A", "--lane", "milestone", "--full", "--dry-run",
        ],
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST_PATH,
        environ={
            "AUTH_TOKENS": "must-not-appear-auth-token",
            "CLOUD_CHANNEL_TOKEN": "must-not-appear-channel-token",
            "E2E_IDENTITY_SECRET": "must-not-appear-owner-secret",
        },
        stdout=output,
        staleness_evaluator=lambda _root: {"stale": False, "reasons": []},
    )

    text = output.getvalue()
    summary = json.loads(text.splitlines()[-1])
    assert rc == 0
    assert [epoch["name"] for epoch in summary["epochs"]] == [
        "default",
        "auth",
        "mtls",
    ]
    assert summary["profile_restore"] == {"count": 1, "profile": "default"}
    assert all(
        item["profile"] in epoch["profiles"]
        for epoch in summary["epochs"]
        for item in epoch["entries"]
    )
    assert "must-not-appear-auth-token" not in text
    assert "must-not-appear-channel-token" not in text
    assert "must-not-appear-owner-secret" not in text
    assert "AUTH_TOKENS" not in text
    assert "CLOUD_CHANNEL_TOKEN" not in text


def test_runner_profile_auth_selects_only_auth_manifest_entry():
    runner = _runner()
    output = io.StringIO()

    rc = runner.main(
        [
            "--target", "local",
            "--profile", "auth", "--dry-run",
        ],
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST_PATH,
        environ={},
        stdout=output,
        staleness_evaluator=lambda _root: {"stale": False, "reasons": []},
    )

    summary = json.loads(output.getvalue().splitlines()[-1])
    assert rc == 0
    assert [item["id"] for item in summary["selection"]] == ["e2e_auth"]
    assert summary["epochs"] == [{
        "name": "auth",
        "profiles": ["auth"],
        "entries": [{"id": "e2e_auth", "profile": "auth"}],
        "restore": False,
    }]


def _child_env(
    tmp_path: Path,
    test_id: str,
    *,
    token_name: str,
    token: str,
) -> dict[str, str]:
    run_id = "e2e-profile-child"
    user_id = f"{run_id}-{test_id}"
    env = {
        "E2E_RUN_ID": run_id,
        "E2E_TEST_ID": test_id,
        "E2E_USER_ID": user_id,
        "E2E_SESSION_PREFIX": f"{user_id}-session",
        "E2E_RESULT_FILE": str(tmp_path / f"{test_id}-result.json"),
        "E2E_ARTIFACT_DIR": str(tmp_path / f"{test_id}-artifacts"),
        "E2E_LANE": "milestone",
        "E2E_PROFILE": "auth" if test_id == "e2e_auth" else "mtls",
        "WS_URL": "ws://127.0.0.1:8090/ws",
        token_name: token,
    }
    if test_id == "e2e_mtls":
        env["E2E_EXPECTED_VEHICLE_ID"] = "v1"
    return env


class _FakeWebSocket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = list(messages)
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        return json.dumps(self.messages.pop(0))


class _RejectedConnection(Exception):
    class _Response:
        status_code = 401

    response = _Response()

    async def __aenter__(self):
        raise self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


def test_e2e_auth_writes_result_protocol_and_uses_runner_user_sessions_and_only_child_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    token = "ordinary-random-auth-token"  # release-secret-fixture
    env = _child_env(
        tmp_path,
        "e2e_auth",
        token_name="WS_TOKEN",
        token=token,
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module = _load(REPO_ROOT / "test" / "e2e_auth.py", "task5b_e2e_auth")
    urls: list[str] = []
    sockets: list[_FakeWebSocket] = []

    def connect(url: str, **_kwargs):
        urls.append(url)
        if "token=" not in url:
            return _RejectedConnection()
        messages = (
            [{"type": "final", "speech": "空调已打开", "actions": [{"ok": True}]}]
            if not sockets
            else [{"type": "final", "speech": "找到附近充电站"}]
        )
        ws = _FakeWebSocket(messages)
        sockets.append(ws)
        return ws

    monkeypatch.setattr(module.websockets, "connect", connect)

    rc = asyncio.run(module.main())

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["status"] == "pass"
    assert payload["counts"] == {
        "selected": 3,
        "executed": 3,
        "passed": 3,
        "failed": 0,
        "skipped": 0,
    }
    assert token not in json.dumps(payload)
    assert urls[0] == env["WS_URL"]
    assert all(token in url for url in urls[1:])
    assert [ws.sent[0]["session_id"] for ws in sockets] == [
        f"{env['E2E_SESSION_PREFIX']}-1",
        f"{env['E2E_SESSION_PREFIX']}-2",
    ]


def test_e2e_mtls_writes_result_protocol_and_uses_signed_identity_ack_and_synthetic_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    token = "e2e.v1.payload.signature"  # release-secret-fixture
    env = _child_env(
        tmp_path,
        "e2e_mtls",
        token_name="E2E_IDENTITY_TOKEN",
        token=token,
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module = _load(REPO_ROOT / "test" / "e2e_mtls.py", "task5b_e2e_mtls")
    ws = _FakeWebSocket([
        {
            "type": "e2e_identity_ack",
            "run_id": env["E2E_RUN_ID"],
            "user_id": env["E2E_USER_ID"],
            "vehicle_id": "v1",
        },
        {"type": "final", "speech": "这是一个笑话"},
    ])
    urls: list[str] = []

    def connect(url: str, **_kwargs):
        urls.append(url)
        return ws

    class _Future:
        def result(self, *, timeout: int):
            assert timeout == 6
            raise module.grpc.FutureTimeoutError()

    class _Channel:
        def close(self):
            pass

    monkeypatch.setattr(module.websockets, "connect", connect)
    monkeypatch.setattr(module.grpc, "insecure_channel", lambda _target: _Channel())
    monkeypatch.setattr(module.grpc, "channel_ready_future", lambda _channel: _Future())

    rc = asyncio.run(module.main())

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["status"] == "pass"
    assert payload["counts"] == {
        "selected": 2,
        "executed": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
    }
    assert token not in json.dumps(payload)
    assert token in urls[0]
    assert ws.sent == [{
        "text": "讲个笑话",
        "session_id": f"{env['E2E_SESSION_PREFIX']}-1",
    }]


def _runner_result(case: Any, *, failed: bool = False) -> dict[str, Any]:
    return {
        "id": case.id,
        "status": "FAIL" if failed else "PASS",
        "returncode": 1 if failed else 0,
        "errors": ["child_failed"] if failed else [],
        "counts": {
            "selected": 1,
            "executed": 1,
            "passed": 0 if failed else 1,
            "failed": 1 if failed else 0,
            "skipped": 0,
        },
        "artifact_dir": "",
        "artifacts": [],
        "logs": [],
        "diagnostic": "",
        "result_file": "",
        "profile": case.profile,
        "timeout_s": case.timeout_s,
    }


def _run_real_profile_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selected: list[SimpleNamespace],
    *,
    environ: dict[str, str],
    ready: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    runner = _runner()
    events: list[str] = []
    base_coordinator = runner.ProfileCoordinator

    class TestCoordinator(base_coordinator):
        def __init__(self, **kwargs):
            def lease_factory(*, environ, **_lease_kwargs):
                return _FakeLease(environ=environ, events=events)

            def is_ready():
                events.append("ready")
                return ready

            kwargs.pop("lease_factory", None)
            kwargs.pop("compose", None)
            kwargs.pop("cert_generator", None)
            super().__init__(
                **kwargs,
                compose=lambda _argv, _env: events.append("compose"),
                ready=is_ready,
                lease_factory=lease_factory,
            )

        def preflight_entry(self, case):
            events.append(f"preflight:{case.id}")
            return super().preflight_entry(case)

    def fake_child(case, **_kwargs):
        events.append(f"child:{case.id}")
        return _runner_result(case)

    monkeypatch.setattr(runner, "ProfileCoordinator", TestCoordinator)
    monkeypatch.setattr(runner, "_run_child", fake_child)
    results, errors = runner._run_profile_epochs(
        selected,
        repo_root=tmp_path,
        run_root=tmp_path / "run",
        run_id="e2e-real-preflight",
        lane="milestone",
        full=False,
        provider=None,
        model=None,
        source_env=environ,
    )
    assert errors == []
    return results, events


def test_real_provider_empty_credentials_returns_preflight_failure_and_zero_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _case("e2e_real_providers", "real")

    results, events = _run_real_profile_entries(
        monkeypatch,
        tmp_path,
        [case],
        environ={},
    )

    assert events == [
        "lease:enable",
        "ready",
        "preflight:e2e_real_providers",
        "lease:restore",
    ]
    assert results[0]["errors"] == ["credential_preflight"]
    assert results[0]["diagnostic"] == (
        "credential preflight failed; missing families: "
        "amap,qweather,search,news,stock"
    )


def test_real_provider_complete_credentials_runs_child_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _case("e2e_real_providers", "real")

    results, events = _run_real_profile_entries(
        monkeypatch,
        tmp_path,
        [case],
        environ={
            "AMAP_KEY": "amap-secret",
            "QWEATHER_PROJECT_ID": "project-secret",
            "QWEATHER_KEY_ID": "key-id-secret",
            "QWEATHER_PRIVATE_KEY": "private-secret",
            "ANYSEARCH_API_KEY": "search-secret",
            "SERPAPI_API_KEY": "news-secret",
            "TUSHARE_TOKEN": "stock-secret",
        },
    )

    assert events.count("child:e2e_real_providers") == 1
    assert results[0]["errors"] == []
    assert not any(
        "secret" in json.dumps(result)
        for result in results
    )


def test_one_real_entry_preflight_failure_does_not_block_next_real_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected = [
        _case("e2e_real_providers", "real"),
        _case("e2e_tts_stream", "real"),
    ]

    results, events = _run_real_profile_entries(
        monkeypatch,
        tmp_path,
        selected,
        environ={},
    )

    assert events == [
        "lease:enable",
        "ready",
        "preflight:e2e_real_providers",
        "preflight:e2e_tts_stream",
        "child:e2e_tts_stream",
        "lease:restore",
    ]
    assert [result["errors"] for result in results] == [
        ["credential_preflight"],
        [],
    ]


def test_unsigned_real_ready_failure_runs_zero_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _case("e2e_tts_stream", "real")

    results, events = _run_real_profile_entries(
        monkeypatch,
        tmp_path,
        [case],
        environ={},
        ready=False,
    )

    assert events == ["lease:enable", "ready", "lease:restore"]
    assert results[0]["errors"] == ["profile_start"]


def _install_profile_runner_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_start: str = "",
    fail_restore: bool = False,
):
    runner = _runner()
    events: list[str] = []
    child_envs: dict[str, dict[str, str]] = {}
    fixture_required_by_case: dict[str, bool] = {}
    pending_fixture_requirements: list[bool] = []

    class FakeLease:
        lease_id = "lease-profile-runner"
        secret = b"x" * 32

    class FakeCoordinator:
        def __init__(self, *, selected, **_kwargs):
            self.epochs = runner.plan_profile_epochs(selected)
            self.lease = FakeLease()
            self.active = None
            fixture_required_by_case.update({
                case.id: case.fixture_pre_step is not None
                for case in selected
            })

        def activate(self, epoch):
            events.append(f"activate:{epoch.name}")
            if epoch.name == fail_start:
                raise runner.ProfileEnableError("profile_enable")
            self.active = epoch
            return {}

        def prepare_entry_runtime(self, case):
            assert self.active is not None and case in self.active.cases

        def child_environment(self, case):
            assert self.active is not None and case in self.active.cases
            env = {"SAFE_PARENT": "present"}
            if case.profile == "auth":
                env["WS_TOKEN"] = "auth-child-only-secret"
            return env

        def restore(self):
            events.append("restore")
            if fail_restore:
                raise runner.ProfileRestoreError("profile_restore")

    def fake_bundle(*, case_id, **_kwargs):
        pending_fixture_requirements.append(
            fixture_required_by_case[case_id],
        )
        return tmp_path / "opaque-bundle.json"

    def fake_load(
        _path,
        *,
        bundle_root,
        lease_id,
        inherited,
        fixture_required,
    ):
        assert lease_id == "lease-profile-runner"
        assert bundle_root.name == "lease-bundles"
        assert fixture_required is pending_fixture_requirements.pop(0)
        return {
            **inherited,
            "E2E_IDENTITY_TOKEN": "e2e.v1.opaque.signature",
            "E2E_STACK_LEASE_ROLE": "child",
            "E2E_STACK_LEASE_ID": lease_id,
        }

    def fake_child(case, *, environ, **_kwargs):
        events.append(f"child:{case.id}")
        child_envs[case.id] = dict(environ)
        return _runner_result(case, failed=case.id == "e2e_auth")

    monkeypatch.setattr(runner, "ProfileCoordinator", FakeCoordinator)
    monkeypatch.setattr(runner, "write_token_bundle", fake_bundle)
    monkeypatch.setattr(
        runner,
        "_presign_memory_bundle",
        lambda path, **_kwargs: path,
    )
    monkeypatch.setattr(runner, "load_child_bundle", fake_load)
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_run_child", fake_child)
    return runner, events, child_envs


def _invoke_profile_runner(runner: ModuleType) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    rc = runner.main(
        [
            "--target", "local",
            "--id",
            "e2e_protocol_smoke",
            "--id",
            "e2e_auth",
            "--id",
            "e2e_mtls",
        ],
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST_PATH,
        environ={"VEHICLE_ID": "v1"},
        stdout=output,
        staleness_evaluator=lambda _root: {"stale": False, "reasons": []},
    )
    return rc, json.loads(output.getvalue().splitlines()[-1])


def test_runner_profile_epochs_are_serial_child_failure_still_runs_later_epoch_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner, events, child_envs = _install_profile_runner_fakes(
        monkeypatch,
        tmp_path,
    )

    rc, summary = _invoke_profile_runner(runner)

    assert rc == 1
    assert events == [
        "activate:default",
        "child:e2e_protocol_smoke",
        "activate:auth",
        "child:e2e_auth",
        "activate:mtls",
        "child:e2e_mtls",
        "restore",
    ]
    assert "WS_TOKEN" not in child_envs["e2e_protocol_smoke"]
    assert child_envs["e2e_auth"]["WS_TOKEN"] == "auth-child-only-secret"
    assert "WS_TOKEN" not in child_envs["e2e_mtls"]
    assert [item["status"] for item in summary["results"]] == [
        "PASS",
        "FAIL",
        "PASS",
    ]


def test_runner_epoch_start_failure_runs_no_epoch_or_later_children_and_still_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner, events, _child_envs = _install_profile_runner_fakes(
        monkeypatch,
        tmp_path,
        fail_start="auth",
    )

    rc, summary = _invoke_profile_runner(runner)

    assert rc == 1
    assert events == [
        "activate:default",
        "child:e2e_protocol_smoke",
        "activate:auth",
        "restore",
    ]
    assert [item["errors"] for item in summary["results"]] == [
        [],
        ["profile_enable"],
        ["profile_enable"],
    ]


def test_runner_profile_restore_failure_overrides_pass_and_child_failure_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner, events, _child_envs = _install_profile_runner_fakes(
        monkeypatch,
        tmp_path,
        fail_restore=True,
    )

    rc, summary = _invoke_profile_runner(runner)

    assert rc == 1
    assert events[-1] == "restore"
    assert summary["errors"] == ["profile_restore"]
    assert all(item["status"] == "FAIL" for item in summary["results"])
    assert all(
        "profile_restore" in item["errors"]
        for item in summary["results"]
    )


def test_root_only_signed_run_keeps_task5a_identity_lease_and_never_uses_profile_coordinator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _runner()
    events: list[str] = []

    class ForbiddenCoordinator:
        def __init__(self, **_kwargs):
            pytest.fail("root-only signed run entered ProfileCoordinator")

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.lease_id = "lease-root-only"
            self.secret = b"x" * 32

        def enable(self):
            events.append("lease:enable")
            self.environ["E2E_IDENTITY_SECRET"] = "owner-only"

        def restore(self):
            events.append("lease:restore")
            self.environ.pop("E2E_IDENTITY_SECRET", None)
            self.secret = b""

    monkeypatch.setattr(runner, "ProfileCoordinator", ForbiddenCoordinator)
    monkeypatch.setattr(runner, "IdentityStackLease", FakeLease)
    monkeypatch.setattr(
        runner,
        "write_token_bundle",
        lambda **_kwargs: tmp_path / "opaque-bundle.json",
    )
    monkeypatch.setattr(
        runner,
        "_presign_memory_bundle",
        lambda path, **_kwargs: path,
    )

    def fake_load(
        _path,
        *,
        bundle_root,
        lease_id,
        inherited,
        fixture_required,
    ):
        assert bundle_root.name == "lease-bundles"
        assert fixture_required is False
        return {
            **inherited,
            "E2E_IDENTITY_TOKEN": "e2e.v1.opaque.signature",
            "E2E_STACK_LEASE_ROLE": "child",
            "E2E_STACK_LEASE_ID": lease_id,
        }

    monkeypatch.setattr(
        runner,
        "load_child_bundle",
        fake_load,
    )
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)

    def fake_child(case, **_kwargs):
        events.append(f"child:{case.id}")
        return _runner_result(case)

    monkeypatch.setattr(runner, "_run_child", fake_child)
    output = io.StringIO()
    rc = runner.main(
        [
            "--target", "local",
            "--id", "e2e_memory",
        ],
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST_PATH,
        environ={"VEHICLE_ID": "v1"},
        stdout=output,
        staleness_evaluator=lambda _root: {"stale": False, "reasons": []},
    )

    assert rc == 0
    assert events == [
        "lease:enable",
        "child:e2e_memory",
        "lease:restore",
    ]

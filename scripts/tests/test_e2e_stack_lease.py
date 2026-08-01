from __future__ import annotations

import hashlib
import json
import importlib.util
import inspect
import io
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

try:
    import scripts.e2e_stack_lease as lease_module
except ModuleNotFoundError:
    lease_module = None


class FakeCompose:
    def __init__(self, *, fail_restore: bool = False):
        self.calls: list[dict[str, str]] = []
        self.fail_restore = fail_restore

    def __call__(self, environ):
        snapshot = dict(environ)
        self.calls.append(snapshot)
        if (
            self.fail_restore
            and snapshot.get("E2E_IDENTITY_ENABLED") == "false"
        ):
            raise RuntimeError("restore failed")


class FakeChild:
    def __init__(self, rc=0, *, crash=False):
        self.rc = rc
        self.crash = crash
        self.waited = False

    def wait(self):
        self.waited = True
        if self.crash:
            raise RuntimeError("child vanished")
        return self.rc


def load_runner(name: str):
    runner_path = Path(__file__).resolve().parents[1] / "run_e2e.py"
    spec = importlib.util.spec_from_file_location(name, runner_path)
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    return runner


def require_module():
    assert lease_module is not None, "identity stack lease module is missing"
    return lease_module


def _voiceprint_fixture(run_root: Path, case_id: str = "e2e_voiceprint"):
    fixture_dir = run_root / case_id / "artifacts" / "voiceprint-fixtures"
    fixture_dir.mkdir(parents=True)
    manifest = fixture_dir / "voiceprint-fixtures.json"
    content = b'{"owner":"runner"}'
    manifest.write_bytes(content)
    return fixture_dir, manifest, hashlib.sha256(content).hexdigest()


def test_single_owner_enables_and_restores_exactly_once_without_leaking_secret():
    module = require_module()
    compose = FakeCompose()
    process_env = {"KEEP": "yes"}
    lease = module.IdentityStackLease(
        repo_root=Path.cwd(),
        environ=process_env,
        compose=compose,
        ready=lambda: True,
        secret_factory=lambda: b"x" * 32,
        lease_id_factory=lambda: "lease-opaque",
    )
    lease.enable()
    assert len(compose.calls) == 1
    assert compose.calls[0]["E2E_IDENTITY_ENABLED"] == "true"
    assert compose.calls[0]["E2E_IDENTITY_SECRET"]
    assert process_env["E2E_IDENTITY_SECRET"]

    lease.restore()
    lease.restore()
    assert len(compose.calls) == 2
    assert compose.calls[1]["E2E_IDENTITY_ENABLED"] == "false"
    assert "E2E_IDENTITY_SECRET" not in compose.calls[1]
    assert "E2E_IDENTITY_SECRET" not in process_env
    assert "E2E_IDENTITY_ENABLED" not in process_env


def test_identity_enable_retries_one_transient_readiness_failure_before_restore():
    module = require_module()
    compose = FakeCompose()
    readiness = iter((False, True, True))
    process_env = {"KEEP": "yes"}
    lease = module.IdentityStackLease(
        repo_root=Path.cwd(),
        environ=process_env,
        compose=compose,
        ready=lambda: next(readiness),
        secret_factory=lambda: b"x" * 32,
        lease_id_factory=lambda: "lease-readiness-retry",
    )

    lease.enable()

    assert len(compose.calls) == 2
    assert all(
        call["E2E_IDENTITY_ENABLED"] == "true"
        for call in compose.calls
    )
    assert (
        compose.calls[0]["E2E_IDENTITY_SECRET"]
        == compose.calls[1]["E2E_IDENTITY_SECRET"]
    )
    lease.restore()
    assert len(compose.calls) == 3
    assert compose.calls[-1]["E2E_IDENTITY_ENABLED"] == "false"


def test_optional_namespace_admin_uses_same_secret_and_restores_extra_service(
    tmp_path,
    monkeypatch,
):
    module = require_module()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs["env"])))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    process_env = {}
    lease = module.IdentityStackLease(
        repo_root=tmp_path,
        environ=process_env,
        ready=lambda: True,
        secret_factory=lambda: bytes(range(32)),
        lease_id_factory=lambda: "lease-admin",
        extra_services=("proactive",),
    )

    lease.enable()
    enable_command, enable_env = calls[0]
    assert enable_command[-1] == "proactive"
    assert enable_env["E2E_NAMESPACE_ADMIN_ENABLED"] == "true"
    assert enable_env["E2E_NAMESPACE_ADMIN_SECRET"] == (
        enable_env["E2E_IDENTITY_SECRET"]
    )

    lease.restore()
    restore_command, restore_env = calls[1]
    assert restore_command[-1] == "proactive"
    assert restore_env["E2E_NAMESPACE_ADMIN_ENABLED"] == "false"
    assert "E2E_NAMESPACE_ADMIN_SECRET" not in restore_env
    assert "E2E_NAMESPACE_ADMIN_ENABLED" not in process_env
    assert "E2E_NAMESPACE_ADMIN_SECRET" not in process_env


def test_private_bundle_contains_tokens_and_namespace_but_never_secret(tmp_path):
    module = require_module()
    bundle_path = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-opaque",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=300,
        secret=b"x" * 32,
        now=1700000000,
        memory_sessions=1,
    )
    assert bundle_path.is_absolute()
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert payload["lease_id"] == "lease-opaque"
    assert payload["identity_token"].startswith("e2e.v1.")
    assert payload["control_identity_token"].startswith("e2e.v1.")
    assert payload["control_user_id"].endswith("-control")
    assert payload["memory_session_ids"]
    assert "secret" not in json.dumps(payload).lower()
    payload["memory_session_ids"] = ["e2e-mem.v1.payload.signature"]
    bundle_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    child_env = module.load_child_bundle(
        bundle_path,
        bundle_root=tmp_path,
        lease_id="lease-opaque",
        inherited={"KEEP": "yes"},
    )
    assert child_env["E2E_STACK_LEASE_ROLE"] == "child"
    assert child_env["E2E_IDENTITY_TOKEN"] == payload["identity_token"]
    assert all("SECRET" not in key for key in child_env)


def test_default_signed_identity_can_reach_all_manifest_agents(tmp_path):
    module = require_module()
    from scripts.e2e_identity import verify_identity

    secret = b"x" * 32
    bundle_path = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-profile",
        case_id="e2e_geofence",
        run_id="e2e-run-profile",
        user_id="e2e-run-profile-e2e_geofence",
        vehicle_id="v1",
        timeout_s=300,
        secret=secret,
        now=1700000000,
        memory_sessions=0,
    )
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    claims = verify_identity(
        payload["identity_token"],
        secret,
        now=1700000001,
    )

    required_scopes = set()
    for manifest_path in (
        Path(__file__).resolve().parents[2] / "agents"
    ).glob("*/manifest.yaml"):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        required_scopes.update(manifest.get("requires_permissions") or [])

    assert required_scopes <= set(claims.scopes)


def test_fixture_attestation_overrides_inherited_paths_and_loads_exact_owner_path(
    tmp_path,
):
    module = require_module()
    bundle_root = tmp_path / "lease-bundles"
    fixture_dir, manifest, digest = _voiceprint_fixture(tmp_path)
    bundle = module.write_token_bundle(
        root=bundle_root,
        lease_id="lease-fixture",
        case_id="e2e_voiceprint",
        run_id="e2e-run-fixture",
        user_id="e2e-run-fixture-e2e_voiceprint",
        vehicle_id="v1",
        timeout_s=300,
        secret=b"x" * 32,
        memory_sessions=0,
        fixture_dir=fixture_dir,
        fixture_manifest=manifest,
        fixture_manifest_sha256=digest,
        fixture_audio_api_origin="https://audio.example.test:5443",
    )
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    bundle.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    child_env = module.load_child_bundle(
        bundle,
        bundle_root=bundle_root,
        lease_id="lease-fixture",
        fixture_required=True,
        inherited={
            "E2E_VOICEPRINT_FIXTURE_DIR": str(tmp_path / "attacker"),
            "E2E_VOICEPRINT_FIXTURE_MANIFEST": str(
                tmp_path / "attacker.json"
            ),
            "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": "0" * 64,
            "E2E_AUDIO_API_ORIGIN": "http://attacker.invalid",
        },
    )

    assert child_env["E2E_VOICEPRINT_FIXTURE_DIR"] == str(
        fixture_dir.resolve(),
    )
    assert child_env["E2E_VOICEPRINT_FIXTURE_MANIFEST"] == str(
        manifest.resolve(),
    )
    assert child_env["E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256"] == digest
    assert child_env["E2E_AUDIO_API_ORIGIN"] == (
        "https://audio.example.test:5443"
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "null", "external", "bad_name", "bad_hash", "bad_origin"],
)
def test_fixture_bundle_tampering_fails_closed(tmp_path, mutation):
    module = require_module()
    bundle_root = tmp_path / "lease-bundles"
    fixture_dir, manifest, digest = _voiceprint_fixture(tmp_path)
    bundle = module.write_token_bundle(
        root=bundle_root,
        lease_id="lease-fixture",
        case_id="e2e_voiceprint",
        run_id="e2e-run-fixture",
        user_id="e2e-run-fixture-e2e_voiceprint",
        vehicle_id="v1",
        timeout_s=300,
        secret=b"x" * 32,
        memory_sessions=0,
        fixture_dir=fixture_dir,
        fixture_manifest=manifest,
        fixture_manifest_sha256=digest,
        fixture_audio_api_origin="https://audio.example.test:5443",
    )
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    if mutation == "missing":
        del payload["voiceprint_fixture"]
    elif mutation == "null":
        payload["voiceprint_fixture"] = None
    elif mutation == "external":
        external = tmp_path / "outside"
        external.mkdir()
        outside_manifest = external / "voiceprint-fixtures.json"
        outside_manifest.write_bytes(manifest.read_bytes())
        payload["voiceprint_fixture"]["directory"] = str(external.resolve())
        payload["voiceprint_fixture"]["manifest"] = str(
            outside_manifest.resolve(),
        )
    elif mutation == "bad_name":
        wrong = fixture_dir / "other.json"
        wrong.write_bytes(manifest.read_bytes())
        payload["voiceprint_fixture"]["manifest"] = str(wrong.resolve())
    elif mutation == "bad_hash":
        payload["voiceprint_fixture"]["manifest_sha256"] = "0" * 64
    else:
        payload["voiceprint_fixture"]["audio_api_origin"] = (
            "https://audio.example.test/path"
        )
    bundle.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(module.StackLeaseProtocolError, match="fixture|shape"):
        module.load_child_bundle(
            bundle,
            bundle_root=bundle_root,
            lease_id="lease-fixture",
            fixture_required=True,
            inherited={},
        )


def test_fixture_bundle_rejects_symbolic_link(tmp_path):
    module = require_module()
    bundle_root = tmp_path / "lease-bundles"
    expected = tmp_path / "e2e_voiceprint" / "artifacts"
    expected.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = outside / "voiceprint-fixtures.json"
    manifest.write_bytes(b"{}")
    fixture_link = expected / "voiceprint-fixtures"
    try:
        fixture_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(module.StackLeaseProtocolError, match="fixture"):
        module.write_token_bundle(
            root=bundle_root,
            lease_id="lease-fixture",
            case_id="e2e_voiceprint",
            run_id="e2e-run-fixture",
            user_id="e2e-run-fixture-e2e_voiceprint",
            vehicle_id="v1",
            timeout_s=300,
            secret=b"x" * 32,
            memory_sessions=0,
            fixture_dir=fixture_link,
            fixture_manifest=fixture_link / "voiceprint-fixtures.json",
            fixture_manifest_sha256=hashlib.sha256(b"{}").hexdigest(),
            fixture_audio_api_origin="https://audio.example.test:5443",
        )


def test_posix_private_metadata_rejects_wrong_mode_and_owner():
    module = require_module()

    with pytest.raises(module.StackLeaseProtocolError, match="permissions"):
        module._require_posix_private_metadata(
            SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=1000),
            expected_mode=0o600,
            expected_kind="file",
            current_uid=1000,
        )
    with pytest.raises(module.StackLeaseProtocolError, match="owner"):
        module._require_posix_private_metadata(
            SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1001),
            expected_mode=0o600,
            expected_kind="file",
            current_uid=1000,
        )
    with pytest.raises(module.StackLeaseProtocolError, match="file type"):
        module._require_posix_private_metadata(
            SimpleNamespace(st_mode=stat.S_IFLNK | 0o600, st_uid=1000),
            expected_mode=0o600,
            expected_kind="file",
            current_uid=1000,
        )


def test_private_directory_permission_enforcement_failure_is_failclosed(
    monkeypatch,
    tmp_path,
):
    module = require_module()

    def reject_permissions(*_args, **_kwargs):
        raise module.StackLeaseProtocolError("private path permissions")

    monkeypatch.setattr(module, "_enforce_private_posix_path", reject_permissions)
    with pytest.raises(module.StackLeaseProtocolError, match="permissions"):
        module._make_private_directory(tmp_path / "private")


def test_common_private_rewrite_permission_failure_keeps_original(
    monkeypatch,
    tmp_path,
):
    module = require_module()
    target = tmp_path / "tokens.json"
    target.write_bytes(b"original")

    def reject_file(path, *, expected_mode, expected_kind):
        del path, expected_mode
        if expected_kind == "file":
            raise module.StackLeaseProtocolError("private file permissions")

    monkeypatch.setattr(module, "_enforce_private_posix_path", reject_file)
    with pytest.raises(module.StackLeaseProtocolError, match="permissions"):
        module.replace_private_file(
            target,
            b"replacement",
            temporary_prefix=".bundle-",
        )
    assert target.read_bytes() == b"original"


def test_common_private_rewrite_replace_failure_keeps_original_and_no_temp(
    monkeypatch,
    tmp_path,
):
    module = require_module()
    target = tmp_path / "tokens.json"
    target.write_bytes(b"original")

    def reject_replace(_source, _target):
        raise OSError("replace denied")

    monkeypatch.setattr(module.os, "replace", reject_replace)
    with pytest.raises(module.StackLeaseProtocolError, match="replace"):
        module.replace_private_file(
            target,
            b"replacement",
            temporary_prefix=".bundle-",
        )
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".bundle-*.tmp"))


def test_common_private_rewrite_post_replace_verify_failure_removes_target(
    monkeypatch,
    tmp_path,
):
    module = require_module()
    target = tmp_path / "tokens.json"
    target.write_bytes(b"original")
    original_verify = module._verify_private_posix_path

    def fail_installed_target(path, *, expected_mode, expected_kind):
        if Path(path) == target and expected_kind == "file":
            raise module.StackLeaseProtocolError(
                "installed target permissions are invalid",
            )
        return original_verify(
            path,
            expected_mode=expected_mode,
            expected_kind=expected_kind,
        )

    monkeypatch.setattr(
        module,
        "_verify_private_posix_path",
        fail_installed_target,
    )
    with pytest.raises(module.StackLeaseProtocolError, match="permissions"):
        module.replace_private_file(
            target,
            b"sensitive-replacement",
            temporary_prefix=".bundle-",
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".bundle-*.tmp"))


def test_initial_and_presigned_bundle_share_private_path_enforcement(
    monkeypatch,
    tmp_path,
):
    module = require_module()
    runner = load_runner("run_e2e_private_bundle_permissions")
    enforced: list[tuple[str, int, str]] = []
    verified: list[tuple[str, int, str]] = []
    original_enforce = module._enforce_private_posix_path
    original_verify = module._verify_private_posix_path

    def record(path, *, expected_mode, expected_kind):
        enforced.append((Path(path).name, expected_mode, expected_kind))
        return original_enforce(
            path,
            expected_mode=expected_mode,
            expected_kind=expected_kind,
        )

    def record_verify(path, *, expected_mode, expected_kind):
        verified.append((Path(path).name, expected_mode, expected_kind))
        return original_verify(
            path,
            expected_mode=expected_mode,
            expected_kind=expected_kind,
        )

    monkeypatch.setattr(module, "_enforce_private_posix_path", record)
    monkeypatch.setattr(module, "_verify_private_posix_path", record_verify)
    monkeypatch.setattr(runner, "replace_private_file", module.replace_private_file)
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-private",
        case_id="e2e_memory",
        run_id="e2e-run-private",
        user_id="e2e-run-private-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    initial = list(enforced)
    initial_verified = list(verified)
    enforced.clear()
    verified.clear()

    runner._presign_memory_bundle(
        bundle,
        expected_bundle_root=tmp_path,
        secret=b"x" * 32,
        run_id="e2e-run-private",
        user_id="e2e-run-private-e2e_memory",
        timeout_s=30,
        memory_sessions=1,
    )

    assert [(mode, kind) for _name, mode, kind in initial] == [
        (0o700, "directory"),
        (0o600, "file"),
    ]
    assert [(mode, kind) for _name, mode, kind in enforced] == [
        (0o600, "file"),
    ]
    assert ("tokens.json", 0o600, "file") in initial_verified
    assert ("tokens.json", 0o600, "file") in verified
    assert not bundle.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership/mode contract")
def test_written_bundle_has_exact_posix_private_modes(tmp_path):
    module = require_module()
    path = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-posix",
        case_id="e2e_memory",
        run_id="e2e-run-posix",
        user_id="e2e-run-posix-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
    )
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent.stat().st_uid == os.getuid()
    assert path.stat().st_uid == os.getuid()


def test_child_protocol_rejects_secret_or_relative_bundle(tmp_path):
    module = require_module()
    with pytest.raises(module.StackLeaseProtocolError):
        module.load_child_bundle(
            Path("relative.json"),
            bundle_root=tmp_path,
            lease_id="lease-opaque",
            inherited={},
        )
    absolute = (tmp_path / "bundle.json").resolve()
    absolute.write_text(
        json.dumps({"lease_id": "lease-opaque"}),
        encoding="utf-8",
    )
    with pytest.raises(module.StackLeaseProtocolError, match="secret"):
        module.load_child_bundle(
            absolute,
            bundle_root=tmp_path,
            lease_id="lease-opaque",
            inherited={"E2E_IDENTITY_SECRET": "forbidden"},
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("E2E_CAPABILITY_SECRET", ""),
        ("E2E_CAPABILITY_SECRET", "owner-secret"),
        ("E2E_CAPABILITY_ENABLED", "false"),
        ("E2E_CAPABILITY_ENABLED", "true"),
    ],
)
def test_child_bundle_rejects_any_inherited_capability_owner_key(
    tmp_path,
    name,
    value,
):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-capability-owner",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    with pytest.raises(module.StackLeaseProtocolError, match="capability"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-capability-owner",
            inherited={name: value},
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("E2E_NAMESPACE_ADMIN_SECRET", ""),
        ("E2E_NAMESPACE_ADMIN_SECRET", "owner-secret"),
        ("E2E_NAMESPACE_ADMIN_ENABLED", "false"),
        ("E2E_NAMESPACE_ADMIN_ENABLED", "true"),
    ],
)
def test_child_bundle_rejects_any_inherited_namespace_admin_owner_key(
    tmp_path,
    name,
    value,
):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-namespace-admin",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["memory_session_ids"] = ["e2e-mem.v1.payload.signature"]
    bundle.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(module.StackLeaseProtocolError, match="namespace admin"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-namespace-admin",
            inherited={name: value},
        )


@pytest.mark.parametrize("duplicate_key", ["identity_token", "memory_session_ids"])
def test_child_bundle_rejects_duplicate_sensitive_json_keys(
    tmp_path,
    duplicate_key,
):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-duplicate",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    raw = bundle.read_text(encoding="utf-8")
    marker = f'"{duplicate_key}":'
    duplicate_value = (
        '"e2e.v1.duplicate.signature"'
        if duplicate_key == "identity_token"
        else "[]"
    )
    bundle.write_text(
        raw.replace(marker, f'{marker}{duplicate_value},{marker}', 1),
        encoding="utf-8",
    )

    with pytest.raises(module.StackLeaseProtocolError, match="duplicate"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-duplicate",
            inherited={},
        )


def _private_dir(path: Path) -> Path:
    """建一个满足 lease 私有目录契约（POSIX 0o700）的目录。

    `_verify_private_posix_path` 在 Windows 上直接 return，在 Linux 上会先校验
    权限位——用 `mkdir()` 建出来的是 0o755，于是这些用例在 Linux 上**先撞权限错误、
    根本走不到它们要断言的那个分支**（CI 报 "Regex pattern did not match"）。
    换句话说：**这些断言在 Windows 上从来没被真正执行过。**
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def test_child_bundle_rejects_16mib_unknown_payload_before_json_load(tmp_path):
    module = require_module()
    directory = _private_dir(tmp_path / "lease-huge-e2e_memory")
    bundle = directory / "tokens.json"
    bundle.write_bytes(
        b'{"unknown":"' + b"x" * (16 * 1024 * 1024) + b'"}',
    )
    if os.name != "nt":
        os.chmod(bundle, 0o600)      # 同私有目录：不满足权限契约就走不到大小检查

    with pytest.raises(module.StackLeaseProtocolError, match="large"):
        module.load_child_bundle(
            bundle.resolve(),
            bundle_root=tmp_path,
            lease_id="lease-huge",
            inherited={},
        )


def test_child_bundle_rejects_8mib_identity_token(tmp_path):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-huge-token",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["identity_token"] = "x" * (8 * 1024 * 1024)
    bundle.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(module.StackLeaseProtocolError, match="large"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-huge-token",
            inherited={},
        )


def test_child_bundle_rejects_empty_identity_secret_key(tmp_path):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-empty-secret",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    with pytest.raises(module.StackLeaseProtocolError, match="secret"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-empty-secret",
            inherited={"E2E_IDENTITY_SECRET": ""},
        )


@pytest.mark.parametrize("value", ["false", "true"])
def test_child_bundle_rejects_inherited_identity_enabled_owner_key(
    tmp_path,
    value,
):
    module = require_module()
    bundle = module.write_token_bundle(
        root=tmp_path,
        lease_id="lease-identity-enabled",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=0,
    )
    with pytest.raises(module.StackLeaseProtocolError, match="owner"):
        module.load_child_bundle(
            bundle,
            bundle_root=tmp_path,
            lease_id="lease-identity-enabled",
            inherited={"E2E_IDENTITY_ENABLED": value},
        )


def test_child_bundle_rejects_nonregular_file_and_expected_root_escape(tmp_path):
    module = require_module()
    private_root = _private_dir(tmp_path / "private")
    directory_path = _private_dir(private_root / "lease-dir-e2e_memory" / "tokens.json")
    if os.name != "nt":
        os.chmod(directory_path.parent, 0o700)
    with pytest.raises(module.StackLeaseProtocolError, match="regular"):
        module.load_child_bundle(
            directory_path.resolve(),
            bundle_root=private_root,
            lease_id="lease-dir",
            inherited={},
        )

    outside_root = tmp_path / "outside"
    escaped = module.write_token_bundle(
        root=outside_root,
        lease_id="lease-escape",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    with pytest.raises(module.StackLeaseProtocolError, match="root"):
        module.load_child_bundle(
            escaped,
            bundle_root=private_root,
            lease_id="lease-escape",
            inherited={},
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_child_bundle_rejects_junction_escape_from_expected_root(tmp_path):
    module = require_module()
    private_root = tmp_path / "private"
    private_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_bundle = module.write_token_bundle(
        root=outside_root,
        lease_id="lease-junction",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=30,
        secret=b"x" * 32,
        memory_sessions=1,
    )
    junction = private_root / outside_bundle.parent.name
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_bundle.parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not os.path.isjunction(junction):
        pytest.skip("junction creation unavailable")

    with pytest.raises(module.StackLeaseProtocolError, match="root|junction"):
        module.load_child_bundle(
            (junction / "tokens.json").absolute(),
            bundle_root=private_root,
            lease_id="lease-junction",
            inherited={},
        )


def test_two_children_are_both_waited_and_return_codes_aggregate():
    module = require_module()
    first, second = FakeChild(0), FakeChild(1)
    outcomes = module.wait_for_children([first, second])
    assert first.waited and second.waited
    assert outcomes == [0, 1]
    assert module.aggregate_return_codes(outcomes) == 1


def test_one_child_crash_still_waits_for_the_other():
    module = require_module()
    crashed, survivor = FakeChild(crash=True), FakeChild(0)
    outcomes = module.wait_for_children([crashed, survivor])
    assert crashed.waited and survivor.waited
    assert outcomes == [1, 0]


def test_restore_failure_is_identity_cleanup_and_overrides_pass():
    module = require_module()
    compose = FakeCompose(fail_restore=True)
    lease = module.IdentityStackLease(
        repo_root=Path.cwd(),
        environ={},
        compose=compose,
        ready=lambda: True,
        secret_factory=lambda: b"x" * 32,
        lease_id_factory=lambda: "lease-opaque",
    )
    lease.enable()
    with pytest.raises(module.IdentityCleanupError, match="identity_cleanup"):
        lease.restore()
    assert len(compose.calls) == 2
    compose.fail_restore = False
    lease.restore()


def test_restore_failure_keeps_owner_lock_and_secret_until_retry_succeeds(
    tmp_path,
):
    module = require_module()
    calls: list[dict[str, str]] = []
    off_attempts = 0

    def flaky_compose(environ):
        nonlocal off_attempts
        calls.append(dict(environ))
        if environ.get("E2E_IDENTITY_ENABLED") == "false":
            off_attempts += 1
            if off_attempts == 1:
                raise RuntimeError("transient restore failure")

    owner_env: dict[str, str] = {}
    lease = module.IdentityStackLease(
        repo_root=tmp_path,
        environ=owner_env,
        compose=flaky_compose,
        ready=lambda: True,
        secret_factory=lambda: b"a" * 32,
        lease_id_factory=lambda: "lease-retry-owner",
    )
    competitor = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=lambda _env: pytest.fail("competitor reached compose"),
        ready=lambda: True,
        secret_factory=lambda: b"b" * 32,
        lease_id_factory=lambda: "lease-competitor",
    )

    lease.enable()
    with pytest.raises(module.IdentityCleanupError, match="identity_cleanup"):
        lease.restore()

    assert lease.secret == b"a" * 32
    assert lease._restored is False
    assert owner_env["E2E_IDENTITY_ENABLED"] == "false"
    assert "E2E_IDENTITY_SECRET" not in owner_env
    with pytest.raises(module.IdentityLeaseBusyError, match="identity_busy"):
        competitor.enable()

    lease.restore()
    assert lease._restored is True
    assert lease.secret == b""
    assert lease._stack_lock is None
    assert "E2E_IDENTITY_ENABLED" not in owner_env
    assert len(calls) == 3


def test_enable_cleanup_failure_remains_retryable_with_same_owner_state(
    tmp_path,
):
    module = require_module()
    calls: list[dict[str, str]] = []
    off_attempts = 0

    def failing_enable_then_flaky_restore(environ):
        nonlocal off_attempts
        calls.append(dict(environ))
        if environ.get("E2E_IDENTITY_ENABLED") == "true":
            raise RuntimeError("enable compose failed")
        off_attempts += 1
        if off_attempts == 1:
            raise RuntimeError("first restore failed")

    lease = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=failing_enable_then_flaky_restore,
        ready=lambda: True,
        secret_factory=lambda: b"z" * 32,
        lease_id_factory=lambda: "lease-enable-retry",
    )

    with pytest.raises(module.IdentityCleanupError, match="identity_cleanup"):
        lease.enable()
    assert lease.secret == b"z" * 32
    assert lease._stack_lock is not None
    assert lease._restored is False

    lease.restore()
    assert lease.secret == b""
    assert lease._stack_lock is None
    assert lease._restored is True
    assert len(calls) == 4
    assert [
        call["E2E_IDENTITY_ENABLED"]
        for call in calls
    ] == ["true", "true", "false", "false"]


def test_runner_retries_identity_restore_once_with_secret_free_off_environment():
    runner = load_runner("run_e2e_retryable_restore_test")
    snapshots: list[dict[str, str]] = []

    class Lease:
        def __init__(self):
            self.environ = {
                "E2E_IDENTITY_ENABLED": "false",
                "E2E_STACK_LEASE_ROLE": "owner",
            }
            self.calls = 0

        def restore(self):
            self.calls += 1
            snapshots.append(dict(self.environ))
            if self.calls == 1:
                raise runner.IdentityCleanupError("identity_cleanup")

    lease = Lease()
    assert runner._restore_identity_lease(lease) is True
    assert lease.calls == 2
    assert all(
        "E2E_IDENTITY_SECRET" not in snapshot
        and "E2E_CAPABILITY_SECRET" not in snapshot
        and "E2E_NAMESPACE_ADMIN_SECRET" not in snapshot
        for snapshot in snapshots
    )


def test_repository_lock_rejects_second_owner_without_compose(tmp_path):
    module = require_module()
    first_compose = FakeCompose()
    second_compose = FakeCompose()
    first = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=first_compose,
        ready=lambda: True,
        secret_factory=lambda: b"a" * 32,
        lease_id_factory=lambda: "lease-first-owner",
    )
    second = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=second_compose,
        ready=lambda: True,
        secret_factory=lambda: b"b" * 32,
        lease_id_factory=lambda: "lease-second-owner",
    )
    busy_error = getattr(module, "IdentityLeaseBusyError", RuntimeError)

    first.enable()
    try:
        with pytest.raises(busy_error, match="identity_busy"):
            second.enable()
        assert len(first_compose.calls) == 1
        assert second_compose.calls == []
    finally:
        first.restore()
    assert len(first_compose.calls) == 2


def test_repository_lock_rejects_real_competing_process_before_compose(tmp_path):
    module = require_module()
    owner_compose = FakeCompose()
    owner = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=owner_compose,
        ready=lambda: True,
        secret_factory=lambda: b"a" * 32,
        lease_id_factory=lambda: "lease-parent-owner",
    )
    compose_marker = tmp_path / "competing-compose-ran"
    child_code = r"""
import sys
from pathlib import Path
from scripts.e2e_stack_lease import IdentityStackLease

repo = Path(sys.argv[1])
marker = Path(sys.argv[2])
def compose(_env):
    marker.write_text("compose-ran", encoding="utf-8")
lease = IdentityStackLease(
    repo_root=repo,
    environ={},
    compose=compose,
    ready=lambda: True,
    secret_factory=lambda: b"b" * 32,
    lease_id_factory=lambda: "lease-child-owner",
)
try:
    lease.enable()
except Exception as exc:
    print(f"{type(exc).__name__}:{exc}")
    raise SystemExit(0 if "identity_busy" in str(exc) else 2)
lease.restore()
print("unexpected-enable")
raise SystemExit(3)
"""

    owner.enable()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                child_code,
                str(tmp_path),
                str(compose_marker),
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        owner.restore()

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "identity_busy" in completed.stdout
    assert not compose_marker.exists()
    assert len(owner_compose.calls) == 2


def test_stale_lock_metadata_is_diagnostic_not_ownership(tmp_path):
    module = require_module()
    lock_path = module.identity_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        'L\n{"owner_pid":999999,"lease_id":"stale","started_at":0}',
        encoding="utf-8",
    )
    compose = FakeCompose()
    lease = module.IdentityStackLease(
        repo_root=tmp_path,
        environ={},
        compose=compose,
        ready=lambda: True,
        secret_factory=lambda: b"x" * 32,
        lease_id_factory=lambda: "lease-replaces-stale-metadata",
    )

    lease.enable()
    metadata_text = module.identity_lock_metadata(tmp_path)
    assert '"owner_pid":' in metadata_text
    assert '"lease_id":"lease-replaces-stale-metadata"' in metadata_text
    assert '"started_at":' in metadata_text
    assert "secret" not in metadata_text.lower()
    lease.restore()

    assert lock_path.is_file()
    assert len(compose.calls) == 2


def test_parallel_subrun_argv_uses_only_fixed_internal_lease_parameters(tmp_path):
    module = require_module()
    bundle = (tmp_path / "tokens.json").resolve()
    bundle.write_text("{}", encoding="utf-8")
    argv = module.parallel_subrun_argv(
        repo_root=tmp_path,
        case_id="e2e_memory",
        lane="milestone",
        milestone="M-A",
        lease_id="lease-opaque",
        token_bundle=bundle,
    )
    assert "--lease-child" in argv
    assert argv[argv.index("--lease-id") + 1] == "lease-opaque"
    assert Path(argv[argv.index("--token-bundle") + 1]).is_absolute()
    assert Path(argv[argv.index("--token-bundle-root") + 1]).is_absolute()
    assert "--parallel-isolation" not in argv


def test_exact_parallel_isolation_entrypoint_is_accepted():
    runner_path = Path(__file__).resolve().parents[1] / "run_e2e.py"
    spec = importlib.util.spec_from_file_location(
        "run_e2e_identity_lease_test",
        runner_path,
    )
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    args = runner._parser().parse_args([
        "--milestone",
        "M-A",
        "--parallel-isolation",
        "2",
        "--id",
        "e2e_memory",
        "--id",
        "e2e_voiceprint",
    ])
    runner._preflight(args)
    assert args.lane == "milestone"
    assert args.parallel_isolation == 2


def test_compose_exposes_identity_gate_only_to_edge_and_llm_gateway():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load(
        (root / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8"),
    )
    services = compose["services"]
    for name in ("edge-gateway", "llm-gateway"):
        env = services[name]["environment"]
        assert env["E2E_IDENTITY_ENABLED"] == "${E2E_IDENTITY_ENABLED:-false}"
        assert env["E2E_IDENTITY_SECRET"] == "${E2E_IDENTITY_SECRET-}"
    assert "E2E_IDENTITY_ENABLED" not in services["memory"]["environment"]
    assert "E2E_IDENTITY_SECRET" not in services["memory"]["environment"]


def test_compose_recreate_overlays_runner_gates_on_operational_root(
    tmp_path,
    monkeypatch,
):
    module = require_module()
    (tmp_path / "compose.yaml").write_text(
        "services:\n  edge-gateway: {}\n  llm-gateway: {}\n"
        "  memory: {}\n  proactive: {}\n  mcp-bridge: {}\n",
        encoding="utf-8",
    )
    observed = {}

    def fake_run(argv, **kwargs):
        override_path = Path(argv[argv.index("-f", 4) + 1])
        observed["argv"] = list(argv)
        observed["override"] = json.loads(
            override_path.read_text(encoding="utf-8"),
        )
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._compose_recreate(
        tmp_path,
        {
            "E2E_IDENTITY_ENABLED": "true",
            "E2E_IDENTITY_SECRET": "opaque",
            "E2E_CAPABILITY_ENABLED": "true",
            "E2E_CAPABILITY_SECRET": "opaque",
            "E2E_NAMESPACE_ADMIN_ENABLED": "true",
            "E2E_NAMESPACE_ADMIN_SECRET": "opaque",
        },
        extra_services=("proactive", "mcp-bridge"),
    )

    assert observed["argv"][:5] == [
        "docker",
        "compose",
        "-f",
        str(tmp_path / "compose.yaml"),
        "-f",
    ]
    services = observed["override"]["services"]
    for name in ("edge-gateway", "llm-gateway"):
        assert services[name]["environment"] == {
            "E2E_IDENTITY_ENABLED": "${E2E_IDENTITY_ENABLED:-false}",
            "E2E_IDENTITY_SECRET": "${E2E_IDENTITY_SECRET-}",
        }
    assert services["memory"]["environment"] == {
        "E2E_CAPABILITY_ENABLED": "${E2E_CAPABILITY_ENABLED:-false}",
        "E2E_CAPABILITY_SECRET": "${E2E_CAPABILITY_SECRET-}",
    }
    for name in ("proactive", "mcp-bridge"):
        assert services[name]["environment"][
            "E2E_NAMESPACE_ADMIN_ENABLED"
        ] == "${E2E_NAMESPACE_ADMIN_ENABLED:-false}"
        assert services[name]["environment"][
            "E2E_NAMESPACE_ADMIN_SECRET"
        ] == "${E2E_NAMESPACE_ADMIN_SECRET-}"
    assert services["mcp-bridge"]["environment"]["NATS_URL"] == (
        "nats://nats:4222"
    )
    assert observed["kwargs"]["cwd"] == tmp_path


def test_compose_exposes_namespace_admin_only_to_selected_persistent_services():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load(
        (root / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8"),
    )
    services = compose["services"]
    for name in ("proactive", "mcp-bridge"):
        env = services[name]["environment"]
        assert env["E2E_NAMESPACE_ADMIN_ENABLED"] == (
            "${E2E_NAMESPACE_ADMIN_ENABLED:-false}"
        )
        assert env["E2E_NAMESPACE_ADMIN_SECRET"] == (
            "${E2E_NAMESPACE_ADMIN_SECRET-}"
        )
    assert services["mcp-bridge"]["environment"]["NATS_URL"] == (
        "nats://nats:4222"
    )
    for name, service in services.items():
        if name in {"proactive", "mcp-bridge"}:
            continue
        environment = service.get("environment", {})
        assert "E2E_NAMESPACE_ADMIN_ENABLED" not in environment
        assert "E2E_NAMESPACE_ADMIN_SECRET" not in environment


def test_runner_signs_immediately_for_child_and_clears_owner_secret(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    runner_path = root / "scripts" / "run_e2e.py"
    spec = importlib.util.spec_from_file_location(
        "run_e2e_single_identity_lease_test",
        runner_path,
    )
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    state = {"restores": 0, "child_env": None}

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.lease_id = "lease-opaque"
            self.secret = b"x" * 32

        def enable(self):
            self.environ["E2E_IDENTITY_ENABLED"] = "true"
            self.environ["E2E_IDENTITY_SECRET"] = "owner-only"

        def restore(self):
            state["restores"] += 1
            self.environ.pop("E2E_IDENTITY_ENABLED", None)
            self.environ.pop("E2E_IDENTITY_SECRET", None)
            self.secret = b""

    def fake_run_child(case, *, environ, **_kwargs):
        state["child_env"] = dict(environ)
        return {
            "id": case.id,
            "status": "PASS",
            "returncode": 0,
            "errors": [],
            "counts": {
                "selected": 1,
                "executed": 1,
                "passed": 1,
                "failed": 0,
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

    monkeypatch.setattr(runner, "IdentityStackLease", FakeLease)
    monkeypatch.setattr(runner, "_run_child", fake_run_child)
    monkeypatch.setattr(runner, "_new_run_id", lambda: "e2e-run-abc")
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)
    output = io.StringIO()
    rc = runner.main(
        ["--lane", "milestone", "--milestone", "M-A", "--id", "e2e_memory"],
        repo_root=root,
        environ={},
        stdout=output,
        staleness_evaluator=lambda _root: runner.Staleness(False, ()),
    )
    assert rc == 0
    assert state["restores"] == 1
    assert state["child_env"]["E2E_IDENTITY_TOKEN"].startswith("e2e.v1.")
    assert state["child_env"]["E2E_STACK_LEASE_ROLE"] == "child"
    assert "E2E_IDENTITY_SECRET" not in state["child_env"]


def _subrun_result(case, *, passed: bool):
    return {
        "id": case.id,
        "status": "PASS" if passed else "FAIL",
        "returncode": 0 if passed else 1,
        "errors": [] if passed else ["child_failed"],
        "counts": {
            "selected": 1,
            "executed": 1,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
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


def _install_parallel_fakes(
    monkeypatch,
    tmp_path,
    *,
    outcomes,
    fail_restore=False,
    launch_fail_case=None,
):
    module = require_module()
    runner = load_runner("run_e2e_parallel_owner_integration")
    assert runner.IdentityStackLease is module.IdentityStackLease
    compose = FakeCompose(fail_restore=fail_restore)
    events = []
    children = {}
    child_envs = {}
    bundles = {}
    trees = []

    def lease_factory(*, repo_root, environ):
        lease = module.IdentityStackLease(
            repo_root=repo_root,
            environ=environ,
            compose=compose,
            ready=lambda: True,
            secret_factory=lambda: b"x" * 32,
            lease_id_factory=lambda: "lease-owner-integration",
        )
        compose.lease = lease
        return lease

    class FakeProcess:
        def __init__(
            self,
            case,
            *,
            passed=True,
            crash=False,
            communicate_error=None,
            polls_before_exit=0,
            stdout_prefix="",
        ):
            self.case = case
            self.returncode = 0 if passed else 1
            self.passed = passed
            self.crash = crash
            self.communicate_error = communicate_error
            self.communicated = False
            self.killed = False
            self.waited = False
            self.pid = 4000 + len(children)
            self.polls_before_exit = polls_before_exit
            result = _subrun_result(self.case, passed=self.passed)
            summary = json.dumps({
                "exit_code": self.returncode,
                "results": [result],
            })
            self.stdout = io.BytesIO(
                (stdout_prefix + summary).encode("utf-8"),
            )
            self.stderr = io.BytesIO()

        def communicate(self, timeout=None):
            del timeout
            self.communicated = True
            events.append(f"wait:{self.case.id}")
            if self.communicate_error is not None:
                raise self.communicate_error
            if self.crash:
                raise RuntimeError("child crashed")
            result = _subrun_result(self.case, passed=self.passed)
            return (
                json.dumps({
                    "exit_code": self.returncode,
                    "results": [result],
                }),
                "",
            )

        def poll(self):
            self.communicated = True
            events.append(f"wait:{self.case.id}")
            if self.communicate_error is not None:
                raise self.communicate_error
            if self.crash:
                raise RuntimeError("child crashed")
            if self.polls_before_exit > 0:
                self.polls_before_exit -= 1
                return None
            events.append(f"complete:{self.case.id}")
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9
            events.append(f"kill:{self.case.id}")

        def wait(self, timeout=None):
            del timeout
            self.waited = True
            return self.returncode

    class FakeTree:
        def __init__(self):
            self.available = True
            self.attached = 0
            self.resumed = 0
            self.terminated = 0
            self.closed = 0
            trees.append(self)

        def attach(self, _process):
            self.attached += 1
            return True

        def resume(self, _process):
            self.resumed += 1
            return True

        def terminate(self, process):
            self.terminated += 1
            process.kill()

        def close(self):
            self.closed += 1

    manifest = runner.load_manifest(
        Path(__file__).resolve().parents[2] / "test" / "e2e_manifest.yaml",
        repo_root=Path(__file__).resolve().parents[2],
    )
    original_popen = runner.subprocess.Popen

    def fake_popen(argv, **kwargs):
        if "--lease-child" not in argv:
            return original_popen(argv, **kwargs)
        case_id = argv[argv.index("--id") + 1]
        if case_id == launch_fail_case:
            raise OSError("subrunner launch failed")
        case = manifest.by_id[case_id]
        outcome = outcomes[case_id]
        passed, crash = outcome[:2]
        communicate_error = outcome[2] if len(outcome) > 2 else None
        polls_before_exit = outcome[3] if len(outcome) > 3 else 0
        stdout_prefix = outcome[4] if len(outcome) > 4 else ""
        process = FakeProcess(
            case,
            passed=passed,
            crash=crash,
            communicate_error=communicate_error,
            polls_before_exit=polls_before_exit,
            stdout_prefix=stdout_prefix,
        )
        children[case_id] = process
        child_envs[case_id] = dict(kwargs["env"])
        bundle = Path(argv[argv.index("--token-bundle") + 1])
        bundles[case_id] = json.loads(bundle.read_text(encoding="utf-8"))
        events.append(f"launch:{case_id}")
        return process

    run_root = tmp_path / "run"

    def fake_mkdtemp(*, prefix):
        del prefix
        run_root.mkdir()
        return str(run_root)

    monkeypatch.setattr(runner, "IdentityStackLease", lease_factory)
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_ProcessTree", FakeTree)
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)

    def fake_fixture_pre_step(
        case,
        *,
        repo_root,
        artifact_dir,
        environ,
        sensitive_values,
    ):
        del case, repo_root, sensitive_values
        fixture_dir = artifact_dir / "voiceprint-fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        manifest = fixture_dir / "voiceprint-fixtures.json"
        manifest.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "error": "",
            "returncode": 0,
            "diagnostic": "",
            "logs": [],
            "fixture_dir": str(fixture_dir.resolve(strict=True)),
            "manifest": str(manifest.resolve(strict=True)),
            "manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
            "audio_api_origin": environ["E2E_AUDIO_API_ORIGIN"],
        }

    monkeypatch.setattr(runner, "_run_fixture_pre_step", fake_fixture_pre_step)
    return runner, compose, events, children, child_envs, bundles, trees


def _parallel_main(runner, *, environ=None):
    output = io.StringIO()
    rc = runner.main(
        [
            "--milestone",
            "M-A",
            "--parallel-isolation",
            "2",
            "--id",
            "e2e_memory",
            "--id",
            "e2e_voiceprint",
        ],
        repo_root=Path(__file__).resolve().parents[2],
        environ=environ or {"VEHICLE_ID": "v1"},
        stdout=output,
        staleness_evaluator=lambda _root: runner.Staleness(False, ()),
    )
    summary = json.loads(output.getvalue().splitlines()[-1])
    return rc, summary


def test_parallel_owner_uses_bounded_nonblocking_concurrent_collection():
    runner = load_runner("run_e2e_parallel_collection_contract")
    source = inspect.getsource(runner._run_parallel_isolated)

    assert "_BoundedPipeCapture" in source
    assert ".poll(" in source
    assert ".communicate(" not in source
    assert "deadline" in source


def test_parallel_fast_second_finishes_before_slow_first(
    monkeypatch,
    tmp_path,
):
    runner, _compose, events, _children, _envs, _bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False, None, 3),
                "e2e_voiceprint": (True, False, None, 0),
            },
        )
    )

    rc, summary = _parallel_main(runner)

    assert rc == 0
    assert events.index("complete:e2e_voiceprint") < events.index(
        "complete:e2e_memory",
    )
    assert all(item["status"] == "PASS" for item in summary["results"])


def test_parallel_owner_ignores_inherited_fixture_paths_and_attests_run_path(
    monkeypatch,
    tmp_path,
):
    runner, _compose, _events, _children, child_envs, bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False, None, 0),
                "e2e_voiceprint": (True, False, None, 0),
            },
        )
    )
    attacker = tmp_path / "attacker"

    rc, summary = _parallel_main(
        runner,
        environ={
            "VEHICLE_ID": "v1",
            "E2E_AUDIO_API_ORIGIN": "http://audio.example.test",
            "E2E_VOICEPRINT_FIXTURE_DIR": str(attacker),
            "E2E_VOICEPRINT_FIXTURE_MANIFEST": str(attacker / "bad.json"),
            "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": "0" * 64,
        },
    )

    expected_dir = (
        tmp_path
        / "run"
        / "e2e_voiceprint"
        / "artifacts"
        / "voiceprint-fixtures"
    ).resolve()
    fixture = bundles["e2e_voiceprint"]["voiceprint_fixture"]
    assert rc == 0, summary
    assert fixture["directory"] == str(expected_dir)
    assert fixture["manifest"] == str(
        expected_dir / "voiceprint-fixtures.json",
    )
    assert child_envs["e2e_voiceprint"][
        "E2E_VOICEPRINT_FIXTURE_DIR"
    ] == str(expected_dir)


def test_parallel_deadlines_are_independent_and_launch_relative(
    monkeypatch,
    tmp_path,
):
    secret = "timeout-sibling-secret-do-not-print"
    huge_prefix = "x" * (2 * 64 * 1024) + "\n" + secret + "\n"
    runner, _compose, events, _children, _envs, _bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False, None, 100),
                "e2e_voiceprint": (
                    True,
                    False,
                    None,
                    1,
                    huge_prefix,
                ),
            },
        )
    )
    manifest = runner.load_manifest(
        Path(__file__).resolve().parents[2] / "test" / "e2e_manifest.yaml",
        repo_root=Path(__file__).resolve().parents[2],
    )
    object.__setattr__(manifest.by_id["e2e_memory"], "timeout_s", 1)
    object.__setattr__(manifest.by_id["e2e_voiceprint"], "timeout_s", 100)
    monkeypatch.setattr(runner, "load_manifest", lambda *_args, **_kwargs: manifest)
    clock = iter((0.0, 0.0, 32.0, 33.0))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    rc, summary = _parallel_main(
        runner,
        environ={"VEHICLE_ID": "v1", "LLM_API_KEY": secret},
    )

    assert rc == 1
    assert [item["id"] for item in summary["results"]] == [
        "e2e_memory",
        "e2e_voiceprint",
    ]
    assert summary["results"][0]["errors"] == ["timeout"]
    assert summary["results"][1]["status"] == "PASS"
    assert events.index("complete:e2e_voiceprint") < events.index(
        "kill:e2e_voiceprint",
    )
    sibling_logs = [
        Path(path)
        for path in summary["results"][1]["logs"]
        if "parallel-subruns" in path
    ]
    assert len(sibling_logs) == 2
    assert all(
        path.stat().st_size <= runner.MAX_LOG_BYTES
        for path in sibling_logs
    )
    assert secret not in "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sibling_logs
    )


def test_parallel_outer_logs_are_bounded_and_redacted(
    monkeypatch,
    tmp_path,
):
    secret = "parallel-outer-secret-do-not-print"
    huge_prefix = (
        "x" * (2 * 64 * 1024)
        + "\n"
        + secret
        + "\n"
    )
    runner, _compose, _events, _children, _envs, _bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False, None, 0, huge_prefix),
                "e2e_voiceprint": (True, False),
            },
        )
    )

    rc, summary = _parallel_main(
        runner,
        environ={"VEHICLE_ID": "v1", "LLM_API_KEY": secret},
    )

    assert rc == 0
    memory = next(
        item for item in summary["results"] if item["id"] == "e2e_memory"
    )
    outer_logs = [
        Path(path)
        for path in memory["logs"]
        if "parallel-subruns" in path
    ]
    assert len(outer_logs) == 2
    assert all(path.stat().st_size <= runner.MAX_LOG_BYTES for path in outer_logs)
    assert secret not in "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in outer_logs
    )


def test_parallel_owner_flow_launches_both_and_parses_independent_results(
    monkeypatch,
    tmp_path,
):
    runner, compose, events, children, child_envs, bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False),
                "e2e_voiceprint": (False, False),
            },
        )
    )
    rc, summary = _parallel_main(runner)

    assert rc == 1
    assert [call["E2E_IDENTITY_ENABLED"] for call in compose.calls] == [
        "true",
        "false",
    ]
    assert events[:2] == ["launch:e2e_memory", "launch:e2e_voiceprint"]
    assert all(process.communicated for process in children.values())
    assert [result["id"] for result in summary["results"]] == [
        "e2e_memory",
        "e2e_voiceprint",
    ]
    assert [result["status"] for result in summary["results"]] == ["PASS", "FAIL"]
    assert all("E2E_IDENTITY_SECRET" not in env for env in child_envs.values())
    assert all(
        "secret" not in json.dumps(bundle).lower()
        for bundle in bundles.values()
    )


def test_parallel_owner_waits_for_survivor_after_other_child_crashes(
    monkeypatch,
    tmp_path,
):
    runner, compose, events, children, _envs, _bundles, _trees = (
        _install_parallel_fakes(
        monkeypatch,
        tmp_path,
        outcomes={
            "e2e_memory": (False, True),
            "e2e_voiceprint": (True, False),
        },
        )
    )
    rc, summary = _parallel_main(runner)

    assert rc == 1
    assert len(compose.calls) == 2
    assert children["e2e_memory"].communicated
    assert children["e2e_voiceprint"].communicated
    assert events.index("wait:e2e_voiceprint") > events.index("wait:e2e_memory")
    assert summary["results"][0]["errors"] == ["child_runtime_failed"]
    assert summary["results"][1]["status"] == "PASS"


def test_parallel_owner_cleanup_failure_overrides_both_passes(
    monkeypatch,
    tmp_path,
):
    runner, compose, _events, _children, _envs, _bundles, _trees = (
        _install_parallel_fakes(
            monkeypatch,
            tmp_path,
            outcomes={
                "e2e_memory": (True, False),
                "e2e_voiceprint": (True, False),
            },
            fail_restore=True,
        )
    )
    rc, summary = _parallel_main(runner)
    calls_before_manual_recovery = len(compose.calls)
    compose.fail_restore = False
    compose.lease.restore()

    assert rc == 1
    assert calls_before_manual_recovery == 3
    assert summary["errors"] == ["identity_cleanup"]
    assert all(result["status"] == "FAIL" for result in summary["results"])
    assert all(
        "identity_cleanup" in result["errors"]
        for result in summary["results"]
    )


def test_parallel_owner_keyboard_interrupt_reaps_every_tree_before_restore(
    monkeypatch,
    tmp_path,
):
    (
        runner,
        compose,
        _events,
        children,
        _envs,
        _bundles,
        trees,
    ) = _install_parallel_fakes(
        monkeypatch,
        tmp_path,
        outcomes={
            "e2e_memory": (False, False, KeyboardInterrupt()),
            "e2e_voiceprint": (True, False),
        },
    )

    with pytest.raises(KeyboardInterrupt):
        _parallel_main(runner)

    assert [call["E2E_IDENTITY_ENABLED"] for call in compose.calls] == [
        "true",
        "false",
    ]
    assert len(children) == 2
    assert all(process.killed and process.waited for process in children.values())
    assert len(trees) == 2
    assert all(tree.terminated >= 1 and tree.closed == 1 for tree in trees)


def test_parallel_second_launch_failure_reaps_first_tree_before_restore(
    monkeypatch,
    tmp_path,
):
    (
        runner,
        compose,
        _events,
        children,
        _envs,
        _bundles,
        trees,
    ) = _install_parallel_fakes(
        monkeypatch,
        tmp_path,
        outcomes={
            "e2e_memory": (True, False),
            "e2e_voiceprint": (True, False),
        },
        launch_fail_case="e2e_voiceprint",
    )

    rc, summary = _parallel_main(runner)

    assert rc == 1
    assert [call["E2E_IDENTITY_ENABLED"] for call in compose.calls] == [
        "true",
        "false",
    ]
    assert set(children) == {"e2e_memory"}
    assert children["e2e_memory"].killed
    assert len(trees) == 2
    assert all(tree.closed == 1 for tree in trees)
    assert trees[0].terminated >= 1
    assert summary["results"][1]["errors"] == ["child_start_failed"]

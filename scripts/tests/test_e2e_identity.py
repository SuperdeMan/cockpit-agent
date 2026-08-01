from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

try:
    from scripts.e2e_identity import (
        IdentityTokenError,
        decode_secret,
        generate_secret,
        sign_identity,
        verify_identity,
    )
    from scripts import e2e_identity as _identity_module
    parse_identity_claims_unverified = getattr(
        _identity_module,
        "parse_identity_claims_unverified",
        None,
    )
except ModuleNotFoundError:
    IdentityTokenError = ValueError
    decode_secret = generate_secret = parse_identity_claims_unverified = None
    sign_identity = verify_identity = None


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test"))
from support import e2e as support_e2e  # noqa: E402
VECTORS = json.loads(
    (ROOT / "test" / "fixtures" / "e2e_identity_vectors.json").read_text(
        encoding="utf-8",
    ),
)


def _unsigned_token(payload: bytes, signature: bytes = b"x" * 32) -> str:
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    return f"e2e.v1.{encode(payload)}.{encode(signature)}"


def _canonical_claims(*, now: int = 1_700_000_000) -> dict:
    return {
        "run_id": "e2e-run-abc",
        "user_id": "e2e-run-abc-e2e_memory",
        "vehicle_id": "v1",
        "scopes": ["memory.read"],
        "iat": now,
        "exp": now + 420,
    }


def test_shared_vectors_define_the_full_cross_language_contract():
    assert verify_identity is not None, "e2e identity signer/verifier is missing"
    secret = decode_secret(VECTORS["secret_b64url"])
    now = VECTORS["now"]
    for vector in VECTORS["vectors"]:
        if vector["valid"]:
            claims = verify_identity(vector["token"], secret, now=now)
            assert claims.to_dict() == vector["claims"], vector["name"]
        else:
            with pytest.raises(IdentityTokenError):
                verify_identity(vector["token"], secret, now=now)


def test_unverified_claims_parser_accepts_tampered_signature_for_owner_self_check():
    assert parse_identity_claims_unverified is not None
    secret = bytes(range(32))
    token = sign_identity(
        secret,
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        scopes=("memory.read",),
        timeout_s=300,
        now=1_700_000_000,
    )
    parts = token.split(".")
    parts[-1] = base64.urlsafe_b64encode(b"tampered" * 4).rstrip(b"=").decode("ascii")

    claims = parse_identity_claims_unverified(
        ".".join(parts),
        now=1_700_000_001,
    )

    assert claims.user_id == "e2e-run-abc-e2e_memory"
    with pytest.raises(IdentityTokenError, match="signature"):
        verify_identity(".".join(parts), secret, now=1_700_000_001)


@pytest.mark.parametrize(
    "token",
    [
        "e2e.v2.payload.signature",
        "e2e.v1.not+base64.signature",
        "e2e.v1.payload",
    ],
)
def test_unverified_claims_parser_rejects_wrong_shape_version_and_base64(token):
    assert parse_identity_claims_unverified is not None
    with pytest.raises(IdentityTokenError):
        parse_identity_claims_unverified(token, now=1_700_000_001)


def test_unverified_claims_parser_rejects_duplicate_and_noncanonical_payloads():
    assert parse_identity_claims_unverified is not None
    duplicate = (
        b'{"run_id":"e2e-run-abc","run_id":"e2e-run-abc",'
        b'"user_id":"e2e-run-abc-e2e_memory","vehicle_id":"v1",'
        b'"scopes":["memory.read"],"iat":1700000000,"exp":1700000420}'
    )
    noncanonical = json.dumps(
        {
            "user_id": "e2e-run-abc-e2e_memory",
            "run_id": "e2e-run-abc",
            "vehicle_id": "v1",
            "scopes": ["memory.read"],
            "iat": 1_700_000_000,
            "exp": 1_700_000_420,
        },
        separators=(",", ":"),
    ).encode()

    with pytest.raises(IdentityTokenError, match="duplicate"):
        parse_identity_claims_unverified(
            _unsigned_token(duplicate),
            now=1_700_000_001,
        )
    with pytest.raises(IdentityTokenError, match="canonical"):
        parse_identity_claims_unverified(
            _unsigned_token(noncanonical),
            now=1_700_000_001,
        )


def test_unverified_claims_parser_rejects_expired_token():
    assert parse_identity_claims_unverified is not None
    payload = json.dumps(
        _canonical_claims(now=1_700_000_000),
        separators=(",", ":"),
    ).encode()
    with pytest.raises(IdentityTokenError, match="expired"):
        parse_identity_claims_unverified(
            _unsigned_token(payload),
            now=1_700_000_420,
        )


def test_runner_secret_is_exactly_32_random_bytes():
    assert generate_secret is not None, "e2e identity secret generator is missing"
    first = generate_secret()
    second = generate_secret()
    assert len(first) == len(second) == 32
    assert first != second


def test_signer_issues_at_child_start_with_full_timeout_plus_grace():
    assert sign_identity is not None, "e2e identity signer is missing"
    secret = bytes(range(32))
    token = sign_identity(
        secret,
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e-memory",
        vehicle_id="v1",
        scopes=("memory.read", "memory.write"),
        timeout_s=1800,
        now=1700000000,
    )
    claims = verify_identity(token, secret, now=1700001919)
    assert claims.iat == 1700000000
    assert claims.exp == 1700001920
    assert claims.exp - claims.iat == 1920
    with pytest.raises(IdentityTokenError, match="expired"):
        verify_identity(token, secret, now=1700001920)


@pytest.mark.parametrize("timeout_s", [0, -1, 1801, True])
def test_signer_rejects_timeout_outside_manifest_contract(timeout_s):
    assert sign_identity is not None, "e2e identity signer is missing"
    with pytest.raises(IdentityTokenError, match="timeout"):
        sign_identity(
            bytes(range(32)),
            run_id="e2e-run-abc",
            user_id="e2e-run-abc-e2e-memory",
            vehicle_id="v1",
            scopes=("memory.read",),
            timeout_s=timeout_s,
            now=1700000000,
        )


def test_secret_decoder_rejects_wrong_length_and_padding():
    assert decode_secret is not None, "e2e identity secret decoder is missing"
    short = base64.urlsafe_b64encode(b"x" * 31).rstrip(b"=").decode("ascii")
    with pytest.raises(IdentityTokenError, match="32 bytes"):
        decode_secret(short)
    with pytest.raises(IdentityTokenError, match="base64url"):
        decode_secret(VECTORS["secret_b64url"] + "=")


def test_owner_proof_accepts_only_ack_for_exact_run_user_and_vehicle():
    confirm = getattr(support_e2e, "confirm_identity_ack", None)
    assert confirm is not None, "E2E owner ACK proof helper is missing"
    env = {
        "E2E_RUN_ID": "e2e-run-abc",
        "E2E_TEST_ID": "e2e_memory",
        "E2E_USER_ID": "e2e-run-abc-e2e_memory",
        "E2E_SESSION_PREFIX": "e2e-run-abc-e2e_memory-session",
        "E2E_IDENTITY_TOKEN": "e2e.v1.payload.signature",
        "E2E_EXPECTED_VEHICLE_ID": "v1",
    }
    ack = {
        "type": "e2e_identity_ack",
        "run_id": "e2e-run-abc",
        "user_id": "e2e-run-abc-e2e_memory",
        "vehicle_id": "v1",
    }
    assert confirm(ack, env=env) == ack
    with pytest.raises(support_e2e.ProtocolError, match="owner"):
        confirm({**ack, "user_id": "e2e-run-abc-e2e-other"}, env=env)


def test_signer_accepts_manifest_case_id_in_user_namespace():
    token = sign_identity(
        bytes(range(32)),
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        scopes=("memory.read",),
        timeout_s=300,
        now=1700000000,
    )
    assert verify_identity(token, bytes(range(32)), now=1700000000).user_id == (
        "e2e-run-abc-e2e_memory"
    )


def test_signer_accepts_the_full_safe_ascii_claim_charset():
    token = sign_identity(
        bytes(range(32)),
        run_id="e2e-run_1.2:3",
        user_id="e2e-run_1.2:3-e2e_memory",
        vehicle_id="VIN:ABC_1.2-3",
        scopes=("scope:read_write-1.2",),
        timeout_s=300,
        now=1700000000,
    )
    claims = verify_identity(token, bytes(range(32)), now=1700000000)
    assert claims.run_id == "e2e-run_1.2:3"
    assert claims.scopes == ("scope:read_write-1.2",)


@pytest.mark.parametrize(
    ("run_id", "user_id", "vehicle_id", "scopes"),
    [
        ("e2e-run\u2028abc", "e2e-run\u2028abc-e2e_memory", "v1", ("memory.read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e\u2029memory", "v1", ("memory.read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "<v&>", ("memory.read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "v\U0001f600", ("memory.read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "v1", ("memory.\x01read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "v1", ("memory read",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "v1", ("",)),
        ("e2e-run-abc", "e2e-run-abc-e2e_memory", "v1", ()),
    ],
)
def test_signer_rejects_claims_outside_the_safe_ascii_contract(
    run_id,
    user_id,
    vehicle_id,
    scopes,
):
    with pytest.raises(IdentityTokenError):
        sign_identity(
            bytes(range(32)),
            run_id=run_id,
            user_id=user_id,
            vehicle_id=vehicle_id,
            scopes=scopes,
            timeout_s=300,
            now=1700000000,
        )


def test_owner_proof_requires_signed_ack_as_first_websocket_frame():
    prove = getattr(
        __import__("scripts.e2e_identity", fromlist=["prove_identity_owner"]),
        "prove_identity_owner",
        None,
    )
    assert prove is not None, "runner owner proof handshake is missing"
    seen = {}

    class FakeSocket:
        async def recv(self):
            return json.dumps({
                "type": "e2e_identity_ack",
                "run_id": "e2e-run-abc",
                "user_id": "e2e-run-abc-e2e_memory",
                "vehicle_id": "v1",
            })

    class FakeContext:
        async def __aenter__(self):
            return FakeSocket()

        async def __aexit__(self, *_args):
            return False

    def connect(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return FakeContext()

    prove(
        ws_base="ws://127.0.0.1:8090/ws",
        token="e2e.v1.payload.signature",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        connect=connect,
    )
    assert "token=e2e.v1.payload.signature" in seen["url"]
    assert seen["kwargs"]["open_timeout"] <= 5


def test_runner_exports_memory_capability_signer_and_secret_free_public_parser():
    identity = __import__("scripts.e2e_identity", fromlist=["*"])
    signer = getattr(identity, "sign_memory_extraction_session", None)
    parser = getattr(identity, "parse_memory_extraction_session", None)
    assert signer is not None, "runner memory capability signer is missing"
    assert parser is not None, "secret-free memory capability parser is missing"

    token = signer(
        bytes(range(32)),
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        session_id="e2e-run-abc-e2e_memory-session-1",
        timeout_s=300,
        now=1_700_000_000,
    )
    claims = parser(token, now=1_700_000_419)
    assert claims.to_dict()["session_id"].endswith("-session-1")
    with pytest.raises(IdentityTokenError, match="expired"):
        parser(token, now=1_700_000_420)


def test_support_deprecated_memory_session_alias_returns_capability_not_session():
    helper = getattr(support_e2e, "memory_session_id", None)
    assert helper is not None, "E2E memory session helper is missing"
    signer = __import__(
        "scripts.e2e_identity",
        fromlist=["sign_memory_extraction_session"],
    ).sign_memory_extraction_session
    secret = bytes(range(32))
    base = {
        "E2E_RUN_ID": "e2e-run-abc",
        "E2E_TEST_ID": "e2e_memory",
        "E2E_USER_ID": "e2e-run-abc-e2e_memory",
        "E2E_SESSION_PREFIX": "e2e-run-abc-e2e_memory-session",
    }
    sessions = [
        signer(
            secret,
            run_id=base["E2E_RUN_ID"],
            user_id=base["E2E_USER_ID"],
            session_id=f"{base['E2E_SESSION_PREFIX']}-{number}",
            timeout_s=300,
        )
        for number in (1, 2)
    ]
    env = {
        **base,
        "E2E_MEMORY_SESSION_IDS": json.dumps(sessions, separators=(",", ":")),
    }

    with pytest.deprecated_call(match="returns a capability, not a business session"):
        assert helper(1, env=env) == sessions[0]
    with pytest.deprecated_call(match="returns a capability, not a business session"):
        assert helper(2, env=env) == sessions[1]
    for number in (0, 3, True):
        with pytest.deprecated_call(match="returns a capability, not a business session"):
            with pytest.raises(support_e2e.ProtocolError):
                helper(number, env=env)
    assert "E2E_CAPABILITY_SECRET" not in env


def test_runner_atomically_rewrites_bundle_with_only_presigned_memory_sessions(
    tmp_path,
):
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    presign = getattr(runner, "_presign_memory_bundle", None)
    assert presign is not None, "runner bundle pre-signer is missing"
    stack_lease = __import__("scripts.e2e_stack_lease", fromlist=["*"])
    secret = bytes(range(32))
    bundle = stack_lease.write_token_bundle(
        root=tmp_path,
        lease_id="lease-test",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=300,
        secret=secret,
        now=1_700_000_000,
        memory_sessions=2,
    )

    presign(
        bundle,
        expected_bundle_root=tmp_path,
        secret=secret,
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        timeout_s=300,
        memory_sessions=2,
        now=1_700_000_000,
    )

    payload = json.loads(bundle.read_text(encoding="utf-8"))
    assert len(payload["memory_session_ids"]) == 2
    assert all(
        token.startswith("e2e-mem.v1.")
        for token in payload["memory_session_ids"]
    )
    assert "secret" not in json.dumps(payload).lower()
    assert not list(bundle.parent.glob(".memory-capability-*.tmp"))


@pytest.mark.parametrize("duplicate_key", ["identity_token", "memory_session_ids"])
def test_owner_presign_rejects_duplicate_sensitive_bundle_keys(
    tmp_path,
    duplicate_key,
):
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    stack_lease = __import__("scripts.e2e_stack_lease", fromlist=["*"])
    bundle = stack_lease.write_token_bundle(
        root=tmp_path,
        lease_id="lease-owner-duplicate",
        case_id="e2e_memory",
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        vehicle_id="v1",
        timeout_s=300,
        secret=bytes(range(32)),
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

    with pytest.raises(runner.StackLeaseProtocolError, match="duplicate"):
        runner._presign_memory_bundle(
            bundle,
            expected_bundle_root=tmp_path,
            secret=bytes(range(32)),
            run_id="e2e-run-abc",
            user_id="e2e-run-abc-e2e_memory",
            timeout_s=300,
            memory_sessions=1,
        )


def test_owner_presign_requires_root_and_explicit_loader_for_missing_fake_bundle(
    tmp_path,
):
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    missing = (
        tmp_path
        / "lease-owner-fake-e2e_memory"
        / "tokens.json"
    ).resolve()
    with pytest.raises(runner.StackLeaseProtocolError):
        runner._presign_memory_bundle(
            missing,
            expected_bundle_root=tmp_path,
            secret=bytes(range(32)),
            run_id="e2e-run-abc",
            user_id="e2e-run-abc-e2e_memory",
            timeout_s=300,
            memory_sessions=1,
        )

    missing.parent.mkdir()
    if os.name != "nt":
        # `replace_private_file` 头一件事就是校验父目录必须是 **0o700**，而
        # `mkdir()` 建出来的是 0o755。Windows 上 `_verify_private_posix_path` 整段
        # return，所以这条用例在 Windows 上跑的是**另一条路径**——它从来没有真正
        # 执行过「私有目录权限」这一层，到 Linux CI 上就直接
        # `memory capability bundle rewrite failed`。
        # 与 `test_e2e_stack_lease.py::_private_dir` 是同一课，只是那边已经收口、
        # 这边漏了一处。
        os.chmod(missing.parent, 0o700)
    payload = {
        "schema_version": 1,
        "lease_id": "lease-owner-fake",
        "case_id": "e2e_memory",
        "run_id": "e2e-run-abc",
        "user_id": "e2e-run-abc-e2e_memory",
        "vehicle_id": "v1",
        "identity_token": "e2e.v1.identity.signature",
        "control_user_id": "e2e-run-abc-e2e_memory-control",
        "control_identity_token": "e2e.v1.control.signature",
        "voiceprint_fixture": None,
        "memory_session_ids": ["e2e-run-abc-e2e_memory-session-1"],
    }
    calls = []

    def fake_owner_loader(path, **kwargs):
        calls.append((path, kwargs))
        return dict(payload)

    signed = runner._presign_memory_bundle(
        missing,
        expected_bundle_root=tmp_path,
        owner_bundle_loader=fake_owner_loader,
        secret=bytes(range(32)),
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        timeout_s=300,
        memory_sessions=1,
    )
    assert calls and signed.is_file()
    written = json.loads(signed.read_text(encoding="utf-8"))
    assert written["memory_session_ids"][0].startswith("e2e-mem.v1.")


def test_root_lease_uses_one_secret_for_identity_and_capability_and_restores_off(
    tmp_path,
    monkeypatch,
):
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    factory = getattr(runner, "_identity_capability_lease", None)
    assert factory is not None, "shared identity/capability lease factory is missing"
    process_env = {}
    compose_calls = []

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.secret_factory = lambda: b"wrong"
            self.secret = b""
            self.compose = lambda env: compose_calls.append(dict(env))

        def enable(self):
            self.secret = self.secret_factory()
            self.environ.update({
                "E2E_IDENTITY_ENABLED": "true",
                "E2E_IDENTITY_SECRET": runner.encode_secret(self.secret),
            })
            self.compose(self.environ)

        def restore(self):
            self.environ["E2E_IDENTITY_ENABLED"] = "false"
            self.environ.pop("E2E_IDENTITY_SECRET", None)
            self.compose(self.environ)

    monkeypatch.setattr(runner, "generate_secret", lambda: bytes(range(32)))
    lease = factory(
        repo_root=tmp_path,
        environ=process_env,
        lease_factory=FakeLease,
    )
    lease.enable()
    assert lease.secret == bytes(range(32))
    assert compose_calls[0]["E2E_IDENTITY_SECRET"] == (
        compose_calls[0]["E2E_CAPABILITY_SECRET"]
    )
    lease.restore()
    assert compose_calls[1]["E2E_IDENTITY_ENABLED"] == "false"
    assert compose_calls[1]["E2E_CAPABILITY_ENABLED"] == "false"
    assert "E2E_CAPABILITY_SECRET" not in compose_calls[1]


def test_profile_compose_enables_capability_only_for_extraction_epoch():
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    wrapper = getattr(runner, "_profile_compose_with_capability", None)
    assert wrapper is not None, "profile capability compose wrapper is missing"
    enabled = {"value": True}
    calls = []
    invoke = wrapper(
        lambda argv, env: calls.append((tuple(argv), dict(env))),
        secret=bytes(range(32)),
        enabled=lambda: enabled["value"],
    )

    invoke(("docker", "compose", "up", "edge-gateway"), {"KEEP": "yes"})
    command, env = calls[-1]
    assert command[-1] == "memory"
    assert env["E2E_CAPABILITY_ENABLED"] == "true"
    assert env["E2E_CAPABILITY_SECRET"] == runner.encode_secret(bytes(range(32)))

    enabled["value"] = False
    invoke(("docker", "compose", "up"), {"E2E_CAPABILITY_SECRET": "stale"})
    command, env = calls[-1]
    assert command[-1] == "memory"
    assert env["E2E_CAPABILITY_ENABLED"] == "false"
    assert "E2E_CAPABILITY_SECRET" not in env

    invoke(("docker", "compose", "up"), {})
    command, env = calls[-1]
    assert "memory" not in command
    assert env["E2E_CAPABILITY_ENABLED"] == "false"

    enabled["value"] = True
    invoke(("docker", "compose", "up"), {})
    command, env = calls[-1]
    assert command[-1] == "memory"
    assert env["E2E_CAPABILITY_ENABLED"] == "true"


def test_profile_compose_rebuilds_only_selected_admin_services_on_and_off():
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    enabled = {"value": False}
    admin = {"services": ("proactive",)}
    calls = []
    invoke = runner._profile_compose_with_capability(
        lambda argv, env: calls.append((tuple(argv), dict(env))),
        secret=bytes(range(32)),
        enabled=lambda: enabled["value"],
        admin_services=lambda: admin["services"],
    )

    invoke(("docker", "compose", "up", "edge-gateway"), {})
    command, env = calls[-1]
    assert command[-1] == "proactive"
    assert env["E2E_NAMESPACE_ADMIN_ENABLED"] == "true"
    assert env["E2E_NAMESPACE_ADMIN_SECRET"] == runner.encode_secret(
        bytes(range(32)),
    )

    admin["services"] = ()
    invoke(("docker", "compose", "up"), {
        "E2E_NAMESPACE_ADMIN_SECRET": "stale",
    })
    command, env = calls[-1]
    assert command[-1] == "proactive"
    assert env["E2E_NAMESPACE_ADMIN_ENABLED"] == "false"
    assert "E2E_NAMESPACE_ADMIN_SECRET" not in env

    invoke(("docker", "compose", "up"), {})
    command, env = calls[-1]
    assert "proactive" not in command
    assert env["E2E_NAMESPACE_ADMIN_ENABLED"] == "false"


def test_namespace_admin_services_are_selected_only_by_runner_case_mapping():
    runner = __import__("scripts.run_e2e", fromlist=["*"])

    class Case:
        def __init__(self, case_id):
            self.id = case_id

    assert runner._namespace_admin_services([
        Case("e2e_mcp"),
        Case("e2e_memory"),
        Case("e2e_proactive"),
        Case("e2e_mcp"),
    ]) == ("proactive", "mcp-bridge")
    assert runner._namespace_admin_services([Case("e2e_memory")]) == ()


def test_runner_resolves_memory_capability_count_by_selected_lane():
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    contract = __import__("scripts.e2e_contract", fromlist=["*"])
    manifest = contract.load_manifest(
        ROOT / "test" / "e2e_manifest.yaml",
        repo_root=ROOT,
    )
    case = manifest.by_id["e2e_memory"]

    assert runner._memory_sessions_for(case, "milestone") == 1
    assert runner._memory_sessions_for(case, "nightly") == 0
    assert runner._memory_sessions_for(case, None) == 1


def test_root_lease_can_keep_memory_capability_off_when_all_cases_request_zero(
    tmp_path,
    monkeypatch,
):
    runner = __import__("scripts.run_e2e", fromlist=["*"])
    process_env = {}
    compose_calls = []

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.secret_factory = lambda: b"wrong"
            self.secret = b""
            self.compose = lambda env: compose_calls.append(dict(env))

        def enable(self):
            self.secret = self.secret_factory()
            self.environ.update({
                "E2E_IDENTITY_ENABLED": "true",
                "E2E_IDENTITY_SECRET": runner.encode_secret(self.secret),
            })
            self.compose(self.environ)

    monkeypatch.setattr(runner, "generate_secret", lambda: bytes(range(32)))
    lease = runner._identity_capability_lease(
        repo_root=tmp_path,
        environ=process_env,
        capability_enabled=False,
        lease_factory=FakeLease,
    )
    lease.enable()

    assert compose_calls[0]["E2E_CAPABILITY_ENABLED"] == "false"
    assert "E2E_CAPABILITY_SECRET" not in compose_calls[0]
    assert process_env["E2E_CAPABILITY_ENABLED"] == "false"
    assert "E2E_CAPABILITY_SECRET" not in process_env

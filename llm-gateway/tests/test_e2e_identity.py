from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


GATEWAY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GATEWAY_ROOT.parent
sys.path.insert(0, os.fspath(GATEWAY_ROOT))

try:
    import e2e_identity as identity_module  # noqa: E402
    IdentityTokenError = identity_module.IdentityTokenError
    decode_secret = identity_module.decode_secret
    verify_identity = identity_module.verify_identity
except ModuleNotFoundError:
    identity_module = None
    IdentityTokenError = ValueError
    decode_secret = verify_identity = None


VECTORS = json.loads(
    (REPO_ROOT / "test" / "fixtures" / "e2e_identity_vectors.json").read_text(
        encoding="utf-8",
    ),
)


def test_llm_gateway_verifier_accepts_and_rejects_shared_vectors():
    assert verify_identity is not None, "llm-gateway identity verifier is missing"
    secret = decode_secret(VECTORS["secret_b64url"])
    for vector in VECTORS["vectors"]:
        if vector["valid"]:
            claims = verify_identity(
                vector["token"],
                secret,
                now=VECTORS["now"],
            )
            assert claims.to_dict() == vector["claims"], vector["name"]
        else:
            with pytest.raises(IdentityTokenError):
                verify_identity(
                    vector["token"],
                    secret,
                    now=VECTORS["now"],
                )


def test_fake_clock_uses_exp_minus_iat_not_exp_minus_now():
    assert verify_identity is not None, "llm-gateway identity verifier is missing"
    secret = decode_secret(VECTORS["secret_b64url"])
    token = next(
        vector["token"]
        for vector in VECTORS["vectors"]
        if vector["name"] == "ttl_1920"
    )
    assert verify_identity(token, secret, now=VECTORS["now"] + 119).run_id
    with pytest.raises(IdentityTokenError, match="expired"):
        verify_identity(token, secret, now=1700000120)


def test_s2s_gate_off_preserves_client_identity_verbatim():
    resolver = getattr(identity_module, "resolve_s2s_identity", None)
    assert resolver is not None, "S2S identity gate resolver is missing"
    user, vehicle, claims = resolver(
        {"user_id": "production-user", "vehicle_id": "production-vehicle"},
        environ={"E2E_IDENTITY_ENABLED": "false"},
        now=VECTORS["now"],
    )
    assert (user, vehicle, claims) == (
        "production-user",
        "production-vehicle",
        None,
    )


def test_s2s_session_binding_uses_shared_vectors():
    validator = getattr(identity_module, "validate_e2e_session_id", None)
    assert validator is not None, "S2S session binding validator is missing"
    for vector in VECTORS["session_vectors"]:
        if vector["valid"]:
            assert validator(vector["session_id"], vector["user_id"]) is None
        else:
            with pytest.raises(IdentityTokenError):
                validator(vector["session_id"], vector["user_id"])


def test_s2s_gate_on_requires_matching_token_and_overrides_from_claims():
    resolver = getattr(identity_module, "resolve_s2s_identity", None)
    assert resolver is not None, "S2S identity gate resolver is missing"
    valid = next(v for v in VECTORS["vectors"] if v["name"] == "valid")
    environ = {
        "E2E_IDENTITY_ENABLED": "true",
        "E2E_IDENTITY_SECRET": VECTORS["secret_b64url"],
    }
    user, vehicle, claims = resolver(
        {
            "user_id": valid["claims"]["user_id"],
            "vehicle_id": "client-spoofed-vehicle",
            "identity_token": valid["token"],
            "session_id": valid["claims"]["user_id"] + "-session-1",
        },
        environ=environ,
        now=VECTORS["now"],
    )
    assert user == valid["claims"]["user_id"]
    assert vehicle == valid["claims"]["vehicle_id"]
    assert claims.to_dict() == valid["claims"]


@pytest.mark.parametrize(
    "start",
    [
        {"user_id": "e2e-run-abc-e2e-memory"},
        {
            "user_id": "e2e-run-abc-e2e-voiceprint",
            "identity_token": "VALID",
        },
        {
            "user_id": "e2e-run-abc-e2e-memory",
            "identity_token": "VALID",
            "session_id": "e2e-run-abc-e2e-voiceprint-session-1",
        },
    ],
)
def test_s2s_gate_on_rejects_missing_or_cross_user_token(start):
    resolver = getattr(identity_module, "resolve_s2s_identity", None)
    assert resolver is not None, "S2S identity gate resolver is missing"
    valid = next(v for v in VECTORS["vectors"] if v["name"] == "valid")
    payload = dict(start)
    if payload.get("identity_token") == "VALID":
        payload["identity_token"] = valid["token"]
    with pytest.raises(IdentityTokenError):
        resolver(
            payload,
            environ={
                "E2E_IDENTITY_ENABLED": "true",
                "E2E_IDENTITY_SECRET": VECTORS["secret_b64url"],
            },
            now=VECTORS["now"],
        )


# ── Android N-04（2026-09-24）：开关开着时，不带签名的普通会话不许被拒 ──────────────────────────
# 云端 `E2E_IDENTITY_ENABLED=true`（W19-c）之后真 App 的端到端挡位全部 1008：开场帧从不带 token。
# 设计（M-A 签名身份一节）与 Edge WS（`gateway/edge/auth.go::resolveSession`）的口径：只有**带了**签名 token
# 才验签；「gate 开启时只有裸 user_id 不构成测试身份」——自称测试命名空间却没签名的照旧拒。

_GATE_ON = {"E2E_IDENTITY_ENABLED": "true", "E2E_IDENTITY_SECRET": VECTORS["secret_b64url"]}


@pytest.mark.parametrize("start", [
    {"user_id": "production-user", "vehicle_id": "production-vehicle", "session_id": "app-7f3a"},
    {"user_id": "production-user", "vehicle_id": "production-vehicle", "session_id": "app-7f3a",
     "identity_token": ""},
])
def test_s2s_gate_on_keeps_an_unsigned_production_session(start):
    user, vehicle, claims = identity_module.resolve_s2s_identity(
        start, environ=_GATE_ON, now=VECTORS["now"])
    assert (user, vehicle, claims) == ("production-user", "production-vehicle", None)


def test_s2s_gate_on_without_a_secret_still_keeps_an_unsigned_session():
    """密钥缺失只影响带 token 的会话（同 Edge WS：配置错误只在认出前缀之后才硬拒）。"""
    user, _, claims = identity_module.resolve_s2s_identity(
        {"user_id": "production-user", "session_id": "app-7f3a"},
        environ={"E2E_IDENTITY_ENABLED": "true"}, now=VECTORS["now"])
    assert (user, claims) == ("production-user", None)


@pytest.mark.parametrize("start", [
    {"user_id": "e2e-run-abc-e2e-memory", "session_id": "app-7f3a"},          # 自称测试用户
    {"user_id": "production-user", "session_id": "e2e-run-abc-e2e-memory-session-1"},  # 借测试会话
])
def test_s2s_gate_on_rejects_an_unsigned_claim_on_the_test_namespace(start):
    with pytest.raises(IdentityTokenError):
        identity_module.resolve_s2s_identity(start, environ=_GATE_ON, now=VECTORS["now"])


def test_s2s_gate_on_rejects_a_supplied_token_even_without_a_secret():
    """带了 token 就必须验得过：密钥缺失 ⇒ 拒，不回落客户端身份。"""
    valid = next(v for v in VECTORS["vectors"] if v["name"] == "valid")
    with pytest.raises(IdentityTokenError):
        identity_module.resolve_s2s_identity(
            {"user_id": valid["claims"]["user_id"], "identity_token": valid["token"],
             "session_id": valid["claims"]["user_id"] + "-session-1"},
            environ={"E2E_IDENTITY_ENABLED": "true"}, now=VECTORS["now"])

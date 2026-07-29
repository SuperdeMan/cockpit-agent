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

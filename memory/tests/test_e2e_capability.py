from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

try:
    from memory import e2e_capability
except ImportError:
    e2e_capability = None


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _raw_token(payload: bytes, *, domain: bytes = b"e2emem.v1.") -> str:
    segment = _b64url(payload)
    signature = hmac.new(
        bytes(range(32)),
        domain + segment.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"e2e-mem.v1.{segment}.{_b64url(signature)}"


def test_memory_capability_round_trip_has_independent_domain_and_expiry_boundary():
    assert e2e_capability is not None, "memory capability module is missing"
    secret = bytes(range(32))
    token = e2e_capability.sign_memory_capability(
        secret,
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        session_id="e2e-run-abc-e2e_memory-session-1",
        timeout_s=300,
        now=1_700_000_000,
    )

    assert token.startswith("e2e-mem.v1.")
    claims = e2e_capability.verify_memory_capability(
        token,
        secret,
        now=1_700_000_419,
    )
    assert claims.to_dict() == {
        "run_id": "e2e-run-abc",
        "user_id": "e2e-run-abc-e2e_memory",
        "session_id": "e2e-run-abc-e2e_memory-session-1",
        "capability": "memory_extraction",
        "exp": 1_700_000_420,
    }

    try:
        e2e_capability.verify_memory_capability(
            token,
            secret,
            now=1_700_000_420,
        )
    except e2e_capability.MemoryCapabilityError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("capability must be expired exactly at exp")


def test_memory_capability_rejects_tamper_wrong_binding_and_wrong_domain():
    secret = bytes(range(32))
    token = e2e_capability.sign_memory_capability(
        secret,
        run_id="e2e-run-abc",
        user_id="e2e-run-abc-e2e_memory",
        session_id="e2e-run-abc-e2e_memory-session-1",
        timeout_s=300,
        now=1_700_000_000,
    )
    parts = token.split(".")
    tampered_payload = ".".join(
        [parts[0], parts[1], parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B"), parts[3]],
    )
    tampered_signature = token[:-1] + ("A" if token[-1] != "A" else "B")

    wrong_user = _raw_token(
        b'{"run_id":"e2e-run-abc","user_id":"e2e-run-abc-other",'
        b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
        b'"capability":"memory_extraction","exp":1700000420}',
    )
    wrong_run = _raw_token(
        b'{"run_id":"e2e-run-other","user_id":"e2e-run-abc-e2e_memory",'
        b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
        b'"capability":"memory_extraction","exp":1700000420}',
    )
    wrong_session = _raw_token(
        b'{"run_id":"e2e-run-abc","user_id":"e2e-run-abc-e2e_memory",'
        b'"session_id":"e2e-run-abc-other-session-1",'
        b'"capability":"memory_extraction","exp":1700000420}',
    )
    identity_domain = _raw_token(
        b'{"run_id":"e2e-run-abc","user_id":"e2e-run-abc-e2e_memory",'
        b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
        b'"capability":"memory_extraction","exp":1700000420}',
        domain=b"e2e.v1.",
    )

    for invalid in (
        tampered_payload,
        tampered_signature,
        wrong_user,
        wrong_run,
        wrong_session,
        identity_domain,
    ):
        with pytest.raises(e2e_capability.MemoryCapabilityError):
            e2e_capability.verify_memory_capability(
                invalid,
                secret,
                now=1_700_000_000,
            )


@pytest.mark.parametrize(
    "payload",
    [
        (
            b'{"run_id":"e2e-run-abc","run_id":"e2e-run-other",'
            b'"user_id":"e2e-run-abc-e2e_memory",'
            b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
            b'"capability":"memory_extraction","exp":1700000420}'
        ),
        (
            b'{"capability":"memory_extraction","exp":1700000420,'
            b'"run_id":"e2e-run-abc",'
            b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
            b'"user_id":"e2e-run-abc-e2e_memory"}'
        ),
        (
            b'{"run_id":"e2e-run-abc","user_id":"e2e-run-abc-e2e_memory",'
            b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
            b'"capability":"memory_extraction","exp":true}'
        ),
        (
            '{"run_id":"e2e-run-abc","user_id":"e2e-run-abc-e2e_memory",'
            '"session_id":"e2e-run-abc-e2e_memory-session-1",'
            '"capability":"memory_extraction","exp":1700000420,'
            '"note":"不安全"}'
        ).encode("utf-8"),
        b"{" + b'"padding":"' + b"x" * 2050 + b'"}',
    ],
)
def test_memory_capability_rejects_duplicate_noncanonical_typed_nonascii_or_oversize_payload(
    payload,
):
    with pytest.raises(e2e_capability.MemoryCapabilityError):
        e2e_capability.verify_memory_capability(
            _raw_token(payload),
            bytes(range(32)),
            now=1_700_000_000,
        )


@pytest.mark.parametrize("secret", [b"x" * 31, b"x" * 33, "x" * 32])
def test_memory_capability_requires_exactly_32_secret_bytes(secret):
    with pytest.raises(
        e2e_capability.MemoryCapabilityError,
        match="exactly 32 bytes",
    ):
        e2e_capability.sign_memory_capability(
            secret,
            run_id="e2e-run-abc",
            user_id="e2e-run-abc-e2e_memory",
            session_id="e2e-run-abc-e2e_memory-session-1",
            timeout_s=300,
            now=1_700_000_000,
        )


def test_memory_capability_rejects_an_oversize_but_otherwise_valid_claim_set():
    run_id = "e2e-" + "r" * 900
    user_id = run_id + "-user"
    session_id = user_id + "-session-1"
    payload = (
        '{"run_id":"%s","user_id":"%s","session_id":"%s",'
        '"capability":"memory_extraction","exp":1700000420}'
        % (run_id, user_id, session_id)
    ).encode("ascii")
    assert len(payload) > 2048

    with pytest.raises(
        e2e_capability.MemoryCapabilityError,
        match="too large",
    ):
        e2e_capability.verify_memory_capability(
            _raw_token(payload),
            bytes(range(32)),
            now=1_700_000_000,
        )


def test_memory_capability_requires_exact_run_derived_from_runner_user():
    """A shorter run prefix must not own another runner's exact case user."""

    prefix_collision = _raw_token(
        b'{"run_id":"e2e-run","user_id":"e2e-run-abc-e2e_memory",'
        b'"session_id":"e2e-run-abc-e2e_memory-session-1",'
        b'"capability":"memory_extraction","exp":1700000420}',
    )
    with pytest.raises(
        e2e_capability.MemoryCapabilityError,
        match="run namespace",
    ):
        e2e_capability.verify_memory_capability(
            prefix_collision,
            bytes(range(32)),
            now=1_700_000_000,
        )

    for ambiguous_run in ("e2e-run-e2e_nested",):
        with pytest.raises(
            e2e_capability.MemoryCapabilityError,
            match="run namespace",
        ):
            e2e_capability.sign_memory_capability(
                bytes(range(32)),
                run_id=ambiguous_run,
                user_id=f"{ambiguous_run}-e2e_memory",
                session_id=f"{ambiguous_run}-e2e_memory-session-1",
                timeout_s=300,
                now=1_700_000_000,
            )

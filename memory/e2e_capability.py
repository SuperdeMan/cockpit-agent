"""Short-lived bearer capabilities for synthetic E2E memory extraction."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any


TOKEN_PREFIX = "e2e-mem.v1."
SIGNING_DOMAIN = b"e2emem.v1."
CAPABILITY = "memory_extraction"
MAX_TIMEOUT_S = 1800
GRACE_S = 120
MAX_TTL_S = MAX_TIMEOUT_S + GRACE_S
MAX_TOKEN_BYTES = 4096
MAX_PAYLOAD_BYTES = 2048
_CLAIM_KEYS = ("run_id", "user_id", "session_id", "capability", "exp")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_SAFE_CLAIM_RE = re.compile(r"[A-Za-z0-9._:-]+\Z")
_RUN_RE = re.compile(r"e2e-[A-Za-z0-9._:-]+\Z")
_CASE_RE = re.compile(r"e2e_[a-z0-9_]+\Z")
_RUN_CASE_DELIMITER = "-e2e_"


class MemoryCapabilityError(ValueError):
    """The capability secret, claim set, token, or clock is invalid."""


@dataclass(frozen=True)
class MemoryCapabilityClaims:
    run_id: str
    user_id: str
    session_id: str
    capability: str
    exp: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "capability": self.capability,
            "exp": self.exp,
        }


def _require_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise MemoryCapabilityError(
            "E2E capability secret must be exactly 32 bytes",
        )


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    if not isinstance(value, str) or _B64URL_RE.fullmatch(value) is None:
        raise MemoryCapabilityError("malformed base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise MemoryCapabilityError("malformed base64url") from exc
    if _encode_base64url(decoded) != value:
        raise MemoryCapabilityError("non-canonical base64url")
    return decoded


def encode_capability_secret(secret: bytes) -> str:
    _require_secret(secret)
    return _encode_base64url(secret)


def decode_capability_secret(value: str) -> bytes:
    secret = _decode_base64url(value)
    _require_secret(secret)
    return secret


def derive_runner_run_id(user_id: str) -> str:
    """Recover the exact runner run from ``{run_id}-{e2e_case_id}``."""

    if not isinstance(user_id, str) or _SAFE_CLAIM_RE.fullmatch(user_id) is None:
        raise MemoryCapabilityError("memory capability run namespace is invalid")
    boundary = user_id.rfind(_RUN_CASE_DELIMITER)
    if boundary <= 0:
        raise MemoryCapabilityError("memory capability run namespace is invalid")
    run_id = user_id[:boundary]
    case_id = user_id[boundary + 1:]
    if (
        _RUN_CASE_DELIMITER in run_id
        or _RUN_RE.fullmatch(run_id) is None
        or _CASE_RE.fullmatch(case_id) is None
        or user_id != f"{run_id}-{case_id}"
    ):
        raise MemoryCapabilityError("memory capability run namespace is invalid")
    return run_id


def _normalize_claims(value: Any) -> MemoryCapabilityClaims:
    if not isinstance(value, dict) or tuple(value) != _CLAIM_KEYS:
        raise MemoryCapabilityError(
            "memory capability claims are missing, extra, or non-canonical",
        )
    run_id = value["run_id"]
    user_id = value["user_id"]
    session_id = value["session_id"]
    capability = value["capability"]
    exp = value["exp"]
    if (
        not isinstance(run_id, str)
        or _RUN_RE.fullmatch(run_id) is None
        or _RUN_CASE_DELIMITER in run_id
    ):
        raise MemoryCapabilityError("memory capability run namespace is invalid")
    if (
        not isinstance(user_id, str)
        or _SAFE_CLAIM_RE.fullmatch(user_id) is None
    ):
        raise MemoryCapabilityError("memory capability user namespace is invalid")
    if derive_runner_run_id(user_id) != run_id:
        raise MemoryCapabilityError("memory capability run namespace is invalid")
    if (
        not isinstance(session_id, str)
        or session_id != f"{user_id}-session-{_session_number(session_id)}"
    ):
        raise MemoryCapabilityError(
            "memory capability session namespace is invalid",
        )
    if capability != CAPABILITY:
        raise MemoryCapabilityError("memory capability kind is invalid")
    if type(exp) is not int:
        raise MemoryCapabilityError("memory capability expiry is invalid")
    return MemoryCapabilityClaims(
        run_id,
        user_id,
        session_id,
        capability,
        exp,
    )


def _session_number(session_id: str) -> int:
    match = re.fullmatch(r".+-session-([1-9][0-9]*)", session_id)
    if match is None:
        raise MemoryCapabilityError(
            "memory capability session namespace is invalid",
        )
    return int(match.group(1))


def _canonical_payload(claims: MemoryCapabilityClaims) -> bytes:
    return json.dumps(
        claims.to_dict(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _current_time(now: int | None) -> int:
    current = int(time.time()) if now is None else now
    if type(current) is not int:
        raise MemoryCapabilityError(
            "memory capability clock must return an integer",
        )
    return current


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MemoryCapabilityError(
                "memory capability payload has duplicate JSON keys",
            )
        value[key] = item
    return value


def _decode_token(token: str) -> tuple[str, bytes, bytes]:
    if not isinstance(token, str):
        raise MemoryCapabilityError("memory capability token must be text")
    if len(token.encode("utf-8", errors="ignore")) > MAX_TOKEN_BYTES:
        raise MemoryCapabilityError("memory capability token is too large")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "e2e-mem" or parts[1] != "v1":
        raise MemoryCapabilityError("memory capability version is invalid")
    payload_segment, signature_segment = parts[2], parts[3]
    payload_bytes = _decode_base64url(payload_segment)
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise MemoryCapabilityError("memory capability payload is too large")
    signature = _decode_base64url(signature_segment)
    if len(signature) != hashlib.sha256().digest_size:
        raise MemoryCapabilityError("memory capability signature is invalid")
    return payload_segment, payload_bytes, signature


def _claims_from_payload(payload_bytes: bytes) -> MemoryCapabilityClaims:
    try:
        raw = json.loads(
            payload_bytes,
            object_pairs_hook=_strict_json_object,
        )
    except MemoryCapabilityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryCapabilityError(
            "memory capability payload is invalid JSON",
        ) from exc
    claims = _normalize_claims(raw)
    if _canonical_payload(claims) != payload_bytes:
        raise MemoryCapabilityError(
            "memory capability payload is not canonical JSON",
        )
    return claims


def _validate_clock(claims: MemoryCapabilityClaims, current: int) -> None:
    if current >= claims.exp:
        raise MemoryCapabilityError("memory capability expired")
    if claims.exp - current > MAX_TTL_S:
        raise MemoryCapabilityError("memory capability ttl exceeds 1920 seconds")


def sign_memory_capability(
    secret: bytes,
    *,
    run_id: str,
    user_id: str,
    session_id: str,
    timeout_s: int,
    now: int | None = None,
) -> str:
    """Sign a capability bound to one business session ID.

    The returned token belongs in ``e2e_memory_capability``; it must never
    replace ``AppendTurnRequest.session_id``.
    """

    _require_secret(secret)
    if type(timeout_s) is not int or not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise MemoryCapabilityError(
            "memory capability timeout must be within 1..1800 seconds",
        )
    current = _current_time(now)
    claims = _normalize_claims({
        "run_id": run_id,
        "user_id": user_id,
        "session_id": session_id,
        "capability": CAPABILITY,
        "exp": current + timeout_s + GRACE_S,
    })
    payload_bytes = _canonical_payload(claims)
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise MemoryCapabilityError("memory capability payload is too large")
    payload_segment = _encode_base64url(payload_bytes)
    signature = hmac.new(
        secret,
        SIGNING_DOMAIN + payload_segment.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return TOKEN_PREFIX + payload_segment + "." + _encode_base64url(signature)


def parse_memory_capability(
    token: str,
    *,
    now: int | None = None,
) -> MemoryCapabilityClaims:
    """Validate public token structure and binding without proving its HMAC."""

    _payload_segment, payload_bytes, _signature = _decode_token(token)
    claims = _claims_from_payload(payload_bytes)
    _validate_clock(claims, _current_time(now))
    return claims


def verify_memory_capability(
    token: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> MemoryCapabilityClaims:
    _require_secret(secret)
    payload_segment, payload_bytes, signature = _decode_token(token)
    expected = hmac.new(
        secret,
        SIGNING_DOMAIN + payload_segment.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise MemoryCapabilityError("memory capability signature is invalid")
    claims = _claims_from_payload(payload_bytes)
    current = _current_time(now)
    _validate_clock(claims, current)
    return claims

"""Verification of runner-issued E2E identity tokens at the S2S boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


PREFIX = "e2e.v1."
MAX_TTL_S = 1920
MAX_FUTURE_IAT_S = 5
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_SAFE_CLAIM_RE = re.compile(r"[A-Za-z0-9._:-]+\Z")
_RUN_RE = re.compile(r"e2e-[A-Za-z0-9._:-]+\Z")
_CLAIM_KEYS = ("run_id", "user_id", "vehicle_id", "scopes", "iat", "exp")


class IdentityTokenError(ValueError):
    """A supplied E2E identity cannot be trusted."""


@dataclass(frozen=True)
class IdentityClaims:
    run_id: str
    user_id: str
    vehicle_id: str
    scopes: tuple[str, ...]
    iat: int
    exp: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "vehicle_id": self.vehicle_id,
            "scopes": list(self.scopes),
            "iat": self.iat,
            "exp": self.exp,
        }


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or _B64URL_RE.fullmatch(value) is None:
        raise IdentityTokenError("malformed base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise IdentityTokenError("malformed base64url") from exc
    if _encode(decoded) != value:
        raise IdentityTokenError("non-canonical base64url")
    return decoded


def _require_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise IdentityTokenError("E2E identity secret must be exactly 32 bytes")


def decode_secret(value: str) -> bytes:
    secret = _decode(value)
    _require_secret(secret)
    return secret


def _claims(value: Any) -> IdentityClaims:
    if not isinstance(value, dict) or tuple(value) != _CLAIM_KEYS:
        raise IdentityTokenError("identity claims are missing, extra, or non-canonical")
    run_id, user_id = value["run_id"], value["user_id"]
    vehicle_id, scopes = value["vehicle_id"], value["scopes"]
    iat, exp = value["iat"], value["exp"]
    if not isinstance(run_id, str) or _RUN_RE.fullmatch(run_id) is None:
        raise IdentityTokenError("identity run namespace is invalid")
    if (
        not isinstance(user_id, str)
        or _SAFE_CLAIM_RE.fullmatch(user_id) is None
        or not user_id.startswith(run_id + "-")
        or len(user_id) == len(run_id) + 1
    ):
        raise IdentityTokenError("identity user namespace is invalid")
    if (
        not isinstance(vehicle_id, str)
        or _SAFE_CLAIM_RE.fullmatch(vehicle_id) is None
    ):
        raise IdentityTokenError("identity vehicle_id is invalid")
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(
            not isinstance(scope, str)
            or _SAFE_CLAIM_RE.fullmatch(scope) is None
            for scope in scopes
        )
    ):
        raise IdentityTokenError("identity scopes are invalid")
    if type(iat) is not int or type(exp) is not int:
        raise IdentityTokenError("identity timestamps are invalid")
    return IdentityClaims(run_id, user_id, vehicle_id, tuple(scopes), iat, exp)


def _canonical(claims: IdentityClaims) -> bytes:
    return json.dumps(
        claims.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_identity(
    token: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> IdentityClaims:
    _require_secret(secret)
    if not isinstance(token, str):
        raise IdentityTokenError("identity token must be text")
    parts = token.split(".")
    if len(parts) != 4 or parts[:2] != ["e2e", "v1"]:
        raise IdentityTokenError("identity token version is invalid")
    payload = _decode(parts[2])
    signature = _decode(parts[3])
    if len(signature) != hashlib.sha256().digest_size:
        raise IdentityTokenError("identity signature is invalid")
    signed = PREFIX + parts[2]
    expected = hmac.new(
        secret,
        signed.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise IdentityTokenError("identity signature is invalid")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityTokenError("identity payload is invalid JSON") from exc
    claims = _claims(raw)
    if _canonical(claims) != payload:
        raise IdentityTokenError("identity payload is not canonical JSON")
    current = int(time.time()) if now is None else now
    if type(current) is not int:
        raise IdentityTokenError("identity clock must return an integer")
    ttl = claims.exp - claims.iat
    if ttl <= 0 or ttl > MAX_TTL_S:
        raise IdentityTokenError("identity ttl exceeds 1920 seconds")
    if claims.iat > current + MAX_FUTURE_IAT_S:
        raise IdentityTokenError("identity iat is too far in the future")
    if current >= claims.exp:
        raise IdentityTokenError("identity token expired")
    return claims


def validate_e2e_session_id(session_id: Any, user_id: str) -> None:
    """Bind a signed E2E owner to its exact helper-issued session namespace."""

    if not isinstance(session_id, str) or not isinstance(user_id, str):
        raise IdentityTokenError("S2S session does not match signed identity")
    prefix = f"{user_id}-session-"
    number = session_id[len(prefix):] if session_id.startswith(prefix) else ""
    if not number or re.fullmatch(r"[1-9][0-9]*", number) is None:
        raise IdentityTokenError("S2S session does not match signed identity")


def resolve_s2s_identity(
    session_start: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    now: int | None = None,
) -> tuple[str, str, IdentityClaims | None]:
    """Resolve a session.start identity without changing production when off."""

    source = os.environ if environ is None else environ
    client_user = str(session_start.get("user_id") or "")
    client_vehicle = str(session_start.get("vehicle_id") or "")
    if str(source.get("E2E_IDENTITY_ENABLED") or "").lower() != "true":
        return client_user, client_vehicle, None
    raw_secret = str(source.get("E2E_IDENTITY_SECRET") or "")
    token = session_start.get("identity_token")
    if not raw_secret or not isinstance(token, str) or not token:
        raise IdentityTokenError("signed E2E identity is required")
    secret = decode_secret(raw_secret)
    claims = verify_identity(token, secret, now=now)
    if not client_user or client_user != claims.user_id:
        raise IdentityTokenError("S2S client user does not match signed identity")
    validate_e2e_session_id(session_start.get("session_id"), claims.user_id)
    return claims.user_id, claims.vehicle_id, claims

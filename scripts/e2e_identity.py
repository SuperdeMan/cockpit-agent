"""Short-lived signed identity tokens for manifest-driven E2E children."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PREFIX = "e2e.v1."
MAX_TIMEOUT_S = 1800
GRACE_S = 120
MAX_TTL_S = MAX_TIMEOUT_S + GRACE_S
MAX_FUTURE_IAT_S = 5
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_SAFE_CLAIM_RE = re.compile(r"[A-Za-z0-9._:-]+\Z")
_RUN_RE = re.compile(r"e2e-[A-Za-z0-9._:-]+\Z")
_CLAIM_KEYS = ("run_id", "user_id", "vehicle_id", "scopes", "iat", "exp")


class IdentityTokenError(ValueError):
    """An E2E identity secret, claim set, or token is invalid."""


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


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    if not isinstance(value, str) or _B64URL_RE.fullmatch(value) is None:
        raise IdentityTokenError("malformed base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise IdentityTokenError("malformed base64url") from exc
    if _encode_base64url(decoded) != value:
        raise IdentityTokenError("non-canonical base64url")
    return decoded


def generate_secret() -> bytes:
    return secrets.token_bytes(32)


def encode_secret(secret: bytes) -> str:
    _require_secret(secret)
    return _encode_base64url(secret)


def decode_secret(value: str) -> bytes:
    secret = _decode_base64url(value)
    _require_secret(secret)
    return secret


def _require_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise IdentityTokenError("E2E identity secret must be exactly 32 bytes")


def _normalize_claims(value: Any) -> IdentityClaims:
    if not isinstance(value, dict) or tuple(value) != _CLAIM_KEYS:
        raise IdentityTokenError("identity claims are missing, extra, or non-canonical")
    run_id = value["run_id"]
    user_id = value["user_id"]
    vehicle_id = value["vehicle_id"]
    scopes = value["scopes"]
    iat = value["iat"]
    exp = value["exp"]
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


def _canonical_payload(claims: IdentityClaims) -> bytes:
    return json.dumps(
        claims.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_clock(claims: IdentityClaims, now: int) -> None:
    ttl = claims.exp - claims.iat
    if ttl <= 0 or ttl > MAX_TTL_S:
        raise IdentityTokenError("identity ttl exceeds 1920 seconds")
    if claims.iat > now + MAX_FUTURE_IAT_S:
        raise IdentityTokenError("identity iat is too far in the future")
    if now >= claims.exp:
        raise IdentityTokenError("identity token expired")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise IdentityTokenError("identity payload contains duplicate keys")
        value[key] = item
    return value


def _decode_identity_token(token: str) -> tuple[str, bytes, bytes]:
    if not isinstance(token, str):
        raise IdentityTokenError("identity token must be text")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "e2e" or parts[1] != "v1":
        raise IdentityTokenError("identity token version is invalid")
    payload_segment, signature_segment = parts[2], parts[3]
    payload_bytes = _decode_base64url(payload_segment)
    signature = _decode_base64url(signature_segment)
    if len(signature) != hashlib.sha256().digest_size:
        raise IdentityTokenError("identity signature is invalid")
    return payload_segment, payload_bytes, signature


def _claims_from_payload(payload_bytes: bytes) -> IdentityClaims:
    try:
        raw = json.loads(
            payload_bytes,
            object_pairs_hook=_strict_json_object,
        )
    except IdentityTokenError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityTokenError("identity payload is invalid JSON") from exc
    claims = _normalize_claims(raw)
    if _canonical_payload(claims) != payload_bytes:
        raise IdentityTokenError("identity payload is not canonical JSON")
    return claims


def parse_identity_claims_unverified(
    token: str,
    *,
    now: int | None = None,
) -> IdentityClaims:
    """Parse strict public claims without verifying HMAC.

    This is only for a child process to self-check that a runner-issued bearer
    belongs to its own run/user/vehicle bundle. It must never be used for
    service-side authentication or authorization.
    """

    _payload_segment, payload_bytes, _signature = _decode_identity_token(token)
    claims = _claims_from_payload(payload_bytes)
    current = int(time.time()) if now is None else now
    if type(current) is not int:
        raise IdentityTokenError("identity clock must return an integer")
    _validate_clock(claims, current)
    return claims


def sign_identity(
    secret: bytes,
    *,
    run_id: str,
    user_id: str,
    vehicle_id: str,
    scopes: Sequence[str],
    timeout_s: int,
    now: int | None = None,
) -> str:
    _require_secret(secret)
    if type(timeout_s) is not int or not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise IdentityTokenError("identity timeout must be within 1..1800 seconds")
    issued_at = int(time.time()) if now is None else now
    if type(issued_at) is not int:
        raise IdentityTokenError("identity clock must return an integer")
    claims = _normalize_claims({
        "run_id": run_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
        "scopes": list(scopes),
        "iat": issued_at,
        "exp": issued_at + timeout_s + GRACE_S,
    })
    payload = _encode_base64url(_canonical_payload(claims))
    signed = PREFIX + payload
    signature = hmac.new(
        secret,
        signed.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return signed + "." + _encode_base64url(signature)


def verify_identity(
    token: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> IdentityClaims:
    _require_secret(secret)
    payload_segment, payload_bytes, signature = _decode_identity_token(token)
    signed = PREFIX + payload_segment
    expected = hmac.new(
        secret,
        signed.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise IdentityTokenError("identity signature is invalid")
    claims = _claims_from_payload(payload_bytes)
    current = int(time.time()) if now is None else now
    if type(current) is not int:
        raise IdentityTokenError("identity clock must return an integer")
    _validate_clock(claims, current)
    return claims


def sign_memory_extraction_session(
    secret: bytes,
    *,
    run_id: str,
    user_id: str,
    session_id: str,
    timeout_s: int,
    now: int | None = None,
):
    """Issue a dedicated memory capability under the ``e2emem.v1`` domain.

    The legacy function name is a wire-compatibility artifact: the returned
    token is bound to, but never replaces, the business ``session_id``.
    """

    from memory.e2e_capability import (
        MemoryCapabilityError,
        sign_memory_capability,
    )

    try:
        return sign_memory_capability(
            secret,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            timeout_s=timeout_s,
            now=now,
        )
    except MemoryCapabilityError as exc:
        raise IdentityTokenError(str(exc)) from exc


def parse_memory_extraction_session(
    token: str,
    *,
    now: int | None = None,
):
    """Validate public claims for a child that intentionally has no secret."""

    from memory.e2e_capability import (
        MemoryCapabilityError,
        parse_memory_capability,
    )

    try:
        return parse_memory_capability(token, now=now)
    except MemoryCapabilityError as exc:
        raise IdentityTokenError(str(exc)) from exc


def _owner_proof_url(ws_base: str, token: str) -> str:
    try:
        parsed = urlsplit(ws_base)
    except ValueError as exc:
        raise IdentityTokenError("owner proof WS URL is invalid") from exc
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc or parsed.fragment:
        raise IdentityTokenError("owner proof WS URL is invalid")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "token"
    ]
    query.append(("token", token))
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query, doseq=True, quote_via=quote),
        "",
    ))


def prove_identity_owner(
    *,
    ws_base: str,
    token: str,
    run_id: str,
    user_id: str,
    vehicle_id: str,
    connect: Any | None = None,
) -> None:
    """Require the gateway ACK before the child can perform test setup."""

    if not isinstance(token, str) or not token.startswith(PREFIX):
        raise IdentityTokenError("owner proof requires an E2E identity token")
    url = _owner_proof_url(ws_base, token)

    async def handshake() -> None:
        connector = connect
        if connector is None:
            try:
                import websockets
            except ImportError as exc:
                raise IdentityTokenError(
                    "websockets is required for E2E owner proof",
                ) from exc
            connector = websockets.connect
        try:
            async with connector(
                url,
                open_timeout=5,
                close_timeout=2,
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
        except IdentityTokenError:
            raise
        except Exception as exc:
            raise IdentityTokenError("E2E owner proof handshake failed") from exc
        try:
            message = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IdentityTokenError("E2E owner proof ACK is invalid") from exc
        expected = {
            "type": "e2e_identity_ack",
            "run_id": run_id,
            "user_id": user_id,
            "vehicle_id": vehicle_id,
        }
        if not isinstance(message, dict) or any(
            message.get(key) != value for key, value in expected.items()
        ):
            raise IdentityTokenError("E2E owner proof ACK does not match child")

    asyncio.run(handshake())

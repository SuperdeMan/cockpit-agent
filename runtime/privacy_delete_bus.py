"""Authenticated, fail-closed NATS protocol for cross-service user deletion.

Only the existing full ``ForgetUser`` operation may use this control plane.
A positive response attests deletion of one target's registered internal,
shared namespace; it does not attest deletion from an external merchant system
of record.  Frames are canonical, bounded and HMAC authenticated.  The HMAC
key is a domain-separated derivative of the already-mounted mesh private key;
no new secret is persisted.  Missing key material disables both callers and
responders instead of silently creating an unauthenticated control plane.

Responders use one queue group per target.  In production each target stores
owner state in shared Redis, so one replica deleting the target namespace is
sufficient; the adapter itself remains idempotent.  A bounded in-process nonce
guard rejects duplicate deliveries to a replica, while the short timestamp
window limits captured-frame reuse across replica churn.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

DELETE_ACTION = "privacy_user_all"
MCP_SUBJECT = "privacy.user.delete.mcp"
CLOUD_SUBJECT = "privacy.user.delete.cloud"
MCP_TARGET = "mcp"
CLOUD_TARGET = "cloud"
MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_BYTES = 1024
MAX_USER_ID_BYTES = 512
MAX_CLOCK_SKEW_S = 30
NONCE_BYTES = 16
MAX_REPLAY_ENTRIES = 4096

_KEY_DERIVATION_CONTEXT = b"car-agent/privacy-delete/control-key/v1"
_REQUEST_SIGNATURE_CONTEXT = b"car-agent/privacy-delete/request/v1"
_RESPONSE_SIGNATURE_CONTEXT = b"car-agent/privacy-delete/response/v1"
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

_TARGETS = frozenset({MCP_TARGET, CLOUD_TARGET})
_SUBJECT_BY_TARGET = {
    MCP_TARGET: MCP_SUBJECT,
    CLOUD_TARGET: CLOUD_SUBJECT,
}


class PrivacyDeleteProtocolError(ValueError):
    """A privacy control-plane frame violated its closed schema."""


@dataclass(frozen=True)
class DeleteRequestClaims:
    user_id: str
    target: str
    nonce: str
    issued_at: int
    request_digest: str


class ReplayGuard:
    """Bounded per-responder nonce cache; full cache fails closed."""

    def __init__(self, maximum: int = MAX_REPLAY_ENTRIES):
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            raise PrivacyDeleteProtocolError("invalid replay guard")
        self._maximum = maximum
        self._seen: OrderedDict[str, int] = OrderedDict()

    def claim(self, nonce: str, *, expires_at: int, now: int) -> bool:
        expired = [key for key, expiry in self._seen.items() if expiry < now]
        for key in expired:
            self._seen.pop(key, None)
        if nonce in self._seen or len(self._seen) >= self._maximum:
            return False
        self._seen[nonce] = expires_at
        return True


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrivacyDeleteProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _decode_canonical(data: bytes, *, maximum: int) -> dict:
    if not isinstance(data, bytes) or not data or len(data) > maximum:
        raise PrivacyDeleteProtocolError("invalid frame size")
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivacyDeleteProtocolError("invalid JSON") from exc
    if not isinstance(value, dict) or _canonical_json(value) != data:
        raise PrivacyDeleteProtocolError("non-canonical JSON object")
    return value


def derive_control_key(key_material: bytes) -> bytes:
    """Derive a privacy-only HMAC key from the mesh private-key bytes."""
    if not isinstance(key_material, bytes) or len(key_material) < 32:
        raise PrivacyDeleteProtocolError("invalid control key material")
    return hmac.new(
        key_material,
        _KEY_DERIVATION_CONTEXT,
        hashlib.sha256,
    ).digest()


def load_control_key(*, environ: Mapping[str, str] | None = None) -> bytes:
    """Load and derive the runtime key; absent/unreadable material is fatal."""
    env = os.environ if environ is None else environ
    path_text = (
        (env.get("PRIVACY_DELETE_KEY_FILE") or "").strip()
        or (env.get("GRPC_TLS_KEY") or "").strip()
        or "/certs/server.key"
    )
    try:
        material = Path(path_text).read_bytes()
    except (OSError, ValueError) as exc:
        raise PrivacyDeleteProtocolError("control key unavailable") from exc
    if len(material) > 64 * 1024:
        raise PrivacyDeleteProtocolError("invalid control key material")
    return derive_control_key(material)


def _resolve_key(key: bytes | None) -> bytes:
    candidate = load_control_key() if key is None else key
    if not isinstance(candidate, bytes) or len(candidate) < 32:
        raise PrivacyDeleteProtocolError("invalid control key")
    return candidate


def _clock(now: float | int | None = None) -> int:
    value = time.time() if now is None else now
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value < 0
    ):
        raise PrivacyDeleteProtocolError("invalid clock")
    return int(value)


def _sign(unsigned: dict, *, key: bytes, context: bytes) -> str:
    return hmac.new(
        key,
        context + b"\0" + _canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()


def _encode_signed(unsigned: dict, *, key: bytes, context: bytes,
                   maximum: int) -> bytes:
    value = dict(unsigned)
    value["signature"] = _sign(unsigned, key=key, context=context)
    data = _canonical_json(value)
    if len(data) > maximum:
        raise PrivacyDeleteProtocolError("invalid frame size")
    return data


def _decode_signed(data: bytes, *, key: bytes, context: bytes,
                   maximum: int, fields: frozenset[str]) -> dict:
    value = _decode_canonical(data, maximum=maximum)
    if set(value) != fields | {"signature"}:
        raise PrivacyDeleteProtocolError("invalid signed frame schema")
    signature = value.get("signature")
    if not isinstance(signature, str) or _HEX_64.fullmatch(signature) is None:
        raise PrivacyDeleteProtocolError("invalid signature")
    unsigned = {name: item for name, item in value.items() if name != "signature"}
    expected = _sign(unsigned, key=key, context=context)
    if not hmac.compare_digest(signature, expected):
        raise PrivacyDeleteProtocolError("invalid signature")
    return unsigned


def _validate_timestamp(issued_at: object, *, now: int) -> int:
    if type(issued_at) is not int or abs(now - issued_at) > MAX_CLOCK_SKEW_S:
        raise PrivacyDeleteProtocolError("expired frame")
    return issued_at


def _validate_nonce(nonce: object) -> str:
    if not isinstance(nonce, str) or _HEX_32.fullmatch(nonce) is None:
        raise PrivacyDeleteProtocolError("invalid nonce")
    return nonce


def _validate_user_id(user_id: object) -> str:
    if not isinstance(user_id, str) or not user_id or user_id != user_id.strip():
        raise PrivacyDeleteProtocolError("invalid user ID")
    if len(user_id.encode("utf-8")) > MAX_USER_ID_BYTES:
        raise PrivacyDeleteProtocolError("invalid user ID")
    if any(unicodedata.category(char).startswith("C") for char in user_id):
        raise PrivacyDeleteProtocolError("invalid user ID")
    return user_id


def validate_user_id(user_id: object) -> str:
    """Public owner validator shared by the authenticated HTTP ingress."""
    return _validate_user_id(user_id)


def encode_delete_request(
    user_id: str,
    *,
    target: str,
    key: bytes | None = None,
    now: float | int | None = None,
    nonce: str | None = None,
) -> bytes:
    owner = _validate_user_id(user_id)
    if target not in _TARGETS:
        raise PrivacyDeleteProtocolError("invalid target")
    nonce_value = secrets.token_hex(NONCE_BYTES) if nonce is None else _validate_nonce(nonce)
    return _encode_signed(
        {
            "action": DELETE_ACTION,
            "issued_at": _clock(now),
            "nonce": nonce_value,
            "target": target,
            "user_id": owner,
        },
        key=_resolve_key(key),
        context=_REQUEST_SIGNATURE_CONTEXT,
        maximum=MAX_REQUEST_BYTES,
    )


def decode_delete_request(
    data: bytes,
    *,
    expected_target: str,
    key: bytes | None = None,
    now: float | int | None = None,
    replay_guard: ReplayGuard | None = None,
) -> DeleteRequestClaims:
    if expected_target not in _TARGETS:
        raise PrivacyDeleteProtocolError("invalid target")
    key_value = _resolve_key(key)
    value = _decode_signed(
        data,
        key=key_value,
        context=_REQUEST_SIGNATURE_CONTEXT,
        maximum=MAX_REQUEST_BYTES,
        fields=frozenset({"action", "issued_at", "nonce", "target", "user_id"}),
    )
    if value.get("action") != DELETE_ACTION:
        raise PrivacyDeleteProtocolError("invalid action")
    if value.get("target") != expected_target:
        raise PrivacyDeleteProtocolError("invalid target")
    current = _clock(now)
    issued_at = _validate_timestamp(value.get("issued_at"), now=current)
    nonce = _validate_nonce(value.get("nonce"))
    if replay_guard is not None and not replay_guard.claim(
        nonce,
        expires_at=issued_at + MAX_CLOCK_SKEW_S,
        now=current,
    ):
        raise PrivacyDeleteProtocolError("replay rejected")
    return DeleteRequestClaims(
        user_id=_validate_user_id(value.get("user_id")),
        target=expected_target,
        nonce=nonce,
        issued_at=issued_at,
        request_digest=hashlib.sha256(data).hexdigest(),
    )


def encode_delete_response(
    *,
    ok: bool,
    target: str,
    request: DeleteRequestClaims,
    key: bytes | None = None,
    now: float | int | None = None,
) -> bytes:
    if (
        type(ok) is not bool
        or target not in _TARGETS
        or not isinstance(request, DeleteRequestClaims)
        or request.target != target
    ):
        raise PrivacyDeleteProtocolError("invalid response")
    return _encode_signed(
        {
            "action": DELETE_ACTION,
            "issued_at": _clock(now),
            "nonce": request.nonce,
            "ok": ok,
            "request_digest": request.request_digest,
            "target": target,
        },
        key=_resolve_key(key),
        context=_RESPONSE_SIGNATURE_CONTEXT,
        maximum=MAX_RESPONSE_BYTES,
    )


def decode_delete_response(
    data: bytes,
    *,
    expected_target: str,
    request_data: bytes,
    key: bytes | None = None,
    now: float | int | None = None,
) -> bool:
    if expected_target not in _TARGETS:
        raise PrivacyDeleteProtocolError("invalid target")
    key_value = _resolve_key(key)
    current = _clock(now)
    request = decode_delete_request(
        request_data,
        expected_target=expected_target,
        key=key_value,
        now=current,
    )
    value = _decode_signed(
        data,
        key=key_value,
        context=_RESPONSE_SIGNATURE_CONTEXT,
        maximum=MAX_RESPONSE_BYTES,
        fields=frozenset({
            "action", "issued_at", "nonce", "ok", "request_digest", "target",
        }),
    )
    if value.get("action") != DELETE_ACTION:
        raise PrivacyDeleteProtocolError("invalid action")
    _validate_timestamp(value.get("issued_at"), now=current)
    if (
        value.get("target") != expected_target
        or type(value.get("ok")) is not bool
        or value.get("nonce") != request.nonce
        or value.get("request_digest") != request.request_digest
    ):
        raise PrivacyDeleteProtocolError("invalid response")
    return value["ok"]


DeleteAdapter = Callable[[str, str], Awaitable[bool] | bool]


def require_shared_redis_backend(redis_url: object) -> None:
    """Reject responders whose target can fall back to process-local memory."""
    if not isinstance(redis_url, str) or not redis_url.strip():
        raise PrivacyDeleteProtocolError("shared Redis backend required")
    parsed = urlsplit(redis_url.strip())
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise PrivacyDeleteProtocolError("shared Redis backend required")


async def require_ready_shared_redis_backend(store: object) -> None:
    """Prove that an adapter is backed by a reachable shared Redis instance."""
    require_shared_redis_backend(getattr(store, "_url", ""))
    try:
        probe = getattr(store, "shared_backend_ready", None)
        if callable(probe):
            candidate = probe()
            ready = await candidate if inspect.isawaitable(candidate) else candidate
        else:
            connect = getattr(store, "_redis", None)
            if not callable(connect):
                raise PrivacyDeleteProtocolError(
                    "shared Redis backend unavailable")
            candidate = connect()
            client = await candidate if inspect.isawaitable(candidate) else candidate
            ping = getattr(client, "ping", None)
            if not callable(ping):
                raise PrivacyDeleteProtocolError(
                    "shared Redis backend unavailable")
            candidate = ping()
            ready = await candidate if inspect.isawaitable(candidate) else candidate
    except PrivacyDeleteProtocolError:
        raise
    except Exception as exc:
        raise PrivacyDeleteProtocolError(
            "shared Redis backend unavailable") from exc
    if ready is not True:
        raise PrivacyDeleteProtocolError("shared Redis backend unavailable")


async def install_delete_responder(
    nc,
    *,
    subject: str,
    target: str,
    delete: DeleteAdapter,
    globally_shared_backend: bool = False,
    key: bytes | None = None,
    now: Callable[[], float | int] | None = None,
) -> bool:
    """Install one queue responder; malformed/replayed frames get no reply."""
    if target not in _TARGETS or subject != _SUBJECT_BY_TARGET[target]:
        raise PrivacyDeleteProtocolError("invalid responder")
    if globally_shared_backend is not True:
        raise PrivacyDeleteProtocolError("shared backend proof required")
    key_value = _resolve_key(key)
    clock = time.time if now is None else now
    replay_guard = ReplayGuard()

    async def callback(message):
        try:
            request = decode_delete_request(
                message.data,
                expected_target=target,
                key=key_value,
                now=clock(),
                replay_guard=replay_guard,
            )
        except Exception:
            return
        try:
            candidate = delete(request.user_id, DELETE_ACTION)
            candidate = await candidate if inspect.isawaitable(candidate) else candidate
            ok = candidate is True
        except Exception:
            ok = False
        if getattr(message, "reply", ""):
            await nc.publish(
                message.reply,
                encode_delete_response(
                    ok=ok,
                    target=target,
                    request=request,
                    key=key_value,
                    now=clock(),
                ),
            )

    await nc.subscribe(subject, queue=f"privacy-delete-{target}", cb=callback)
    flush = getattr(nc, "flush", None)
    if callable(flush):
        await flush(timeout=1.0)
    return True


async def request_delete(
    nc,
    *,
    subject: str,
    target: str,
    user_id: str,
    timeout: float,
    key: bytes | None = None,
    now: float | int | None = None,
) -> bool:
    """Issue one request and accept only its authenticated, bound response."""
    if (
        target not in _TARGETS
        or subject != _SUBJECT_BY_TARGET[target]
        or not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        raise PrivacyDeleteProtocolError("invalid request parameters")
    key_value = _resolve_key(key)
    issued_at = _clock(now)
    request_data = encode_delete_request(
        user_id,
        target=target,
        key=key_value,
        now=issued_at,
    )
    message = await nc.request(
        subject,
        request_data,
        timeout=float(timeout),
    )
    return decode_delete_response(
        message.data,
        expected_target=target,
        request_data=request_data,
        key=key_value,
        now=issued_at if now is not None else None,
    )

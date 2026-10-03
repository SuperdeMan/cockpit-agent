"""Collector 调试面的运维凭据：除健康与指标外，读写都要运维者签的短期令牌。

collector 是运维工具（会话原话、轨迹、日志、模拟车态都在里面），合法主体是运维者、不是车主
（设计：``docs/design/2026-10-03-v2-collector-access.md``）。修前 20 个接口一律不鉴权，tailnet 里谁都能读全部对话。

格式 ``obs.v1.<base64url 载荷>.<base64url 签名>``，载荷是规范 JSON：主体（固定 ``operator``）、签发与过期时间。
HMAC-SHA256，密钥由运维工具与云上已有的 ``E2E_IDENTITY_SECRET`` 按独立上下文派生——不新增密钥、不改 ``.env``；
与 e2e 身份令牌（``e2e.v1.``，直接用该密钥）前缀与密钥都不同，互相不能冒用。
密钥缺失或过短 ⇒ ``None``：校验一律失败，调试面关闭（失败方向是少给）。

HTTP 只认 ``Authorization: Bearer``（不进 URL，免得进访问日志）；WebSocket 不能设头，首帧发 ``auth_frame``。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Mapping

SECRET_ENV = "E2E_IDENTITY_SECRET"
#: e2e 运行器签好后传给子进程的令牌（子进程不得继承签名密钥）
TOKEN_ENV = "E2E_COLLECTOR_TOKEN"
PREFIX = "obs.v1."
SUBJECT = "operator"
MAX_TTL_S = 12 * 3600
MAX_FUTURE_S = 30
MAX_TOKEN_CHARS = 1024
#: 首帧认证的类型（``{"type": "auth", "token": ...}``）与等待时限
AUTH_FRAME_TYPE = "auth"
AUTH_FRAME_TIMEOUT_S = 5.0
_MIN_SECRET_CHARS = 32
_KEY_CONTEXT = b"car-agent/collector-operator/key/v1"
_SIGN_CONTEXT = b"car-agent/collector-operator/v1."
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_FIELDS = ("exp", "iat", "sub", "v")


class ObsTokenError(ValueError):
    """凭据不可信（格式、签名、过期、主体，或没有配置密钥）。"""


def derive_key(secret: str) -> bytes | None:
    material = (secret or "").strip()
    if len(material) < _MIN_SECRET_CHARS:
        return None
    return hmac.new(material.encode("utf-8"), _KEY_CONTEXT, hashlib.sha256).digest()


def key_from_env(environ: Mapping[str, str] | None = None) -> bytes | None:
    env = os.environ if environ is None else environ
    return derive_key(env.get(SECRET_ENV, ""))


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    if not _B64.fullmatch(text or ""):
        raise ValueError("malformed base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _encode(raw) != text:
        raise ValueError("non-canonical base64url")
    return raw


def _canonical(claims: dict) -> bytes:
    return json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signature(key: bytes, payload: str) -> bytes:
    return hmac.new(key, _SIGN_CONTEXT + payload.encode("ascii"), hashlib.sha256).digest()


def issue(key: bytes, *, ttl_s: int = MAX_TTL_S, now: int | None = None) -> str:
    if not key:
        raise ObsTokenError("collector operator key is not configured")
    if not 0 < int(ttl_s) <= MAX_TTL_S:
        raise ObsTokenError("operator token lifetime out of range")
    issued = int(time.time()) if now is None else int(now)
    payload = _encode(_canonical({"v": 1, "sub": SUBJECT, "iat": issued, "exp": issued + int(ttl_s)}))
    return f"{PREFIX}{payload}.{_encode(_signature(key, payload))}"


def verify(key: bytes | None, token, *, now: int | None = None) -> dict:
    """有效 ⇒ 载荷；任何不对 ⇒ ``ObsTokenError``（不说明是哪一项不对，也不回显令牌）。"""
    if not key:
        raise ObsTokenError("collector operator key is not configured")
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or not token.startswith(PREFIX):
        raise ObsTokenError("invalid operator token")
    try:
        payload, signature = token[len(PREFIX):].split(".")
        if not hmac.compare_digest(_decode(signature), _signature(key, payload)):
            raise ObsTokenError("invalid operator token")
        raw = _decode(payload)
        claims = json.loads(raw)
        if not isinstance(claims, dict) or tuple(sorted(claims)) != _FIELDS or _canonical(claims) != raw:
            raise ObsTokenError("invalid operator token")
        iat, exp = claims["iat"], claims["exp"]
        if claims["v"] != 1 or claims["sub"] != SUBJECT or type(iat) is not int or type(exp) is not int \
                or not 0 < exp - iat <= MAX_TTL_S:
            raise ObsTokenError("invalid operator token")
    except (ValueError, UnicodeError, KeyError) as exc:
        if isinstance(exc, ObsTokenError):
            raise
        raise ObsTokenError("invalid operator token") from None
    current = int(time.time()) if now is None else int(now)
    if iat > current + MAX_FUTURE_S or current >= exp:
        raise ObsTokenError("invalid operator token")
    return claims


def bearer(header_value: str) -> str:
    """``Authorization`` 头里的 Bearer 令牌；不是 Bearer ⇒ 空串。"""
    scheme, _, value = (header_value or "").strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def auth_frame(token: str) -> str:
    return json.dumps({"type": AUTH_FRAME_TYPE, "token": token})


def token_from_frame(text: str) -> str:
    """首帧里的令牌；不是认证帧 ⇒ 空串。"""
    try:
        frame = json.loads(text)
    except (TypeError, ValueError):
        return ""
    if not isinstance(frame, dict) or frame.get("type") != AUTH_FRAME_TYPE:
        return ""
    token = frame.get("token")
    return token if isinstance(token, str) else ""

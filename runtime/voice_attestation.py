"""CA2-15 S2：声音证明——乘员身份只认 llm-gateway 签发的声纹识别结果。

修前云端与车端直接信 meta 里的 ``occupant_id`` / ``occupant_name``：任何拿着车辆 token 的客户端都能自称任意乘员、
读到那位乘员的个人记忆。现在 llm-gateway 在声纹 ``accept`` 时签发一枚短期证明，客户端只转交（meta
``voice_attestation``、S2S ``occupant`` 帧）；接收方验签后才换乘员，否则按 ``primary``（存量语义）。

格式 ``voice.v1.<base64url 载荷>.<base64url 签名>``，载荷是规范 JSON：主体、乘员、称呼、签发与过期时间。
HMAC-SHA256，密钥由各服务已挂载的网格私钥按独立上下文派生（与删除总线同一做法，不新增密钥）；
材料不可读时签发与校验都失效——没有证明就是 ``primary``，失败方向是少给，不是多给。

⚠ 声纹只做个性化：证明决定「这一轮读谁的记忆」，不进权限、确认、VAL 或支付（``test_voiceprint_not_auth.py``）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

META = "voice_attestation"
#: HMI 声明「这一轮免唤醒语音没有认出说话人」（CA2-15 S2 口径：只有车机免唤醒语音会被投影）。
IDENTITY = "voice_identity"
UNRECOGNIZED = "unrecognized"

PREFIX = "voice.v1."
TTL_S = 600
MAX_FUTURE_S = 30
MAX_TOKEN_CHARS = 2048
_KEY_CONTEXT = b"car-agent/voice-attestation/key/v1"
_SIGN_CONTEXT = b"car-agent/voice-attestation/v1."
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_FIELDS = ("v", "user_id", "occupant_id", "display_name", "iat", "exp")


@dataclass(frozen=True)
class VoiceAttestation:
    user_id: str
    occupant_id: str
    display_name: str
    exp: int


def derive_key(material: bytes) -> bytes:
    if not isinstance(material, bytes) or len(material) < 32:
        raise ValueError("invalid voice attestation key material")
    return hmac.new(material, _KEY_CONTEXT, hashlib.sha256).digest()


def load_key(*, environ: Mapping[str, str] | None = None) -> bytes | None:
    """派生签名密钥；材料缺失或不可读回 None（调用方据此一律按 ``primary``）。"""
    env = os.environ if environ is None else environ
    path = (env.get("VOICE_ATTESTATION_KEY_FILE") or "").strip() or \
        (env.get("GRPC_TLS_KEY") or "").strip() or "/certs/server.key"
    try:
        material = Path(path).read_bytes()
        if len(material) > 64 * 1024:
            return None
        return derive_key(material)
    except (OSError, ValueError):
        return None


_runtime_key: bytes | None = None


def runtime_key() -> bytes | None:
    """本进程的签名密钥（首次成功读取后缓存；读不到时下次再试）。"""
    global _runtime_key
    if _runtime_key is None:
        _runtime_key = load_key()
    return _runtime_key


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
    return json.dumps(claims, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def issue(key: bytes, *, user_id: str, occupant_id: str, display_name: str = "",
          now: int | None = None, ttl_s: int = TTL_S) -> str:
    if not user_id or not occupant_id:
        raise ValueError("attestation needs an owner and an occupant")
    issued = int(time.time()) if now is None else int(now)
    claims = {"v": 1, "user_id": user_id, "occupant_id": occupant_id,
              "display_name": display_name or "", "iat": issued, "exp": issued + int(ttl_s)}
    payload = _encode(_canonical(claims))
    signature = hmac.new(key, _SIGN_CONTEXT + payload.encode("ascii"), hashlib.sha256).digest()
    return f"{PREFIX}{payload}.{_encode(signature)}"


def verify(token, key: bytes | None, *, user_id: str, now: int | None = None) -> VoiceAttestation | None:
    """有效 ⇒ 证明里的乘员与称呼；任何不对（格式、签名、过期、主体不符、没有密钥）⇒ None。"""
    if key is None or not user_id or not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS:
        return None
    if not token.startswith(PREFIX):
        return None
    try:
        payload, signature = token[len(PREFIX):].split(".")
        expected = hmac.new(key, _SIGN_CONTEXT + payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_decode(signature), expected):
            return None
        raw = _decode(payload)
        claims = json.loads(raw)
        if not isinstance(claims, dict) or tuple(sorted(claims)) != tuple(sorted(_FIELDS)):
            return None
        if _canonical(claims) != raw or claims["v"] != 1:
            return None
        owner, occupant, name = claims["user_id"], claims["occupant_id"], claims["display_name"]
        iat, exp = claims["iat"], claims["exp"]
        if not all(isinstance(v, str) for v in (owner, occupant, name)) or not occupant:
            return None
        if type(iat) is not int or type(exp) is not int or not 0 < exp - iat <= TTL_S:
            return None
    except (ValueError, UnicodeError, KeyError):
        return None
    current = int(time.time()) if now is None else int(now)
    if iat > current + MAX_FUTURE_S or current >= exp:
        return None
    if not hmac.compare_digest(owner.encode("utf-8"), user_id.encode("utf-8")):
        return None
    return VoiceAttestation(user_id=owner, occupant_id=occupant, display_name=name, exp=exp)

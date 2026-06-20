"""和风天气 WeatherProvider 适配（QWeather Web API）。

支持两种鉴权（凭证均经 env 注入，绝不进代码/日志/commit）：
- **JWT（和风新版，推荐）**：用 Ed25519 私钥本地签发 JWT（alg=EdDSA、header 带 kid、
  payload sub=项目ID），按 `Authorization: Bearer <jwt>` 调用；token 短期有效，本地缓存重签。
- **API Key（旧版）**：查询参数 `?key=`。

和风约定：先经 GeoAPI 城市检索把城市名解析成 location id，再查实时天气；响应 ``code=="200"``
为成功。host 按账户不同（控制台专属 host / devapi.qweather.com / api.qweather.com），经
QWEATHER_HOST 配置。docs: https://dev.qweather.com/docs/configuration/authentication/
"""
from __future__ import annotations
import base64
import json
import logging
import time

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import WeatherProvider, Weather

logger = logging.getLogger("agent.info.qweather")


def _s(v) -> str:
    return str(v) if v is not None else ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_ed25519_private_key(data):
    """健壮加载 Ed25519 私钥，容忍多种粘贴形态：

    - 完整 PEM（``-----BEGIN PRIVATE KEY-----`` …）；
    - 单行 PEM（换行用字面 ``\\n``）；
    - 裸 base64 的 PKCS8 DER（即 PEM 去掉头尾的中间那段，和风控制台常见）；
    - 裸 base64 的 32 字节原始种子。

    凭证只在内存解析，不落盘、不打印。
    """
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key, load_der_private_key)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    text = data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)
    text = text.strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        key = load_pem_private_key(text.encode(), password=None)
    else:
        blob = base64.b64decode("".join(text.split()) + "=" * (-len("".join(text.split())) % 4))
        key = (Ed25519PrivateKey.from_private_bytes(blob) if len(blob) == 32
               else load_der_private_key(blob, password=None))
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("QWeather JWT requires an Ed25519 private key")
    return key


class QWeatherJWT:
    """和风 JWT 签发器：Ed25519 签名 + 短期缓存。私钥只在内存，不外泄。"""

    def __init__(self, project_id: str, key_id: str, private_key, ttl: int = 900):
        if not project_id or not key_id:
            raise ValueError("QWeather JWT requires project_id(sub) and key_id(kid)")
        key = load_ed25519_private_key(private_key)
        self._key = key
        self._sub = project_id
        self._kid = key_id
        self._ttl = ttl
        self._token = ""
        self._exp = 0

    def token(self) -> str:
        now = int(time.time())
        if self._token and now < self._exp - 60:
            return self._token
        header = _b64url(json.dumps({"alg": "EdDSA", "kid": self._kid},
                                    separators=(",", ":")).encode())
        exp = now + self._ttl
        payload = _b64url(json.dumps({"sub": self._sub, "iat": now - 30, "exp": exp},
                                     separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        sig = _b64url(self._key.sign(signing_input))
        self._token = f"{header}.{payload}.{sig}"
        self._exp = exp
        return self._token


class QWeatherProvider(WeatherProvider):
    def __init__(self, api_key: str = "", jwt_auth: QWeatherJWT | None = None,
                 host: str = "devapi.qweather.com"):
        if not api_key and jwt_auth is None:
            raise ValueError("QWeather requires api_key or jwt_auth")
        self._api_key = api_key
        self._jwt = jwt_auth
        self._base = f"https://{host.strip().rstrip('/')}"
        self._http = AsyncHttpClient(vendor="qweather", service="info")

    async def _get(self, path: str, params: dict, op: str, meta) -> dict:
        q = dict(params)
        headers = None
        if self._jwt is not None:
            headers = {"Authorization": f"Bearer {self._jwt.token()}"}
        else:
            q["key"] = self._api_key
        data = await self._http.get_json(
            f"{self._base}{path}", params=q, op=op, headers=headers, meta=meta)
        code = str(data.get("code"))
        if code != "200":
            raise ProviderError(f"qweather {op} failed: code={code}")
        return data

    async def _lookup_city(self, city: str, meta) -> tuple[str, str]:
        """城市名 → (location_id, 规范城市名)。无结果抛 ProviderError。"""
        data = await self._get("/geo/v2/city/lookup", {"location": city}, "city_lookup", meta)
        locs = data.get("location") or []
        if not locs:
            raise ProviderError(f"qweather city not found: {city}")
        top = locs[0]
        return _s(top.get("id")), _s(top.get("name")) or city

    async def now(self, city: str, meta: dict | None = None) -> Weather:
        loc_id, city_name = await self._lookup_city(city, meta)
        data = await self._get("/v7/weather/now", {"location": loc_id}, "weather_now", meta)
        now = data.get("now") or {}
        return Weather(
            city=city_name,
            temp=_s(now.get("temp")),
            text=_s(now.get("text")),
            feels_like=_s(now.get("feelsLike")),
            humidity=_s(now.get("humidity")),
            wind_dir=_s(now.get("windDir")),
            wind_scale=_s(now.get("windScale")),
            update_time=_s(data.get("updateTime")),
        )

"""和风 QWeatherProvider 单测：mock 掉底层 HTTP，喂和风黄金响应；覆盖 API Key 与 JWT 两种鉴权。

验证 GeoAPI 城市检索→实时天气解析、城市无结果报错、code!=200 报错，以及 JWT 签发（EdDSA/Ed25519）
与 Bearer 头注入。不发真实网络。
"""
import asyncio
import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents._sdk.http import ProviderError
from agents.info.src.providers.qweather import QWeatherProvider, QWeatherJWT


def _provider(responses: dict, **kw):
    p = QWeatherProvider(api_key="test-key", **kw) if "jwt_auth" not in kw \
        else QWeatherProvider(**kw)

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        fake_get_json.last = {"url": url, "params": params, "headers": headers}
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    p._spy = fake_get_json
    return p


def _ed25519_pem() -> bytes:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_LOOKUP_OK = {"code": "200", "location": [{"id": "101010100", "name": "北京"}]}
_NOW_OK = {"code": "200", "updateTime": "2026-06-20T10:00+08:00",
           "now": {"temp": "28", "text": "晴", "feelsLike": "30",
                   "humidity": "45", "windDir": "南风", "windScale": "3"}}


def test_now_parses_apikey_mode():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK, "/v7/weather/now": _NOW_OK})
    w = asyncio.run(p.now("北京"))
    assert w.city == "北京"
    assert w.temp == "28" and w.text == "晴"
    assert w.feels_like == "30" and w.wind_dir == "南风" and w.wind_scale == "3"
    assert w.update_time.startswith("2026-06-20")
    # API Key 模式：带 key 参数、无 Authorization 头
    assert p._spy.last["params"].get("key") == "test-key"
    assert p._spy.last["headers"] is None


def test_city_not_found_raises():
    p = _provider({"/geo/v2/city/lookup": {"code": "404", "location": []}})
    with pytest.raises(ProviderError):
        asyncio.run(p.now("不存在城"))


def test_weather_now_bad_code_raises():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK, "/v7/weather/now": {"code": "401"}})
    with pytest.raises(ProviderError):
        asyncio.run(p.now("北京"))


def test_jwt_token_is_valid_eddsa():
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    signer = QWeatherJWT("proj-1", "kid-1", pem)
    tok = signer.token()
    h, p, s = tok.split(".")
    assert json.loads(_b64d(h)) == {"alg": "EdDSA", "kid": "kid-1"}
    payload = json.loads(_b64d(p))
    assert payload["sub"] == "proj-1" and payload["exp"] > payload["iat"]
    # 签名可被公钥验证（EdDSA）
    key.public_key().verify(_b64d(s), f"{h}.{p}".encode("ascii"))
    # 第二次调用命中缓存（同一 token）
    assert signer.token() == tok


def test_jwt_mode_sends_bearer_header_no_key_param():
    signer = QWeatherJWT("proj", "kid", _ed25519_pem())
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK, "/v7/weather/now": _NOW_OK},
                  jwt_auth=signer)
    asyncio.run(p.now("北京"))
    assert p._spy.last["headers"]["Authorization"].startswith("Bearer ")
    assert "key" not in (p._spy.last["params"] or {})  # JWT 模式不带 key 参数


def test_jwt_accepts_bare_base64_pkcs8_der():
    """私钥贴成"裸 base64（PKCS8 DER，无 PEM 头尾）"也能用——和风控制台/误填常见形态。"""
    key = Ed25519PrivateKey.generate()
    der = key.private_bytes(serialization.Encoding.DER,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    bare_b64 = base64.b64encode(der).decode()  # 无头尾、单行
    signer = QWeatherJWT("proj", "kid", bare_b64)
    h, p, s = signer.token().split(".")
    key.public_key().verify(_b64d(s), f"{h}.{p}".encode("ascii"))  # 签名可验证


def test_jwt_accepts_raw_seed_base64():
    """私钥贴成"裸 base64（32 字节原始种子）"也能用。"""
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    signer = QWeatherJWT("proj", "kid", base64.b64encode(seed).decode())
    h, p, s = signer.token().split(".")
    key.public_key().verify(_b64d(s), f"{h}.{p}".encode("ascii"))

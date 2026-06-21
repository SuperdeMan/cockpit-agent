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


_LOOKUP_OK = {
    "code": "200",
    "location": [{"id": "101010100", "name": "北京", "lat": "39.90", "lon": "116.40"}],
}
_NOW_OK = {"code": "200", "updateTime": "2026-06-20T10:00+08:00",
           "now": {"temp": "28", "text": "晴", "feelsLike": "30",
                   "humidity": "45", "windDir": "南风", "windScale": "3",
                   "precip": "0.2", "pressure": "1008", "vis": "10",
                   "cloud": "15", "dew": "12"}}


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


# ── forecast 测试 ──────────────────────────────────────────

_FORECAST_3D_OK = {
    "code": "200",
    "daily": [
        {"fxDate": "2026-06-21", "textDay": "多云", "textNight": "晴",
         "tempMax": "30", "tempMin": "22", "windDirDay": "东南风",
         "windScaleDay": "2", "humidity": "55", "precip": "0.3",
         "uvIndex": "6", "sunrise": "04:45", "sunset": "19:40"},
        {"fxDate": "2026-06-22", "textDay": "晴", "textNight": "多云",
         "tempMax": "32", "tempMin": "23", "windDirDay": "南风",
         "windScaleDay": "3", "humidity": "50"},
        {"fxDate": "2026-06-23", "textDay": "小雨", "textNight": "阴",
         "tempMax": "28", "tempMin": "21", "windDirDay": "北风",
         "windScaleDay": "2", "humidity": "70"},
    ],
}


def test_forecast_parses_3day():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/v7/weather/3d": _FORECAST_3D_OK})
    res = asyncio.run(p.forecast("北京", days=3))
    assert len(res) == 3
    assert res[0].date == "2026-06-21"
    assert res[0].text_day == "多云" and res[0].text_night == "晴"
    assert res[0].temp_high == "30" and res[0].temp_low == "22"
    assert res[2].text_day == "小雨"


def test_forecast_7d_uses_7d_endpoint():
    """days > 3 时应走 /v7/weather/7d。"""
    resp_7d = {"code": "200", "daily": _FORECAST_3D_OK["daily"] * 3}  # 凑 9 条
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/v7/weather/7d": resp_7d})
    res = asyncio.run(p.forecast("北京", days=7))
    assert len(res) == 7  # 取前 7 条


# ── alerts 测试 ───────────────────────────────────────────

_ALERTS_OK = {
    "code": "200",
    "warning": [
        {"title": "北京市气象台发布暴雨蓝色预警", "level": "蓝",
         "typeName": "暴雨", "text": "预计未来6小时有暴雨",
         "pubTime": "2026-06-20T10:00+08:00"},
        {"title": "沿海台风黄色预警", "level": "黄",
         "typeName": "台风", "text": "请做好防风防雨准备",
         "pubTime": "2026-06-20T09:00+08:00"},
    ],
}


def test_alerts_keeps_all_current_weather_warnings():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/v7/warning/now": _ALERTS_OK})
    res = asyncio.run(p.alerts("北京"))
    assert len(res) == 2
    assert res[0].title == "北京市气象台发布暴雨蓝色预警"
    assert res[0].level == "蓝"
    assert res[0].type_name == "暴雨"
    assert res[1].type_name == "台风"


def test_alerts_empty_when_no_warning():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/v7/warning/now": {"code": "200", "warning": []}})
    res = asyncio.run(p.alerts("北京"))
    assert res == []


# ── indices 测试 ─────────────────────────────────────────

_INDICES_OK = {
    "code": "200",
    "daily": [
        {"type": "1", "name": "运动指数", "category": "适宜", "text": "天气较好"},
        {"type": "3", "name": "洗车指数", "category": "较适宜", "text": "无雨"},
        {"type": "5", "name": "紫外线指数", "category": "弱", "text": "辐射弱"},
    ],
}


def test_indices_parses():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/v7/indices/1d": _INDICES_OK})
    res = asyncio.run(p.indices("北京"))
    assert len(res) == 3
    assert res[0].name == "运动指数" and res[0].level == "适宜"
    assert res[2].name == "紫外线指数"


# ── air_quality 测试 ─────────────────────────────────────

_AIR_CURRENT_OK = {
    "metadata": {"tag": "202606201000"},
    "indexes": [{
        "code": "cn-mep", "aqi": 52, "aqiDisplay": "52", "category": "良",
        "primaryPollutant": {"code": "pm2p5", "name": "PM2.5"},
    }],
    "pollutants": [
        {"code": "pm2p5", "concentration": {"value": 35, "unit": "μg/m³"}},
        {"code": "pm10", "concentration": {"value": 52, "unit": "μg/m³"}},
        {"code": "no2", "concentration": {"value": 20, "unit": "μg/m³"}},
        {"code": "o3", "concentration": {"value": 88, "unit": "μg/m³"}},
        {"code": "co", "concentration": {"value": 0.6, "unit": "mg/m³"}},
        {"code": "so2", "concentration": {"value": 5, "unit": "μg/m³"}},
    ],
}


def test_air_quality_uses_current_air_endpoint_and_parses_cn_index():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK,
                   "/airquality/v1/current/39.90/116.40": _AIR_CURRENT_OK},
                  jwt_auth=QWeatherJWT("proj", "kid", _ed25519_pem()))
    aq = asyncio.run(p.air_quality("北京"))
    assert aq.aqi == "52"
    assert aq.category == "良"
    assert aq.primary_pollutant == "PM2.5"
    assert aq.pm2p5 == "35"
    assert aq.pm10 == "52"
    assert p._spy.last["url"].endswith("/airquality/v1/current/39.90/116.40")


def test_current_air_quality_requires_jwt_authentication():
    p = _provider({"/geo/v2/city/lookup": _LOOKUP_OK})
    with pytest.raises(ProviderError, match="JWT"):
        asyncio.run(p.air_quality("北京"))


def test_overview_parses_extra_data_and_keeps_optional_sections():
    """一张天气卡只需一次城市解析，随后聚合所有和风天气信息。"""
    p = _provider({
        "/geo/v2/city/lookup": _LOOKUP_OK,
        "/v7/weather/now": _NOW_OK,
        "/v7/weather/3d": _FORECAST_3D_OK,
        "/airquality/v1/current/39.90/116.40": _AIR_CURRENT_OK,
        "/v7/indices/1d": _INDICES_OK,
        "/v7/warning/now": {"code": "200", "warning": []},
    }, jwt_auth=QWeatherJWT("proj", "kid", _ed25519_pem()))

    overview = asyncio.run(p.overview("北京"))

    assert overview.now.visibility == "10"
    assert overview.now.pressure == "1008"
    assert overview.forecast[0].uv_index == "6"
    assert overview.forecast[0].sunrise == "04:45"
    assert overview.air_quality.aqi == "52"
    assert len(overview.indices) == 3
    assert overview.alerts == []

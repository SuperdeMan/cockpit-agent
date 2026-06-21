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
import asyncio
import base64
import json
import logging
import time

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import (
    WeatherProvider, Weather,
    ForecastDay, WeatherAlert, LifeIndex, AirQuality, WeatherOverview,
)

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

    async def _get(self, path: str, params: dict, op: str, meta,
                   require_code: bool = True) -> dict:
        q = dict(params)
        headers = None
        if self._jwt is not None:
            headers = {"Authorization": f"Bearer {self._jwt.token()}"}
        else:
            q["key"] = self._api_key
        data = await self._http.get_json(
            f"{self._base}{path}", params=q, op=op, headers=headers, meta=meta)
        code = str(data.get("code"))
        if require_code and code != "200":
            raise ProviderError(f"qweather {op} failed: code={code}")
        return data

    async def _lookup_location(self, city: str, meta) -> tuple[str, str, float, float]:
        """城市名或 ``lng,lat`` → Location ID、城市名和空气质量接口所需坐标。"""
        data = await self._get("/geo/v2/city/lookup", {"location": city}, "city_lookup", meta)
        locs = data.get("location") or []
        if not locs:
            raise ProviderError(f"qweather city not found: {city}")
        top = locs[0]
        try:
            lat = float(top.get("lat"))
            lng = float(top.get("lon"))
        except (TypeError, ValueError):
            raise ProviderError(f"qweather city has no coordinates: {city}")
        return _s(top.get("id")), _s(top.get("name")) or city, lat, lng

    async def _lookup_city(self, city: str, meta) -> tuple[str, str]:
        """兼容现有天气接口：城市名 → (location_id, 规范城市名)。"""
        loc_id, city_name, _, _ = await self._lookup_location(city, meta)
        return loc_id, city_name

    async def _now_for_location(self, loc_id: str, city_name: str, meta) -> Weather:
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
            precip=_s(now.get("precip")),
            pressure=_s(now.get("pressure")),
            visibility=_s(now.get("vis")),
            cloud=_s(now.get("cloud")),
            dew_point=_s(now.get("dew")),
            update_time=_s(data.get("updateTime")),
        )

    async def now(self, city: str, meta: dict | None = None) -> Weather:
        loc_id, city_name = await self._lookup_city(city, meta)
        return await self._now_for_location(loc_id, city_name, meta)

    async def _forecast_for_location(self, loc_id: str, days: int, meta) -> list[ForecastDay]:
        # 和风 3天预报 / 7天预报；按 days 选 endpoint
        path = "/v7/weather/7d" if days > 3 else "/v7/weather/3d"
        data = await self._get(path, {"location": loc_id}, "weather_forecast", meta)
        result: list[ForecastDay] = []
        for d in (data.get("daily") or [])[:days]:
            result.append(ForecastDay(
                date=_s(d.get("fxDate")),
                text_day=_s(d.get("textDay")),
                text_night=_s(d.get("textNight")),
                temp_high=_s(d.get("tempMax")),
                temp_low=_s(d.get("tempMin")),
                wind_dir=_s(d.get("windDirDay")),
                wind_scale=_s(d.get("windScaleDay")),
                humidity=_s(d.get("humidity")),
                precip=_s(d.get("precip")),
                uv_index=_s(d.get("uvIndex")),
                sunrise=_s(d.get("sunrise")),
                sunset=_s(d.get("sunset")),
            ))
        return result

    async def forecast(self, city: str, days: int = 3,
                       meta: dict | None = None) -> list[ForecastDay]:
        loc_id, _ = await self._lookup_city(city, meta)
        return await self._forecast_for_location(loc_id, days, meta)

    async def _alerts_for_location(self, loc_id: str, meta) -> list[WeatherAlert]:
        """查询当前生效的天气预警，保留厂商返回的全部陆地/沿海预警。"""
        data = await self._get("/v7/warning/now", {"location": loc_id},
                               "warning_now", meta)
        result: list[WeatherAlert] = []
        for w in (data.get("warning") or []):
            result.append(WeatherAlert(
                title=_s(w.get("title")),
                level=_s(w.get("level")),
                type_name=_s(w.get("typeName")),
                text=_s(w.get("text")),
                pub_time=_s(w.get("pubTime")),
            ))
        return result

    async def alerts(self, city: str,
                     meta: dict | None = None) -> list[WeatherAlert]:
        loc_id, _ = await self._lookup_city(city, meta)
        return await self._alerts_for_location(loc_id, meta)

    async def _indices_for_location(self, loc_id: str, meta) -> list[LifeIndex]:
        """查询生活指数：运动(1)、洗车(3)、紫外线(5)。"""
        data = await self._get("/v7/indices/1d",
                               {"location": loc_id, "type": "1,3,5"},
                               "indices_1d", meta)
        result: list[LifeIndex] = []
        for d in (data.get("daily") or []):
            result.append(LifeIndex(
                category=_s(d.get("type")),
                name=_s(d.get("name")),
                level=_s(d.get("category")),
                text=_s(d.get("text")),
            ))
        return result

    async def indices(self, city: str,
                      meta: dict | None = None) -> list[LifeIndex]:
        loc_id, _ = await self._lookup_city(city, meta)
        return await self._indices_for_location(loc_id, meta)

    @staticmethod
    def _air_index(indexes: list[dict]) -> dict:
        """优先中国国标指数；其它地区回退厂商返回的首个本地指数。"""
        for index in indexes:
            if str(index.get("code", "")).lower() == "cn-mep":
                return index
        return indexes[0] if indexes else {}

    async def _air_quality_for_coordinates(self, lat: float, lng: float, meta) -> AirQuality:
        """查询新版实时空气质量。

        文档：GET /airquality/v1/current/{latitude}/{longitude}；新版仅依赖 HTTP
        状态码，响应体不再使用 V7 的 ``code`` / ``now`` 结构。
        """
        # ``/airquality/v1`` is a new-generation endpoint and, unlike the
        # legacy V7 APIs, accepts JWT authentication only.  Failing here gives
        # callers a useful configuration error instead of an opaque 401/403.
        if self._jwt is None:
            raise ProviderError("qweather air_current requires JWT authentication")
        path = f"/airquality/v1/current/{lat:.2f}/{lng:.2f}"
        data = await self._get(path, {}, "air_current", meta, require_code=False)
        index = self._air_index(data.get("indexes") or [])
        concentrations: dict[str, str] = {}
        for pollutant in data.get("pollutants") or []:
            code = _s(pollutant.get("code")).lower()
            concentration = pollutant.get("concentration") or {}
            value = concentration.get("value") if isinstance(concentration, dict) else concentration
            if code:
                concentrations[code] = _s(value)
        primary = index.get("primaryPollutant") or {}
        metadata = data.get("metadata") or {}
        return AirQuality(
            aqi=_s(index.get("aqiDisplay") or index.get("aqi")),
            category=_s(index.get("category")),
            primary_pollutant=_s(primary.get("name") or primary.get("code")),
            pm2p5=concentrations.get("pm2p5", ""),
            pm10=concentrations.get("pm10", ""),
            no2=concentrations.get("no2", ""),
            o3=concentrations.get("o3", ""),
            co=concentrations.get("co", ""),
            so2=concentrations.get("so2", ""),
            update_time=_s(metadata.get("updateTime") or metadata.get("tag")),
        )

    async def air_quality(self, city: str,
                          meta: dict | None = None) -> AirQuality:
        _, _, lat, lng = await self._lookup_location(city, meta)
        return await self._air_quality_for_coordinates(lat, lng, meta)

    async def overview(self, city: str,
                       meta: dict | None = None) -> WeatherOverview:
        """用一次城市 lookup 并发聚合天气卡所需的全部分区。"""
        loc_id, city_name, lat, lng = await self._lookup_location(city, meta)
        results = await asyncio.gather(
            self._now_for_location(loc_id, city_name, meta),
            self._forecast_for_location(loc_id, 3, meta),
            self._air_quality_for_coordinates(lat, lng, meta),
            self._indices_for_location(loc_id, meta),
            self._alerts_for_location(loc_id, meta),
            return_exceptions=True,
        )
        now, forecast, air_quality, indices, alerts = results
        if isinstance(now, Exception):
            raise now

        optional = {
            "forecast": forecast,
            "air_quality": air_quality,
            "indices": indices,
            "alerts": alerts,
        }
        for section, value in optional.items():
            if isinstance(value, Exception):
                logger.warning("qweather overview %s unavailable: %s", section, value)

        return WeatherOverview(
            now=now,
            forecast=[] if isinstance(forecast, Exception) else forecast,
            air_quality=AirQuality() if isinstance(air_quality, Exception) else air_quality,
            indices=[] if isinstance(indices, Exception) else indices,
            alerts=[] if isinstance(alerts, Exception) else alerts,
        )

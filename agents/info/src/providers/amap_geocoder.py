"""高德逆地理编码适配：只把已授权坐标转换成人类可读地点。"""
from __future__ import annotations

import os

from agents._sdk.http import AsyncHttpClient, ProviderError


class AmapGeocoder:
    def __init__(self, key: str, base_url: str = "https://restapi.amap.com"):
        if not key:
            raise ValueError("AMAP_KEY required for AmapGeocoder")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="amap", service="info")

    async def reverse(self, lng: float, lat: float, meta: dict | None = None) -> str:
        data = await self._http.get_json(
            f"{self._base}/v3/geocode/regeo",
            params={"key": self._key, "location": f"{lng:g},{lat:g}", "extensions": "base"},
            op="weather_reverse_geocode", meta=meta)
        if str(data.get("status")) != "1":
            raise ProviderError(
                f"amap weather_reverse_geocode failed: {data.get('info', 'unknown')}")
        return str((data.get("regeocode") or {}).get("formatted_address") or "")


class NoopGeocoder:
    async def reverse(self, lng: float, lat: float, meta: dict | None = None) -> str:
        return ""


def build_location_resolver():
    key = os.getenv("AMAP_KEY", "")
    return AmapGeocoder(key) if key else NoopGeocoder()

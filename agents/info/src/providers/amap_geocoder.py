"""高德逆地理编码适配：只把已授权坐标转换成人类可读地点。"""
from __future__ import annotations

import logging
import os
import time

from agents._sdk.http import AsyncHttpClient, ProviderError

logger = logging.getLogger(__name__)

#: **失败兜底**用的坐标键精度与有效期。3 位小数 ≈ 110 米：车在动时这个键几秒就变
#: （⇒ 拿不到旧值，老老实实重查），停着时才命中。TTL 再加一道上界。
_LKG_PRECISION = 3
_LKG_TTL_S = 600


class AmapGeocoder:
    def __init__(self, key: str, base_url: str = "https://restapi.amap.com"):
        if not key:
            raise ValueError("AMAP_KEY required for AmapGeocoder")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="amap", service="info")
        #: 同坐标上一次**成功**的反查结果（last known good）。
        #: ⚠ **刻意不是缓存**：成功路径一次都不走它，每次都真查。它只在
        #: 反查抛错那一支被读——因为那一支的替代品是**把地名整个丢掉**。
        self._last_good: dict[tuple[float, float], tuple[float, str]] = {}

    def _lkg_key(self, lng: float, lat: float) -> tuple[float, float]:
        return (round(lng, _LKG_PRECISION), round(lat, _LKG_PRECISION))

    async def reverse(self, lng: float, lat: float, meta: dict | None = None) -> str:
        """坐标 → 可读地址。**反查失败时退回同坐标的上一次成功结果**（有界）。

        ⚠ **2026-08-30 补失败兜底**（QA 长会话 `538335f` `information` T4，专项
        `--repeat 3` = 1/3 复现）：真栈原话
        「**当前位置**当前有1条天气预警：深圳市气象台发布暴雨黄色预警（黄级）。」
        ——同一条会话的前三轮（实况/预报/空气质量）都正确说了「深圳」。

        机制从代码可证：GPS meta 在场且没有显式 `city` 槽时，`_spoken_place`
        落到「当前位置」**当且仅当** `_display_city` 拿到空串，而那一支的唯一来源
        就是这里抛 `ProviderError`。⇒ **一次瞬时失败，把系统已经知道、而且几十秒前
        刚说对过的地名整个丢掉。**

        判据：**降级要降到「上一个正确答案」，不是降到「什么都不知道」**——
        坐标没变（键按 110 米粒度）、时间没走远（TTL 600s），那个名字仍然是对的。
        同族先例是 `openhours` 那条「判不出返回 None 不是 0」：
        两者都在说**别把「这次没拿到」写成「没有」**。
        """
        key = self._lkg_key(lng, lat)
        try:
            data = await self._http.get_json(
                f"{self._base}/v3/geocode/regeo",
                params={"key": self._key, "location": f"{lng:g},{lat:g}",
                        "extensions": "base"},
                op="weather_reverse_geocode", meta=meta)
            if str(data.get("status")) != "1":
                raise ProviderError(
                    f"amap weather_reverse_geocode failed: {data.get('info', 'unknown')}")
            name = str((data.get("regeocode") or {}).get("formatted_address") or "")
        except ProviderError:
            fallback = self._last_good.get(key)
            if fallback and (time.time() - fallback[0]) <= _LKG_TTL_S:
                logger.warning(
                    "reverse geocode failed; falling back to last known good "
                    "for the same coordinates (age=%.0fs)", time.time() - fallback[0])
                return fallback[1]
            raise
        if name:
            self._last_good[key] = (time.time(), name)
        return name


class NoopGeocoder:
    async def reverse(self, lng: float, lat: float, meta: dict | None = None) -> str:
        return ""


def build_location_resolver():
    key = os.getenv("AMAP_KEY", "")
    return AmapGeocoder(key) if key else NoopGeocoder()

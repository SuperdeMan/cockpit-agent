"""Mock 天气 Provider。PoC / 离线 / 单测用，返回确定性假数据。"""
from __future__ import annotations
from .base import WeatherProvider, Weather


class MockWeatherProvider(WeatherProvider):
    async def now(self, city: str, meta: dict | None = None) -> Weather:
        return Weather(
            city=city or "示例城市",
            temp="23",
            text="多云",
            feels_like="24",
            humidity="60",
            wind_dir="东南风",
            wind_scale="2",
            update_time="mock",
        )

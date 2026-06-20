"""信息 Agent（info）。当前提供实时天气（info.weather），预留 news/calendar/reminder。

Phase 1：使用 WeatherProvider 适配层（mock/real 可切换）。真实 provider 抖动时降级到 mock，
保证链路不阻断；失败本身由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from .providers import build_weather_provider
from .providers.mock import MockWeatherProvider

logger = logging.getLogger("agent.info")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class InfoAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.weather = build_weather_provider()
        self._fallback = MockWeatherProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "info.weather":
            return await self._weather(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个信息查询我还不会处理。")

    async def _weather(self, intent, ctx, meta) -> AgentResult:
        city = (intent.slots.get("city") or "").strip()
        if not city:
            # 未指定城市时，尝试用车辆当前位置（隐私最小化：只取需要的 scope）
            ctx_values = await ctx.fetch("vehicle.location")
            loc = ctx_values.get("vehicle.location", "")
            if isinstance(loc, str) and loc.strip():
                city = loc.strip()
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气？",
                               follow_up="请告诉我城市名", missing_slots=["city"])

        try:
            w = await self.weather.now(city, meta=meta)
        except ProviderError as e:
            logger.warning("weather query failed, fallback to mock: %s", e)
            w = await self._fallback.now(city, meta=meta)

        name = w.city or city
        parts = [f"{name}当前{w.text or '天气'}"]
        if w.temp:
            parts.append(f"，气温{w.temp}℃")
        if w.feels_like:
            parts.append(f"，体感{w.feels_like}℃")
        if w.wind_dir:
            parts.append(f"，{w.wind_dir}{w.wind_scale}级" if w.wind_scale else f"，{w.wind_dir}")
        speech = "".join(parts) + "。"

        card = {"type": "weather", "city": name, "temp": w.temp, "text": w.text,
                "feels_like": w.feels_like, "humidity": w.humidity,
                "wind_dir": w.wind_dir, "wind_scale": w.wind_scale,
                "update_time": w.update_time}
        return AgentResult(speech=speech, ui_card=card, data={"weather": card})

"""天气域：实时天气 / 预报 / 预警 / 生活指数 / 空气质量（和风 provider）。

城市解析/定位标注（_resolve_city/_display_city/_location_accuracy_note）留在 InfoAgent，
本 mixin 经 self 调用。
"""
from __future__ import annotations
import logging

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta

from ._util import _is_coordinate_label

logger = logging.getLogger("agent.info")


class WeatherMixin:
    async def _weather(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            overview = await self.weather.overview(city, meta=meta)
        except ProviderError as e:
            # 真实 provider 失败不再 fallback mock 假数据（无效城市/服务抖动会编出"当前未知的小雨"）。
            logger.warning("weather query failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"没查到「{asked}」的天气，可能是城市名不准确或天气服务暂时不可用，"
                                      f"换个城市名或稍后再试。")

        w = overview.now
        display_city = await self._display_city(intent, city, meta)
        provider_city = "" if _is_coordinate_label(w.city) else w.city
        name = display_city or provider_city or "当前位置"
        accuracy_note = self._location_accuracy_note(meta)
        parts = [f"{name}当前{w.text or '天气'}"]
        if w.temp:
            parts.append(f"，气温{w.temp}℃")
        if w.feels_like:
            parts.append(f"，体感{w.feels_like}℃")
        if w.wind_dir:
            parts.append(f"，{w.wind_dir}{w.wind_scale}级" if w.wind_scale else f"，{w.wind_dir}")
        if accuracy_note:
            parts.append(accuracy_note)
        speech = "".join(parts) + "。"

        forecast = [
            {"date": d.date, "text_day": d.text_day, "text_night": d.text_night,
             "temp_high": d.temp_high, "temp_low": d.temp_low,
             "wind_dir": d.wind_dir, "wind_scale": d.wind_scale,
             "humidity": d.humidity, "precip": d.precip, "uv_index": d.uv_index,
             "sunrise": d.sunrise, "sunset": d.sunset}
            for d in overview.forecast
        ]
        air_quality = {
            "aqi": overview.air_quality.aqi,
            "category": overview.air_quality.category,
            "pm2p5": overview.air_quality.pm2p5,
            "primary_pollutant": overview.air_quality.primary_pollutant,
        }
        indices = [
            {"name": idx.name, "level": idx.level, "text": idx.text}
            for idx in overview.indices[:3]
        ]
        alerts = [
            {"title": alert.title, "level": alert.level, "type": alert.type_name,
             "text": alert.text, "pub_time": alert.pub_time}
            for alert in overview.alerts
        ]
        card = {
            "type": "weather", "city": name, "temp": w.temp, "text": w.text,
            "feels_like": w.feels_like, "humidity": w.humidity,
            "wind_dir": w.wind_dir, "wind_scale": w.wind_scale,
            "precip": w.precip, "pressure": w.pressure, "visibility": w.visibility,
            "cloud": w.cloud, "dew_point": w.dew_point, "update_time": w.update_time,
            "forecast": forecast, "air_quality": air_quality,
            "indices": indices, "alerts": alerts,
            "alerts_available": overview.alerts_available,
        }
        return AgentResult(speech=speech, ui_card=card, data={"weather": card})

    async def _forecast(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预报？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        display_city = await self._display_city(intent, city, meta)
        name = display_city or ("当前位置" if current_location_from_meta(meta) else city)
        days = int(intent.slots.get("days", 3) or 3)
        try:
            forecast = await self.weather.forecast(city, days=days, meta=meta)
        except ProviderError as e:
            logger.warning("forecast failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"没查到「{asked}」的天气预报，可能是城市名不准确或服务暂时不可用，请稍后再试。")

        if not forecast:
            return AgentResult(speech=f"暂无{name}的天气预报数据。")

        parts = [f"{name}未来{len(forecast)}天天气预报："]
        for d in forecast:
            day_str = d.date[-5:] if len(d.date) >= 5 else d.date  # MM-DD
            parts.append(f"{day_str} {d.text_day}转{d.text_night}，"
                         f"{d.temp_low}~{d.temp_high}℃")
        speech = "；".join(parts) + "。"

        items = [{"date": d.date, "text_day": d.text_day, "text_night": d.text_night,
                  "temp_high": d.temp_high, "temp_low": d.temp_low,
                  "wind_dir": d.wind_dir, "wind_scale": d.wind_scale}
                 for d in forecast]
        card = {"type": "forecast", "city": name, "days": items}
        return AgentResult(speech=speech, ui_card=card, data={"forecast": items})

    async def _alerts(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预警？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        name = await self._display_city(intent, city, meta) or ("当前位置" if current_location_from_meta(meta) else city)
        try:
            alerts = await self.weather.alerts(city, meta=meta)
        except ProviderError as e:
            # 不可 fallback mock（会谎报"无预警"，预警是安全信息）。
            logger.warning("alerts failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的天气预警，请稍后再试。")

        if not alerts:
            return AgentResult(speech=f"{name}当前没有生效的天气预警。",
                               data={"alerts": []})

        parts = [f"{name}当前有{len(alerts)}条天气预警："]
        for a in alerts:
            parts.append(f"{a.title}（{a.level}级）")
        speech = "；".join(parts) + "。请注意防范。"

        items = [{"title": a.title, "level": a.level, "type": a.type_name,
                  "text": a.text, "pub_time": a.pub_time} for a in alerts]
        card = {"type": "weather_alerts", "city": name, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"alerts": items})

    async def _indices(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的生活指数？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            indices = await self.weather.indices(city, meta=meta)
        except ProviderError as e:
            logger.warning("indices failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的生活指数，请稍后再试。")

        if not indices:
            return AgentResult(speech=f"暂无{city}的生活指数数据。")

        parts = [f"{city}生活指数："]
        for idx in indices:
            parts.append(f"{idx.name} {idx.level}——{idx.text}")
        speech = "，".join(parts) + "。"

        items = [{"category": idx.category, "name": idx.name,
                  "level": idx.level, "text": idx.text} for idx in indices]
        card = {"type": "life_indices", "city": city, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"indices": items})

    async def _air_quality(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的空气质量？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            aq = await self.weather.air_quality(city, meta=meta)
        except ProviderError as e:
            logger.warning("air_quality failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的空气质量，请稍后再试。")

        parts = [f"{city}空气质量{aq.category or '未知'}"]
        if aq.aqi:
            parts.append(f"，AQI {aq.aqi}")
        if aq.pm2p5:
            parts.append(f"，PM2.5 {aq.pm2p5}μg/m³")
        if aq.primary_pollutant:
            parts.append(f"，首要污染物{aq.primary_pollutant}")
        speech = "".join(parts) + "。"

        card = {"type": "air_quality", "city": city, "aqi": aq.aqi,
                "category": aq.category, "pm2p5": aq.pm2p5, "pm10": aq.pm10,
                "primary_pollutant": aq.primary_pollutant,
                "no2": aq.no2, "o3": aq.o3, "co": aq.co, "so2": aq.so2,
                "update_time": aq.update_time}
        return AgentResult(speech=speech, ui_card=card, data={"air_quality": card})

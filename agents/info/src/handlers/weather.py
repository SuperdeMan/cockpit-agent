"""天气域：实时天气 / 预报 / 预警 / 生活指数 / 空气质量（和风 provider）。

城市解析/定位标注（_resolve_city/_display_city/_location_accuracy_note）留在 InfoAgent，
本 mixin 经 self 调用。
"""
from __future__ import annotations
from datetime import datetime
import logging
import re

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta

from ._util import _is_coordinate_label, _shanghai_now

logger = logging.getLogger("agent.info")


# ── 意图先答 + speech 可读性（badcase f555cde3：「未来几天会下雨吗」只回模板罗列，
#    且把完整逆地理地址整段念出、「预报：；」双标点）─────────────────────────

_CITY_RE = re.compile(r"(?:^|省|区)([^省市区县\s]{1,7}市)")
_DIST_RE = re.compile(r"市([^省市区县\s]{1,7}[区县])")


def _speech_place(name: str) -> str:
    """speech 地点名收敛：逆地理完整地址（省市区街道楼宇）收敛到「市+区」级，
    短名/非地址原样返回。只影响语音，卡片仍用完整名。"""
    n = (name or "").strip()
    if len(n) <= 9:
        return n
    city = _CITY_RE.search(n)
    dist = _DIST_RE.search(n)
    if city and dist:
        return city.group(1) + dist.group(1)
    if city:
        return city.group(1)
    return n[:9]


def _day_label(date_str: str) -> str:
    """YYYY-MM-DD → 今天/明天/后天/N号（相对上海时区今天；解析失败原样返回）。"""
    try:
        d = datetime.strptime((date_str or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return date_str or ""
    diff = (d - _shanghai_now().date()).days
    return {0: "今天", 1: "明天", 2: "后天"}.get(diff, f"{d.day}号")


def _max_wind_scale(forecast) -> int:
    top = 0
    for d in forecast:
        for m in re.findall(r"\d+", d.wind_scale or ""):
            top = max(top, int(m))
    return top


def _num(v) -> int | None:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


_GO_OUT_WORDS = ("出行", "出门", "出去", "上路")


def _weather_answer(raw: str, w, today, alerts) -> str:
    """实时天气的意图先答（badcase 11db5215：「今天天气怎么样，适合出行吗」只机械
    播报当前天气）：出行适宜性/雨/雪/冷热穿衣四类问法，依据实况+当日预报+预警给
    直接回答；泛问「天气怎么样」不加前导。纯确定性，零额外延迟/token。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    texts = " ".join(filter(None, [
        getattr(w, "text", ""), getattr(today, "text_day", "") if today else "",
        getattr(today, "text_night", "") if today else ""]))
    rainy, snowy = "雨" in texts, "雪" in texts
    feels = _num(getattr(w, "feels_like", "")) or _num(getattr(w, "temp", ""))
    if any(k in raw for k in _GO_OUT_WORDS):
        if alerts:
            a = alerts[0]
            kind = getattr(a, "type_name", "") or getattr(a, "title", "") or "天气"
            return f"今天有{kind}预警，出行请注意安全。"
        if rainy:
            return "可以出行，但今天有雨，记得带伞、路上慢行。"
        if snowy:
            return "今天有雪，出行注意路面湿滑、减速慢行。"
        if feels is not None and feels >= 33:
            return "适合出行，不过比较热，注意防晒和补水。"
        if feels is not None and feels <= 0:
            return "可以出行，但气温很低，注意保暖。"
        return "适合出行，今天天气不错。"
    if "雨" in raw or "伞" in raw:
        return "今天有雨，出门记得带伞。" if rainy else "今天没有降雨。"
    if "雪" in raw:
        return "今天有雪，注意路面湿滑。" if snowy else "今天不会下雪。"
    if any(k in raw for k in ("冷", "热", "穿")):
        if feels is None:
            return ""
        feel_desc = "比较热" if feels >= 30 else ("比较冷" if feels <= 10 else "体感比较舒适")
        return f"现在体感{feels}℃，{feel_desc}。"
    return ""


def _forecast_answer(raw: str, forecast) -> str:
    """意图先答：用户问「会不会下雨/下雪、冷不冷、风大不大、适不适合出行」时，先依据
    预报数据给直接回答，随后再接逐日摘要；罗列型问法（「未来三天天气」）不加前导。
    纯确定性规则，零额外延迟/token（天气域刻意不走 LLM 的既有取向）。"""
    raw = (raw or "").strip()
    if not raw or not forecast:
        return ""
    n = len(forecast)
    if any(k in raw for k in _GO_OUT_WORDS):
        hits = [d for d in forecast if "雨" in f"{d.text_day}{d.text_night}"
                or "雪" in f"{d.text_day}{d.text_night}"]
        if not hits:
            return f"未来{n}天没有雨雪，适合出行。"
        labels = "、".join(_day_label(d.date) for d in hits[:4])
        return f"可以出行，但{labels}有雨雪，记得带伞、路上慢行。"
    for kw, verb, tip in (("雨", "下雨", "出门记得带伞。"),
                          ("雪", "下雪", "注意路面湿滑。")):
        if kw in raw or (kw == "雨" and "伞" in raw):
            hits = [d for d in forecast if kw in f"{d.text_day}{d.text_night}"]
            if not hits:
                return f"未来{n}天都不会{verb}。"
            if len(hits) == n:
                return f"会{verb}，这{n}天每天都有{kw}，{tip}"
            labels = "、".join(_day_label(d.date) for d in hits[:4])
            return f"会{verb}，{labels}有{kw}，{tip}"
    if any(k in raw for k in ("冷", "热", "温度", "气温", "穿什么", "穿衣")):
        try:
            hi = max(int(d.temp_high) for d in forecast if d.temp_high)
            lo = min(int(d.temp_low) for d in forecast if d.temp_low)
        except ValueError:
            return ""
        feel = "白天比较热" if hi >= 30 else ("整体偏冷" if hi <= 10 else "体感比较舒适")
        return f"未来{n}天最低{lo}℃、最高{hi}℃，{feel}。"
    if "风" in raw:
        top = _max_wind_scale(forecast)
        if top >= 6:
            return f"未来{n}天风比较大，最高有{top}级，注意行车稳定。"
        if top > 0:
            return f"未来{n}天风都不大，最高{top}级。"
    return ""


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
        # 意图先答（适合出行吗/会下雨吗/冷不冷…）再接实况摘要（badcase 11db5215）
        lead = _weather_answer(intent.raw_text, w,
                               overview.forecast[0] if overview.forecast else None,
                               overview.alerts)
        parts = [lead, f"{_speech_place(name)}当前{w.text or '天气'}"]
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

        # 意图先答（会不会下雨/冷不冷…）+ 逐日摘要（今天/明天/后天，地点收敛到市区级）；
        # 「：」后直接接首日，修「预报：；」双标点。完整地址/ISO 日期仍在卡片。
        parts = [f"{_day_label(d.date)}{d.text_day}转{d.text_night}，"
                 f"{d.temp_low}~{d.temp_high}℃" for d in forecast]
        summary = f"{_speech_place(name)}未来{len(forecast)}天：" + "；".join(parts) + "。"
        speech = _forecast_answer(intent.raw_text, forecast) + summary

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

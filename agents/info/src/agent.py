"""信息 Agent（info）。实时天气 + 天气预报 + 预警 + 生活指数 + 联网搜索 + 新闻 + 股票。

Phase 1：使用 Provider 适配层（mock/real 可切换）。真实 provider 抖动时降级到 mock，
保证链路不阻断；失败本身由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from .providers import (
    build_weather_provider, build_search_provider,
    build_news_provider, build_stock_provider,
)
from .providers.mock import (
    MockWeatherProvider, MockSearchProvider,
    MockNewsProvider, MockStockProvider,
)
from .providers.amap_geocoder import build_location_resolver

logger = logging.getLogger("agent.info")

_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]|(?:\d+|[一二三四五六七八九十]+)[.、)）])\s*")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


def _shanghai_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone(timedelta(hours=8), name="Asia/Shanghai"))


def _fresh_search_query(query: str) -> str:
    """为“今晚/今天”的赛事赛程补齐日期，避免检索到历史届次背景资料。"""
    real_time_words = ("今晚", "今天", "今日")
    schedule_words = ("赛程", "赛程表", "比赛安排", "比赛")
    if any(word in query for word in real_time_words) and any(word in query for word in schedule_words):
        return f"{query} {_shanghai_now():%Y年%m月%d日} 当日赛程"
    return query


def _is_coordinate_label(value: str) -> bool:
    """防止 mock/异常上游把 ``lng,lat`` 直接展示给用户。"""
    try:
        lng, lat = str(value).split(",", 1)
        float(lng)
        float(lat)
        return True
    except (TypeError, ValueError):
        return False


class InfoAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.weather = build_weather_provider()
        self.search = build_search_provider()
        self.news = build_news_provider()
        self.stock = build_stock_provider()
        self.location_resolver = build_location_resolver()
        # 东方财富实时行情（免费无 key，全市场）：Tushare 无港美股权限时的降级
        try:
            from .providers.stock_eastmoney import EastMoneyStockProvider
            self._stock_eastmoney = EastMoneyStockProvider()
        except Exception:
            self._stock_eastmoney = None
        self._fallback_weather = MockWeatherProvider()
        self._fallback_search = MockSearchProvider()
        self._fallback_news = MockNewsProvider()
        self._fallback_stock = MockStockProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "info.weather": self._weather,
            "info.forecast": self._forecast,
            "info.alerts": self._alerts,
            "info.indices": self._indices,
            "info.air_quality": self._air_quality,
            "info.search": self._search,
            "info.news": self._news,
            "info.stock": self._stock,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个信息查询我还不会处理。")

    # ── 天气相关 ──────────────────────────────────────────────

    async def _resolve_city(self, intent, ctx, meta: dict | None = None) -> str:
        """从 intent slots 或车辆位置解析城市名。空串表示无法解析。"""
        city = (intent.slots.get("city") or "").strip()
        current = current_location_from_meta(meta)
        if not city and current:
            # 和风 GeoAPI 接受 ``lng,lat``，再由 Provider 解析为规范城市与空气接口坐标。
            city = f"{current.lng:.6f},{current.lat:.6f}"
        if not city:
            ctx_values = await ctx.fetch("vehicle.location")
            loc = ctx_values.get("vehicle.location", "")
            if isinstance(loc, str) and loc.strip():
                city = loc.strip()
        return city

    async def _display_city(self, intent, city: str, meta: dict | None = None) -> str:
        """坐标仅用于请求上游；展示时优先用高德反查出的可读地址。"""
        explicit_city = (intent.slots.get("city") or "").strip()
        if explicit_city:
            return explicit_city
        current = current_location_from_meta(meta)
        if current:
            try:
                return await self.location_resolver.reverse(current.lng, current.lat, meta)
            except ProviderError as e:
                logger.warning("weather reverse geocode unavailable: %s", e)
                return ""
        return city

    async def _weather(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            overview = await self.weather.overview(city, meta=meta)
        except ProviderError as e:
            logger.warning("weather query failed, fallback to mock: %s", e)
            overview = await self._fallback_weather.overview(city, meta=meta)

        w = overview.now
        display_city = await self._display_city(intent, city, meta)
        provider_city = "" if _is_coordinate_label(w.city) else w.city
        name = display_city or provider_city or "当前位置"
        parts = [f"{name}当前{w.text or '天气'}"]
        if w.temp:
            parts.append(f"，气温{w.temp}℃")
        if w.feels_like:
            parts.append(f"，体感{w.feels_like}℃")
        if w.wind_dir:
            parts.append(f"，{w.wind_dir}{w.wind_scale}级" if w.wind_scale else f"，{w.wind_dir}")
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
            logger.warning("forecast failed, fallback to mock: %s", e)
            forecast = await self._fallback_weather.forecast(city, days=days, meta=meta)

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
            logger.warning("alerts failed, fallback to mock: %s", e)
            alerts = await self._fallback_weather.alerts(city, meta=meta)

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
            logger.warning("indices failed, fallback to mock: %s", e)
            indices = await self._fallback_weather.indices(city, meta=meta)

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
            logger.warning("air_quality failed, fallback to mock: %s", e)
            aq = await self._fallback_weather.air_quality(city, meta=meta)

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

    # ── 联网搜索 ──────────────────────────────────────────────

    async def _summarize_sources(self, subject: str, source_kind: str,
                                 source_lines: list[str], fallback_points: list[str]) -> tuple[str, bool]:
        """把新鲜来源压缩成可播报结论，模型失效时仍不退化为标题清单。

        Returns: (speech_text, llm_used) — llm_used=False 表示走了 fallback。
        """
        time_context = ""
        is_live_request = any(word in subject for word in ("今晚", "今天", "今日"))
        is_schedule_request = any(word in subject for word in ("赛程", "赛程表", "比赛安排"))
        if is_live_request:
            time_context = (
                f"当前上海日期为{_shanghai_now():%Y年%m月%d日}。这是实时赛程/资讯问题："
                "只可依据与该日期相符的资料回答，不得用赛事背景、往届或下届信息替代。\n\n"
            )
        if is_schedule_request:
            time_context += (
                "用户要的是赛程：按时间列出资料中明确出现的对阵和开赛时间，"
                "最多四场；不要只说赛事背景或只挑其中一场。\n\n"
            )
        prompt = (
            f"用户关心：{subject}\n\n{time_context}"
            f"以下是刚查询到的{source_kind}资料：\n" + "\n".join(source_lines[:5]) + "\n\n"
            "请只依据这些资料，用中文给出结论式摘要。要求：\n"
            "1. 先说最重要的结论或进展，不要说‘根据搜索结果’\n"
            "2. 不罗列标题、链接、来源，也不要用编号或项目符号\n"
            "3. 适合车内语音播报，最多四句；资料不足时明确说明不确定性\n"
            "4. 不补充资料中没有的事实、数字、时间或因果关系"
        )
        try:
            answer = await self.llm.complete([
                {"role": "system", "content": "你是严谨的车载信息编辑，只能归纳提供的资料。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, max_tokens=260)
            answer = (answer or "").strip()
            if answer and not answer.startswith("[mock]"):
                # 模型偶尔仍会用列表模板；播报与卡片都需要连续结论，而不是标题罗列。
                answer = _LIST_MARKER.sub("", answer)
                answer = " ".join(line.strip() for line in answer.splitlines() if line.strip())
                if answer:
                    return answer, True  # LLM 成功
        except Exception as e:
            logger.warning("%s summary synthesis failed: %s", source_kind, e)

        points = [point.strip().rstrip("。") for point in fallback_points if point and point.strip()]
        lead = f"关于「{subject}」，" if subject else "当前热点主要是，"
        if points:
            return lead + "；".join(points[:2]) + "。", False  # LLM 失败，走 fallback
        return lead + "暂时没有足够资料形成可靠摘要。", False

    async def _search(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return AgentResult(status=NEED_SLOT, speech="您想搜什么？",
                               follow_up="请告诉我搜索内容", missing_slots=["query"])
        limit = int(intent.slots.get("limit", 5) or 5)
        provider_query = _fresh_search_query(query)
        try:
            results = await self.search.search(provider_query, limit=limit, meta=meta)
        except ProviderError as e:
            logger.warning("search failed: %s", e)
            return AgentResult(
                status=FAILED,
                speech="联网检索暂时不可用，无法确认最新结果，请稍后再试。",
            )

        if not results:
            return AgentResult(speech=f"没有找到关于「{query}」的搜索结果。")

        speech, llm_used = await self._summarize_sources(
            query, "联网搜索", [f"{r.title}（{r.source}）：{r.snippet}" for r in results],
            [r.snippet for r in results],
        )

        items = [{"title": r.title, "url": r.url, "snippet": r.snippet,
                  "source": r.source} for r in results]
        # ws2 search-news-redesign：LLM 成功用结论式 search_answer，失败退化为旧 search_list
        if llm_used:
            card = {
                "type": "search_answer",
                "query": query,
                "answer": speech,
                "sources": [{"title": r.title, "url": r.url, "source": r.source} for r in results],
                "items": items,
            }
        else:
            card = {"type": "search_list", "query": query, "summary": speech, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"items": items})

    # ── 新闻 ─────────────────────────────────────────────────

    async def _news(self, intent, ctx, meta) -> AgentResult:
        topic = (intent.slots.get("topic") or "").strip()
        limit = int(intent.slots.get("limit", 5) or 5)
        try:
            items_list = await self.news.headlines(topic=topic, limit=limit, meta=meta)
        except ProviderError as e:
            logger.warning("news failed, fallback to mock: %s", e)
            items_list = await self._fallback_news.headlines(topic=topic, limit=limit, meta=meta)

        if not items_list:
            return AgentResult(speech="暂无新闻资讯。")

        subject = topic or "今日热点"
        speech, llm_used = await self._summarize_sources(
            subject, "新闻", [f"{n.title}（{n.source}）：{n.summary}" for n in items_list],
            [n.summary for n in items_list],
        )

        items = [{"title": n.title, "summary": n.summary, "source": n.source,
                  "publish_time": n.publish_time} for n in items_list]
        # ws2 search-news-redesign：LLM 成功用摘要式 news_digest，失败退化为旧 news_list
        if llm_used:
            card = {
                "type": "news_digest",
                "topic": topic,
                "summary": speech,
                "headlines": [{"title": n.title, "source": n.source} for n in items_list[:3]],
                "items": items,
            }
        else:
            card = {"type": "news_list", "topic": topic, "summary": speech, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"items": items})

    # ── 股票 ─────────────────────────────────────────────────

    async def _stock(self, intent, ctx, meta) -> AgentResult:
        symbol = (intent.slots.get("symbol") or "").strip()
        if not symbol:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪只股票或指数？",
                               follow_up="请告诉我股票名称或代码", missing_slots=["symbol"])
        stock_provider = self.stock
        try:
            q = await self.stock.quote(symbol, meta=meta)
        except ProviderError as e:
            logger.warning("tushare quote failed: %s", e)
            # Tushare 失败（如无港美股权限）→ 降级到东方财富实时行情（免费，全市场）
            if self._stock_eastmoney:
                try:
                    q = await self._stock_eastmoney.quote_text(symbol, meta=meta)
                    stock_provider = self._stock_eastmoney  # history 也用东方财富
                except ProviderError as e2:
                    logger.warning("eastmoney quote also failed: %s", e2)
                    return AgentResult(
                        status=FAILED,
                        speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。"
                               f"您可以试试用代码查询，如「600519」（A股）、「00700」（港股）。",
                    )
            else:
                return AgentResult(
                    status=FAILED,
                    speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。",
                )
        try:
            candles = await stock_provider.history(symbol, limit=20, meta=meta)
        except ProviderError as e:
            # 报价仍然有价值；历史失败时不混用 mock K 线误导用户。
            logger.warning("stock history unavailable, leaving chart empty: %s", e)
            candles = []

        parts = [f"{q.name or symbol}"]
        if q.price:
            parts.append(f"当前价{q.price}")
        if q.change and q.change_pct:
            direction = "跌" if q.change.startswith("-") else "涨"
            parts.append(f"，{direction}{q.change}（{q.change_pct}）")
        speech = "".join(parts) + "。"

        card = {"type": "stock_quote", "name": q.name, "symbol": q.symbol,
                "price": q.price, "change": q.change, "change_pct": q.change_pct,
                "market_time": q.market_time,
                "candles": [
                    {"date": candle.date, "open": candle.open, "high": candle.high,
                     "low": candle.low, "close": candle.close, "volume": candle.volume}
                    for candle in candles
                ]}
        return AgentResult(speech=speech, ui_card=card, data={"quote": card})

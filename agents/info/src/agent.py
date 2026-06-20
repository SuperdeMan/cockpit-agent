"""信息 Agent（info）。实时天气 + 天气预报 + 预警 + 生活指数 + 联网搜索 + 新闻 + 股票。

Phase 1：使用 Provider 适配层（mock/real 可切换）。真实 provider 抖动时降级到 mock，
保证链路不阻断；失败本身由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from .providers import (
    build_weather_provider, build_search_provider,
    build_news_provider, build_stock_provider,
)
from .providers.mock import (
    MockWeatherProvider, MockSearchProvider,
    MockNewsProvider, MockStockProvider,
)

logger = logging.getLogger("agent.info")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class InfoAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.weather = build_weather_provider()
        self.search = build_search_provider()
        self.news = build_news_provider()
        self.stock = build_stock_provider()
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

    async def _resolve_city(self, intent, ctx) -> str:
        """从 intent slots 或车辆位置解析城市名。空串表示无法解析。"""
        city = (intent.slots.get("city") or "").strip()
        if not city:
            ctx_values = await ctx.fetch("vehicle.location")
            loc = ctx_values.get("vehicle.location", "")
            if isinstance(loc, str) and loc.strip():
                city = loc.strip()
        return city

    async def _weather(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            w = await self.weather.now(city, meta=meta)
        except ProviderError as e:
            logger.warning("weather query failed, fallback to mock: %s", e)
            w = await self._fallback_weather.now(city, meta=meta)

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

    async def _forecast(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预报？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        days = int(intent.slots.get("days", 3) or 3)
        try:
            forecast = await self.weather.forecast(city, days=days, meta=meta)
        except ProviderError as e:
            logger.warning("forecast failed, fallback to mock: %s", e)
            forecast = await self._fallback_weather.forecast(city, days=days, meta=meta)

        if not forecast:
            return AgentResult(speech=f"暂无{city}的天气预报数据。")

        parts = [f"{city}未来{len(forecast)}天天气预报："]
        for d in forecast:
            day_str = d.date[-5:] if len(d.date) >= 5 else d.date  # MM-DD
            parts.append(f"{day_str} {d.text_day}转{d.text_night}，"
                         f"{d.temp_low}~{d.temp_high}℃")
        speech = "；".join(parts) + "。"

        items = [{"date": d.date, "text_day": d.text_day, "text_night": d.text_night,
                  "temp_high": d.temp_high, "temp_low": d.temp_low,
                  "wind_dir": d.wind_dir, "wind_scale": d.wind_scale}
                 for d in forecast]
        card = {"type": "forecast", "city": city, "days": items}
        return AgentResult(speech=speech, ui_card=card, data={"forecast": items})

    async def _alerts(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预警？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            alerts = await self.weather.alerts(city, meta=meta)
        except ProviderError as e:
            logger.warning("alerts failed, fallback to mock: %s", e)
            alerts = await self._fallback_weather.alerts(city, meta=meta)

        if not alerts:
            return AgentResult(speech=f"{city}当前没有生效的天气预警。",
                               data={"alerts": []})

        parts = [f"{city}当前有{len(alerts)}条天气预警："]
        for a in alerts:
            parts.append(f"{a.title}（{a.level}级）")
        speech = "；".join(parts) + "。请注意防范。"

        items = [{"title": a.title, "level": a.level, "type": a.type_name,
                  "text": a.text, "pub_time": a.pub_time} for a in alerts]
        card = {"type": "weather_alerts", "city": city, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"alerts": items})

    async def _indices(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx)
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
        city = await self._resolve_city(intent, ctx)
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

    async def _search(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return AgentResult(status=NEED_SLOT, speech="您想搜什么？",
                               follow_up="请告诉我搜索内容", missing_slots=["query"])
        limit = int(intent.slots.get("limit", 5) or 5)
        try:
            results = await self.search.search(query, limit=limit, meta=meta)
        except ProviderError as e:
            logger.warning("search failed, fallback to mock: %s", e)
            results = await self._fallback_search.search(query, limit=limit, meta=meta)

        if not results:
            return AgentResult(speech=f"没有找到关于「{query}」的搜索结果。")

        # 用 LLM 从搜索结果合成直接回答（不是罗列链接）
        snippets = "\n".join(
            f"- {r.title}（{r.source}）：{r.snippet}" for r in results[:5]
        )
        synth_prompt = (
            f"用户问：{query}\n\n"
            f"以下是联网搜索到的参考资料：\n{snippets}\n\n"
            "请根据以上资料，直接回答用户的问题。要求：\n"
            "1. 给出直接的答案/结论，不要说'根据搜索结果'之类的废话\n"
            "2. 简洁口语化，适合语音播报，不超过 5 句话\n"
            "3. 如果是赛程/比分/行情等实时数据，直接列出关键数字\n"
            "4. 如果资料不足以回答，诚实说明"
        )
        try:
            speech = await self.llm.complete([
                {"role": "system", "content": "你是一个信息助手，根据搜索结果直接回答用户问题。"},
                {"role": "user", "content": synth_prompt},
            ], temperature=0.3, max_tokens=300)
        except Exception as e:
            logger.warning("search synthesis LLM failed, using raw results: %s", e)
            parts = [f"为您搜索到{len(results)}条结果："]
            for i, r in enumerate(results[:3], 1):
                parts.append(f"{i}. {r.title}——{r.snippet[:50]}")
            speech = " ".join(parts)

        items = [{"title": r.title, "url": r.url, "snippet": r.snippet,
                  "source": r.source} for r in results]
        card = {"type": "search_list", "query": query, "items": items}
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

        label = f"关于「{topic}」的" if topic else "今日"
        parts = [f"{label}热点新闻："]
        for i, n in enumerate(items_list[:3], 1):
            parts.append(f"{i}. {n.title}")
        speech = " ".join(parts)

        items = [{"title": n.title, "summary": n.summary, "source": n.source,
                  "publish_time": n.publish_time} for n in items_list]
        card = {"type": "news_list", "topic": topic, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"items": items})

    # ── 股票 ─────────────────────────────────────────────────

    async def _stock(self, intent, ctx, meta) -> AgentResult:
        symbol = (intent.slots.get("symbol") or "").strip()
        if not symbol:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪只股票或指数？",
                               follow_up="请告诉我股票名称或代码", missing_slots=["symbol"])
        try:
            q = await self.stock.quote(symbol, meta=meta)
        except ProviderError as e:
            logger.warning("stock quote failed, fallback to mock: %s", e)
            q = await self._fallback_stock.quote(symbol, meta=meta)

        parts = [f"{q.name or symbol}"]
        if q.price:
            parts.append(f"当前价{q.price}")
        if q.change and q.change_pct:
            direction = "跌" if q.change.startswith("-") else "涨"
            parts.append(f"，{direction}{q.change}（{q.change_pct}）")
        speech = "".join(parts) + "。"

        card = {"type": "stock_quote", "name": q.name, "symbol": q.symbol,
                "price": q.price, "change": q.change, "change_pct": q.change_pct,
                "market_time": q.market_time}
        return AgentResult(speech=speech, ui_card=card, data={"quote": card})

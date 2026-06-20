"""Mock 天气/搜索/新闻/股票 Provider。PoC / 离线 / 单测用，返回确定性假数据。"""
from __future__ import annotations
import datetime as _dt

from .base import (
    WeatherProvider, Weather,
    ForecastDay, WeatherAlert, LifeIndex,
    SearchProvider, SearchResult,
    NewsProvider, NewsItem,
    StockProvider, Quote,
)


class MockWeatherProvider(WeatherProvider):
    async def now(self, city: str, meta: dict | None = None) -> Weather:
        return Weather(
            city=city or "示例城市",
            temp="23", text="多云", feels_like="24",
            humidity="60", wind_dir="东南风", wind_scale="2",
            update_time="mock",
        )

    async def forecast(self, city: str, days: int = 3,
                       meta: dict | None = None) -> list[ForecastDay]:
        today = _dt.date.today()
        patterns = [("多云", "晴"), ("晴", "多云"), ("小雨", "阴")]
        return [
            ForecastDay(
                date=str(today + _dt.timedelta(days=i)),
                text_day=patterns[i % 3][0], text_night=patterns[i % 3][1],
                temp_high=str(26 + i), temp_low=str(18 + i),
                wind_dir="东南风", wind_scale="2", humidity="55",
            )
            for i in range(min(days, 3))
        ]

    async def alerts(self, city: str,
                     meta: dict | None = None) -> list[WeatherAlert]:
        return []  # mock 无预警

    async def indices(self, city: str,
                      meta: dict | None = None) -> list[LifeIndex]:
        return [
            LifeIndex(category="运动", name="运动指数", level="适宜", text="天气较好，适宜户外运动。"),
            LifeIndex(category="洗车", name="洗车指数", level="较适宜", text="未来一天无雨，适合洗车。"),
            LifeIndex(category="紫外线", name="紫外线指数", level="弱", text="辐射较弱，涂擦SPF12-15护肤品。"),
        ]


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"{query} - 示例结果{i}",
                url=f"https://example.com/search?q={query}&p={i}",
                snippet=f"这是关于「{query}」的第{i}条示例搜索结果摘要。",
                source="example.com",
            )
            for i in range(1, min(limit, 4) + 1)
        ]


class MockNewsProvider(NewsProvider):
    async def headlines(self, topic: str = "", limit: int = 5,
                        meta: dict | None = None) -> list[NewsItem]:
        t = topic or "热点"
        return [
            NewsItem(
                title=f"{t}新闻标题{i}",
                summary=f"这是关于{t}的第{i}条示例新闻摘要内容。",
                source="示例新闻社",
                publish_time="mock",
            )
            for i in range(1, min(limit, 4) + 1)
        ]


class MockStockProvider(StockProvider):
    async def quote(self, symbol: str,
                    meta: dict | None = None) -> Quote:
        return Quote(
            name=symbol or "示例股票", symbol=symbol or "000000",
            price="15.88", change="+0.32", change_pct="+2.06",
            market_time="mock",
        )

    async def index(self, name: str = "上证",
                    meta: dict | None = None) -> Quote:
        return Quote(
            name=f"{name}指数", symbol="000001",
            price="3250.68", change="+12.35", change_pct="+0.38",
            market_time="mock",
        )

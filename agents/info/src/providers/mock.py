"""Mock 天气/搜索/新闻/股票 Provider。PoC / 离线 / 单测用，返回确定性假数据。"""
from __future__ import annotations
import asyncio
import datetime as _dt

from .base import (
    WeatherProvider, Weather,
    ForecastDay, WeatherAlert, LifeIndex, AirQuality, WeatherOverview,
    SearchProvider, SearchResult,
    NewsProvider, NewsItem,
    StockProvider, Quote, StockCandle,
)


class MockWeatherProvider(WeatherProvider):
    @staticmethod
    def _profile(city: str) -> tuple[int, int, str]:
        """按地点稳定生成不同的离线样例，避免误导为各城市天气一致。"""
        seed = sum((index + 1) * ord(char) for index, char in enumerate(city or "当前位置"))
        return seed % 5, 16 + seed % 17, 42 + seed % 43

    async def overview(self, city: str,
                       meta: dict | None = None) -> WeatherOverview:
        now, forecast, air_quality, indices, alerts = await asyncio.gather(
            self.now(city, meta=meta),
            self.forecast(city, days=3, meta=meta),
            self.air_quality(city, meta=meta),
            self.indices(city, meta=meta),
            self.alerts(city, meta=meta),
        )
        return WeatherOverview(
            now=now, forecast=forecast, air_quality=air_quality,
            indices=indices, alerts=alerts,
        )

    async def now(self, city: str, meta: dict | None = None) -> Weather:
        condition_index, temp, humidity = self._profile(city)
        conditions = ["晴", "多云", "阴", "小雨", "雷阵雨"]
        winds = ["东风", "东南风", "南风", "西南风", "北风"]
        return Weather(
            city=city or "当前位置",
            temp=str(temp), text=conditions[condition_index], feels_like=str(temp + 1),
            humidity=str(humidity), wind_dir=winds[condition_index], wind_scale=str(1 + condition_index % 4),
            precip="0" if condition_index < 3 else str(condition_index - 2),
            pressure=str(1004 + condition_index * 2), visibility=str(10 + condition_index * 2),
            cloud=str(20 + condition_index * 15), dew_point=str(temp - 7),
            update_time="mock",
        )

    async def forecast(self, city: str, days: int = 3,
                       meta: dict | None = None) -> list[ForecastDay]:
        today = _dt.date.today()
        condition_index, temp, humidity = self._profile(city)
        patterns = [("多云", "晴"), ("晴", "多云"), ("小雨", "阴")]
        return [
            ForecastDay(
                date=str(today + _dt.timedelta(days=i)),
                text_day=patterns[(condition_index + i) % 3][0], text_night=patterns[(condition_index + i) % 3][1],
                temp_high=str(temp + 3 + i), temp_low=str(temp - 5 + i),
                wind_dir=["东风", "东南风", "南风", "西南风", "北风"][condition_index],
                wind_scale=str(1 + condition_index % 4), humidity=str(humidity),
                precip="0" if condition_index < 3 else str(condition_index - 2), uv_index=str(2 + condition_index), sunrise="05:20", sunset="18:45",
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

    async def air_quality(self, city: str,
                          meta: dict | None = None) -> AirQuality:
        _, _, humidity = self._profile(city)
        aqi = max(25, humidity + 4)
        category = "优" if aqi <= 50 else "良" if aqi <= 100 else "轻度污染"
        return AirQuality(
            aqi=str(aqi), category=category, primary_pollutant="PM2.5",
            pm2p5=str(max(12, aqi - 18)), pm10=str(aqi + 4), no2=str(12 + aqi % 16),
            o3=str(55 + aqi % 35), co="0.6", so2="5",
            update_time="mock",
        )


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

    async def history(self, symbol: str, limit: int = 20,
                      meta: dict | None = None) -> list[StockCandle]:
        count = max(2, min(limit, 20))
        start = _dt.date.today() - _dt.timedelta(days=count - 1)
        candles: list[StockCandle] = []
        for index in range(count):
            open_price = 15.20 + index * 0.03
            close_price = open_price + (0.18 if index % 3 else -0.11)
            candles.append(StockCandle(
                date=str(start + _dt.timedelta(days=index)),
                open=f"{open_price:.2f}", high=f"{max(open_price, close_price) + 0.16:.2f}",
                low=f"{min(open_price, close_price) - 0.13:.2f}", close=f"{close_price:.2f}",
                volume=str(8000 + index * 240),
            ))
        return candles

    async def index(self, name: str = "上证",
                    meta: dict | None = None) -> Quote:
        return Quote(
            name=f"{name}指数", symbol="000001",
            price="3250.68", change="+12.35", change_pct="+0.38",
            market_time="mock",
        )

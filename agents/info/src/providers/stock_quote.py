"""股票行情 Provider 适配（通用 REST 行情 API）。

凭证经 env(STOCK_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

当前适配通用行情接口（如 Alpha Vantage / 聚合数据等），返回统一 Quote 格式。
具体 API 按 STOCK_API_BASE 配置，默认使用 Alpha Vantage。
docs: https://www.alphavantage.co/documentation/
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import StockProvider, Quote, StockCandle, market_label

logger = logging.getLogger("agent.info.stock")

_BASE = "https://www.alphavantage.co"


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class QuoteStockProvider(StockProvider):
    def __init__(self, key: str, base_url: str = _BASE):
        if not key:
            raise ValueError("STOCK_API_KEY required for QuoteStockProvider")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="stock_api", service="info",
                                     timeout_s=5.0)

    async def quote(self, symbol: str,
                    meta: dict | None = None) -> Quote:
        """查询股票/指数实时行情。symbol 为代码（如 AAPL / 600519.SH）。"""
        data = await self._http.get_json(
            f"{self._base}/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol,
                    "apikey": self._key},
            op="stock_quote", meta=meta,
        )
        # Alpha Vantage 错误：含 "Error Message" 或 "Note"（频率限制）
        if "Error Message" in data:
            raise ProviderError(f"stock quote failed: {data['Error Message']}")
        if "Note" in data:
            raise ProviderError(f"stock quote rate limited: {data['Note'][:200]}")

        gq = data.get("Global Quote") or {}
        if not gq:
            raise ProviderError(f"stock quote: no data for {symbol}")

        return Quote(
            name=_s(gq.get("01. symbol")),
            symbol=_s(gq.get("01. symbol")),
            price=_s(gq.get("05. price")),
            change=_s(gq.get("09. change")),
            change_pct=_s(gq.get("10. change percent")),
            market_time=_s(gq.get("07. latest trading day")),
            market=market_label(_s(gq.get("01. symbol"))),
        )

    async def history(self, symbol: str, limit: int = 20,
                      meta: dict | None = None) -> list[StockCandle]:
        """Alpha Vantage 的日线字段统一成从旧到新的 OHLC 数据。"""
        data = await self._http.get_json(
            f"{self._base}/query",
            params={"function": "TIME_SERIES_DAILY", "symbol": symbol,
                    "outputsize": "compact", "apikey": self._key},
            op="stock_history", meta=meta,
        )
        if "Error Message" in data:
            raise ProviderError(f"stock history failed: {data['Error Message']}")
        if "Note" in data:
            raise ProviderError(f"stock history rate limited: {data['Note'][:200]}")
        series = data.get("Time Series (Daily)") or {}
        candles = [
            StockCandle(
                date=_s(day), open=_s(values.get("1. open")),
                high=_s(values.get("2. high")), low=_s(values.get("3. low")),
                close=_s(values.get("4. close")), volume=_s(values.get("5. volume")),
            )
            for day, values in series.items()
            if isinstance(values, dict)
        ]
        if not candles:
            raise ProviderError(f"stock history: no data for {symbol}")
        return sorted(candles, key=lambda candle: candle.date)[-max(1, min(limit, 100)):]

    async def index(self, name: str = "上证",
                    meta: dict | None = None) -> Quote:
        """查询大盘指数。名称映射到 Alpha Vantage symbol。"""
        _INDEX_MAP = {
            "上证": "000001.SS", "上证指数": "000001.SS",
            "深证": "399001.SZ", "深证成指": "399001.SZ",
            "沪深300": "000300.SS",
            "道琼斯": "DJI", "纳斯达克": "IXIC", "标普500": "SPX",
        }
        symbol = _INDEX_MAP.get(name, name)
        return await self.quote(symbol, meta=meta)

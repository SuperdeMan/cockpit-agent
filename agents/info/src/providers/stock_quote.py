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
from .base import StockProvider, Quote

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
        )

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

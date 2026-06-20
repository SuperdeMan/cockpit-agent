"""东方财富实时行情 Provider（免费无 key，支持 A 股/港股/美股）。

作为 Tushare 的降级方案：当 Tushare 免费 token 无港美股权限时自动使用。
东方财富行情 API 无需 key，通过 secid 参数指定市场：
  A 股: secid=1.600519(沪) / 0.000001(深)
  港股: secid=116.00700
  美股: secid=105.AAPL
docs: https://push2.eastmoney.com/api/qt/stock/get
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import StockProvider, Quote

logger = logging.getLogger("agent.info.stock_eastmoney")

_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def _s(v) -> str:
    return str(v) if v is not None else ""


class EastMoneyStockProvider(StockProvider):
    """东方财富实时行情（免费，全市场）。"""

    def __init__(self):
        self._http = AsyncHttpClient(vendor="eastmoney", service="info", timeout_s=5.0)
        # 东方财富 suggest API 用于名称→代码解析
        self._suggest_url = "https://searchapi.eastmoney.com/api/suggest/get"

    async def _resolve_secid(self, symbol: str, meta) -> tuple[str, str]:
        """symbol → (secid, display_name)。secid 用于东方财富行情 API。"""
        symbol = (symbol or "").strip()
        # 已是代码格式
        if symbol.isdigit():
            if len(symbol) == 6:
                prefix = "1" if symbol.startswith(("6", "5", "9")) else "0"
                return f"{prefix}.{symbol}", symbol
            if len(symbol) == 5:
                return f"116.{symbol}", symbol  # 港股
        if "." in symbol:
            return symbol, symbol

        # 名称搜索
        data = await self._http.get_json(
            self._suggest_url,
            params={"input": symbol, "type": "14", "count": "5"},
            op="stock_suggest", meta=meta,
        )
        items = ((data.get("QuotationCodeTable") or {}).get("Data") or [])
        if not items:
            raise ProviderError(f"eastmoney: no stock found for {symbol}")

        # 按市场优先：A股 > 港股 > 美股
        _RANK = {"AStock": 0, "": 0, "HKStock": 1, "HKIndex": 1, "USStock": 2, "USIndex": 2}
        items.sort(key=lambda x: _RANK.get(_s(x.get("Classify", "")), 3))
        item = items[0]
        code = _s(item.get("Code"))
        name = _s(item.get("Name")) or symbol
        classify = _s(item.get("Classify", "")).lower()

        if classify in ("hkstock", "hkindex"):
            secid = f"116.{code}"
        elif classify in ("usstock", "usindex"):
            secid = f"105.{code}"
        elif code.isdigit() and len(code) == 6:
            prefix = "1" if code.startswith(("6", "5", "9")) else "0"
            secid = f"{prefix}.{code}"
        else:
            raise ProviderError(f"eastmoney: unsupported market for {symbol}")

        return secid, name

    async def quote(self, symbol: str, meta=None) -> Quote:
        secid, name = await self._resolve_secid(symbol, meta)
        data = await self._http.get_json(
            _QUOTE_URL,
            params={"secid": secid, "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f169,f170"},
            op="quote", meta=meta,
        )
        d = data.get("data") or {}
        if not d:
            raise ProviderError(f"eastmoney: no quote data for {secid}")

        # 东方财富行情字段：f43=最新价(×100), f44=最高, f45=最低, f46=开盘
        # f169=涨跌额(×100), f170=涨跌幅(×100), f57=代码, f58=名称
        def _price(field):
            v = d.get(field)
            if v is None or v == "-":
                return ""
            try:
                return str(round(int(v) / 100, 2))
            except (ValueError, TypeError):
                return _s(v)

        return Quote(
            name=_s(d.get("f58")) or name,
            symbol=_s(d.get("f57")) or secid.split(".")[-1],
            price=_price("f43"),
            change=_price("f169"),
            change_pct=f"{_price('f170')}%" if _price("f170") else "",
            market_time="",
        )

    async def history(self, symbol: str, limit: int = 20, meta=None):
        raise ProviderError("eastmoney: history not supported, use tushare")

    async def index(self, name: str = "上证", meta=None) -> Quote:
        return await self.quote(name, meta=meta)

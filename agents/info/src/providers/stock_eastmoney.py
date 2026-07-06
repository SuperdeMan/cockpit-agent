"""新浪实时行情 Provider（免费无 key，支持 A 股/港股/美股）。

作为 Tushare 的降级方案：当 Tushare 免费 token 无港美股权限时自动使用。
东方财富 suggest API 做名称→代码解析，新浪行情 API 取实时数据。
  A 股: sh600519 / sz000001
  港股: hk00700
  美股: gb_aapl
docs: https://finance.sina.com.cn/realstock/company
"""
from __future__ import annotations
import logging
import re

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import StockProvider, Quote, market_label

logger = logging.getLogger("agent.info.stock_sina")

_SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
_EASTMONEY_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"


def _s(v) -> str:
    return str(v) if v is not None else ""


class EastMoneyStockProvider(StockProvider):
    """新浪实时行情 + 东方财富名称解析（免费，全市场）。"""

    def __init__(self):
        self._http = AsyncHttpClient(vendor="sina", service="info", timeout_s=5.0)
        self._suggest_http = AsyncHttpClient(vendor="eastmoney_suggest", service="info", timeout_s=5.0)

    async def _resolve_sina_code(self, symbol: str, meta) -> tuple[str, str]:
        """symbol → (sina_code, display_name)。sina_code 用于新浪行情 API。"""
        symbol = (symbol or "").strip()

        # 已是代码格式
        if symbol.isdigit():
            if len(symbol) == 6:
                prefix = "sh" if symbol.startswith(("6", "5", "9")) else "sz"
                return f"{prefix}{symbol}", symbol
            if len(symbol) == 5:
                return f"hk{symbol}", symbol  # 港股
        if "." in symbol:
            return symbol, symbol

        # 名称搜索（东方财富 suggest API）
        data = await self._suggest_http.get_json(
            _EASTMONEY_SUGGEST_URL,
            params={"input": symbol, "type": "14", "count": "5"},
            op="stock_suggest", meta=meta,
        )
        items = ((data.get("QuotationCodeTable") or {}).get("Data") or [])
        if not items:
            raise ProviderError(f"sina: no stock found for {symbol}")

        # 按市场优先：A股 > 港股 > 美股
        _RANK = {"astock": 0, "": 0, "hk": 1, "hkstock": 1, "hkindex": 1,
                 "usstock": 2, "usindex": 2}
        items.sort(key=lambda x: _RANK.get(_s(x.get("Classify", "")).lower(), 3))

        item = items[0]
        code = _s(item.get("Code"))
        name = _s(item.get("Name")) or symbol
        classify = _s(item.get("Classify", "")).lower()

        if classify in ("hkstock", "hkindex", "hk"):
            sina_code = f"hk{code}"
        elif classify in ("usstock", "usindex", "us"):
            sina_code = f"gb_{code.lower()}"
        elif code.isdigit() and len(code) == 6:
            prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
            sina_code = f"{prefix}{code}"
        else:
            raise ProviderError(f"sina: unsupported market for {symbol}")

        return sina_code, name

    async def quote(self, symbol: str, meta=None) -> Quote:
        sina_code, name = await self._resolve_sina_code(symbol, meta)

        # 新浪行情 API 返回格式：var hq_str_xxx="字段1,字段2,..."
        raw = await self._http.get_json(
            f"{_SINA_QUOTE_URL}{sina_code}",
            op="sina_quote", meta=meta,
            headers={"Referer": "https://finance.sina.com.cn"},
        )
        # raw 是 JSON 但实际是 JS 变量赋值字符串
        # httpx 会把它当 JSON 解析失败——改用纯文本请求
        # 实际上 get_json 期望 JSON，但新浪返回 JS。需要直接用 get_text。
        raise ProviderError("sina: use quote_text instead")

    async def quote_text(self, symbol: str, meta=None) -> Quote:
        """用纯文本方式调新浪 API 并解析。"""
        sina_code, name = await self._resolve_sina_code(symbol, meta)

        # 直接 HTTP GET 拿文本
        resp = await self._http._client.get(
            f"{_SINA_QUOTE_URL}{sina_code}",
            headers={"Referer": "https://finance.sina.com.cn"},
        )
        text = resp.text

        # 解析：var hq_str_xxx="字段1,字段2,..."
        m = re.search(r'"([^"]*)"', text)
        if not m or not m.group(1):
            raise ProviderError(f"sina: empty response for {sina_code}")
        fields = m.group(1).split(",")

        # 根据 sina_code 前缀判断格式
        if sina_code.startswith("hk"):
            return self._parse_hk(fields, name, sina_code)
        elif sina_code.startswith("gb_"):
            return self._parse_us(fields, name, sina_code)
        else:
            return self._parse_a(fields, name, sina_code)

    @staticmethod
    def _parse_a(fields: list, name: str, code: str) -> Quote:
        # A股: 名称,昨收,今开,最新价,...（字段0=名称, 1=昨收, 2=今开, 3=最新价）
        if len(fields) < 4:
            raise ProviderError("sina: insufficient A-share data")
        price = _s(fields[3])
        prev_close = float(fields[1]) if fields[1] else 0
        cur = float(fields[3]) if fields[3] else 0
        change = round(cur - prev_close, 2) if prev_close else 0
        pct = round(change / prev_close * 100, 2) if prev_close else 0
        return Quote(
            name=_s(fields[0]) or name,
            symbol=code.replace("sh", "").replace("sz", ""),
            price=price,
            change=f"{change:+.2f}",
            change_pct=f"{pct:+.2f}%",
            market=market_label(code),
        )

    @staticmethod
    def _parse_hk(fields: list, name: str, code: str) -> Quote:
        # 港股: 英文名,中文名,昨收,今开,最高,最低,最新价,涨跌额,涨跌幅,...
        if len(fields) < 9:
            raise ProviderError("sina: insufficient HK data")
        return Quote(
            name=_s(fields[1]) or name,
            symbol=code.replace("hk", ""),
            price=_s(fields[6]),
            change=_s(fields[7]),
            change_pct=f"{_s(fields[8])}%" if fields[8] else "",
            market="港股",
        )

    @staticmethod
    def _parse_us(fields: list, name: str, code: str) -> Quote:
        # 美股: 中文名,最新价,涨跌幅,时间,涨跌额,...
        if len(fields) < 5:
            raise ProviderError("sina: insufficient US data")
        return Quote(
            name=_s(fields[0]) or name,
            symbol=code.replace("gb_", "").upper(),
            price=_s(fields[1]),
            change=_s(fields[4]),
            change_pct=f"{_s(fields[2])}%" if fields[2] else "",
            market="美股",
        )

    async def history(self, symbol: str, limit: int = 20, meta=None):
        raise ProviderError("sina: history not supported, use tushare")

    async def index(self, name: str = "上证", meta=None) -> Quote:
        return await self.quote_text(name, meta=meta)

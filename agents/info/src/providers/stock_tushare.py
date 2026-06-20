"""Tushare 股票行情 Provider 适配。

凭证经 env(TUSHARE_TOKEN) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

Tushare 统一 POST 到 https://api.tushare.pro，body 含 api_name/token/params/fields。
daily 接口获取日线行情（最近一个交易日）；stock_basic 获取公司名称。
docs: https://tushare.pro/document/1?doc_id=5
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import StockProvider, Quote, StockCandle

logger = logging.getLogger("agent.info.stock_tushare")

_API_URL = "https://api.tushare.pro"
_EASTMONEY_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"


def _s(v) -> str:
    return str(v) if v is not None else ""


class TushareStockProvider(StockProvider):
    def __init__(self, token: str, api_url: str = _API_URL):
        if not token:
            raise ValueError("TUSHARE_TOKEN required for TushareStockProvider")
        self._token = token
        self._url = api_url
        self._http = AsyncHttpClient(vendor="tushare", service="info",
                                     timeout_s=5.0)
        self._lookup_http = AsyncHttpClient(vendor="eastmoney", service="info",
                                            timeout_s=5.0)
        self._name_to_code: dict[str, str] = {}
        self._code_to_name: dict[str, str] = {}

    async def _post(self, api_name: str, params: dict,
                    fields: str = "", op: str = "tushare",
                    meta: dict | None = None) -> dict:
        body = {"api_name": api_name, "token": self._token, "params": params}
        if fields:
            body["fields"] = fields
        data = await self._http.post_json(self._url, json_body=body,
                                          op=op, meta=meta)
        # Tushare 错误：code!=0 或 msg 非空
        code = data.get("code", 0)
        if code != 0:
            msg = data.get("msg", "unknown error")
            raise ProviderError(f"tushare {api_name} failed: {msg}")
        return data

    def _latest_trade_date(self) -> str:
        """最近交易日（往前推 1 天，跳过周末）。格式 YYYYMMDD。"""
        d = datetime.now()
        # 如果当前时间是周末或当天还未收盘（15:00前），往前多推
        if d.weekday() >= 5:  # 周六日
            d -= timedelta(days=d.weekday() - 4)
        elif d.hour < 15:  # 当天未收盘，用前一天
            d -= timedelta(days=1)
            if d.weekday() >= 5:
                d -= timedelta(days=d.weekday() - 4)
        else:
            # 已收盘，用当天（但如果是周一到周五）
            pass
        return d.strftime("%Y%m%d")

    @staticmethod
    def _daily_api(ts_code: str) -> str:
        """按 ts_code 后缀返回对应的 Tushare 日线 API 名。"""
        if ts_code.endswith(".HK"):
            return "hk_daily"
        if ts_code.endswith(".US"):
            return "us_daily"
        return "daily"

    async def _get_stock_name(self, ts_code: str, meta) -> str:
        """通过 stock_basic 获取股票名称。"""
        if ts_code in self._code_to_name:
            return self._code_to_name[ts_code]
        try:
            data = await self._post("stock_basic",
                                    {"ts_code": ts_code},
                                    fields="ts_code,name",
                                    op="stock_basic", meta=meta)
            items = (data.get("data") or {}).get("items") or []
            if items:
                # fields: [ts_code, name]
                name = _s(items[0][1]) if len(items[0]) > 1 else ts_code
                if name:
                    self._code_to_name[ts_code] = name
                    self._name_to_code.setdefault(name, ts_code)
                return name or ts_code
        except Exception:
            pass
        return ts_code

    def _normalize_ts_code(self, symbol: str) -> str:
        """把指数名/证券代码归一为 ts_code；中文名称交给异步查表。"""
        # 常见指数映射
        _INDEX_MAP = {
            "上证": "000001.SH", "上证指数": "000001.SH",
            "深证": "399001.SZ", "深证成指": "399001.SZ",
            "沪深300": "000300.SH", "创业板": "399006.SZ",
            "科创50": "000688.SH",
        }
        if symbol in _INDEX_MAP:
            return _INDEX_MAP[symbol]
        # 已经是标准格式（如 000001.SZ / 600519.SH）
        if "." in symbol:
            return symbol
        if symbol.isdigit() and len(symbol) == 6 and symbol.startswith("6"):
            return f"{symbol}.SH"
        if symbol.isdigit() and len(symbol) == 6:
            return f"{symbol}.SZ"
        return ""

    async def _resolve_ts_code(self, symbol: str, meta) -> str:
        """把中文股票名解析成真实 ts_code，并在进程内缓存映射。"""
        symbol = (symbol or "").strip()
        direct = self._normalize_ts_code(symbol)
        if direct:
            return direct
        if symbol in self._name_to_code:
            return self._name_to_code[symbol]

        data = await self._lookup_http.get_json(
            _EASTMONEY_SUGGEST_URL,
            params={"input": symbol, "type": "14", "count": "10"},
            op="stock_lookup", meta=meta,
        )
        items = ((data.get("QuotationCodeTable") or {}).get("Data") or [])
        matches = [item for item in items if _s(item.get("Name")) == symbol]
        if not matches:
            matches = [
                item for item in items
                if symbol in _s(item.get("Name"))
            ]
        if not matches:
            # 无名称匹配时，取第一条 A 股结果（如果有）
            matches = [item for item in items
                       if _s(item.get("Classify")) in ("", "AStock")]
        if len(matches) != 1:
            raise ProviderError(f"stock lookup: no unambiguous code found for {symbol}")

        item = matches[0]
        code = _s(item.get("Code"))
        classify = _s(item.get("Classify", ""))
        # 按市场归一化 ts_code（支持 A 股/港股/美股）
        if classify in ("HKStock", "HKIndex", "HK"):
            # 港股：5 位数字代码 → .HK（如 00700 → 00700.HK）
            ts_code = f"{code}.HK"
        elif classify in ("USStock", "USIndex", "US"):
            # 美股：字母代码 → .US（如 AAPL → AAPL.US）
            ts_code = f"{code.upper()}.US"
        elif code.isdigit() and len(code) == 6:
            # A 股：6 位数字
            if code.startswith(("6", "5", "9")):
                ts_code = f"{code}.SH"
            elif code.startswith(("4", "8")):
                ts_code = f"{code}.BJ"
            else:
                ts_code = f"{code}.SZ"
        else:
            raise ProviderError(f"stock lookup: unsupported market for {symbol} (code={code}, classify={classify})")
        name = _s(item.get("Name")) or symbol
        self._name_to_code[symbol] = ts_code
        self._code_to_name[ts_code] = name
        return ts_code

    @staticmethod
    def _candle_from_row(row: list) -> StockCandle:
        """Tushare daily fields: ts_code, date, open, high, low, close, ..., vol."""
        return StockCandle(
            date=_s(row[1]) if len(row) > 1 else "",
            open=_s(row[2]) if len(row) > 2 else "",
            high=_s(row[3]) if len(row) > 3 else "",
            low=_s(row[4]) if len(row) > 4 else "",
            close=_s(row[5]) if len(row) > 5 else "",
            volume=_s(row[9]) if len(row) > 9 else "",
        )

    async def history(self, symbol: str, limit: int = 20,
                      meta: dict | None = None) -> list[StockCandle]:
        """拉取一个有限交易日窗口，规整为前端 K 线所需的正序 OHLC 数据。"""
        ts_code = await self._resolve_ts_code(symbol, meta)
        api_name = self._daily_api(ts_code)
        end_date = self._latest_trade_date()
        start_date = (datetime.strptime(end_date, "%Y%m%d")
                      - timedelta(days=max(14, min(limit, 60) * 3))).strftime("%Y%m%d")
        data = await self._post(
            api_name, {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            fields=_DAILY_FIELDS, op="daily_history", meta=meta,
        )
        rows = (data.get("data") or {}).get("items") or []
        candles = [self._candle_from_row(row) for row in rows]
        candles = [candle for candle in candles if candle.date and candle.close]
        if not candles:
            raise ProviderError(f"tushare: no daily history for {ts_code}")
        return sorted(candles, key=lambda candle: candle.date)[-max(1, min(limit, 60)):]

    async def quote(self, symbol: str,
                    meta: dict | None = None) -> Quote:
        ts_code = await self._resolve_ts_code(symbol, meta)
        api_name = self._daily_api(ts_code)
        trade_date = self._latest_trade_date()
        # 先取日线数据，再取公司名
        data = await self._post(
            api_name,
            {"ts_code": ts_code, "trade_date": trade_date},
            fields=_DAILY_FIELDS,
            op="daily", meta=meta,
        )
        items = (data.get("data") or {}).get("items") or []
        if not items:
            # 可能非交易日，尝试往前找
            for offset in range(1, 5):
                d = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=offset)
                alt = await self._post(
                    api_name,
                    {"ts_code": ts_code, "trade_date": d.strftime("%Y%m%d")},
                    fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg",
                    op="daily", meta=meta,
                )
                items = (alt.get("data") or {}).get("items") or []
                if items:
                    break
        if not items:
            raise ProviderError(f"tushare: no daily data for {ts_code}")

        # fields 索引：ts_code=0, trade_date=1, open=2, high=3, low=4, close=5,
        # pre_close=6, change=7, pct_chg=8, vol=9, amount=10
        row = items[0]
        close = _s(row[5]) if len(row) > 5 else ""
        change = _s(row[7]) if len(row) > 7 else ""
        pct_chg = _s(row[8]) if len(row) > 8 else ""
        trade_dt = _s(row[1]) if len(row) > 1 else ""

        # 获取股票名称
        name = await self._get_stock_name(ts_code, meta)

        return Quote(
            name=name,
            symbol=ts_code,
            price=close,
            change=change,
            change_pct=f"{pct_chg}%" if pct_chg else "",
            market_time=trade_dt,
        )

    async def index(self, name: str = "上证",
                    meta: dict | None = None) -> Quote:
        return await self.quote(name, meta=meta)

"""TushareStockProvider 单测：mock 掉底层 HTTP，喂 Tushare 黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.stock_tushare import TushareStockProvider


def _provider(responses: dict):
    p = TushareStockProvider(token="test-token")

    async def fake_post_json(url, json_body=None, op="post", headers=None, meta=None):
        api_name = (json_body or {}).get("api_name", "")
        for key, val in responses.items():
            if key == api_name:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for api_name={api_name}")

    p._http.post_json = fake_post_json
    return p


_DAILY_OK = {
    "code": 0,
    "data": {
        "fields": ["ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount"],
        "items": [["600519.SH", "20260620", "1860.00", "1900.50",
                   "1855.00", "1888.00", "1862.50", "25.50", "1.37",
                   "12345", "234567.89"]],
    },
}

_STOCK_BASIC_OK = {
    "code": 0,
    "data": {
        "fields": ["ts_code", "name"],
        "items": [["600519.SH", "贵州茅台"]],
    },
}


def test_quote_parses():
    p = _provider({"daily": _DAILY_OK, "stock_basic": _STOCK_BASIC_OK})
    q = asyncio.run(p.quote("600519.SH"))
    assert q.name == "贵州茅台"
    assert q.symbol == "600519.SH"
    assert q.price == "1888.00"
    assert q.change == "25.50"
    assert q.change_pct == "1.37%"
    assert q.market_time == "20260620"


def test_quote_resolves_short_code():
    """6位纯数字自动补 .SH/.SZ。"""
    p = _provider({"daily": _DAILY_OK, "stock_basic": _STOCK_BASIC_OK})
    q = asyncio.run(p.quote("600519"))
    assert q.symbol == "600519.SH"  # 6 开头 → SH


def test_quote_error_raises():
    p = _provider({"daily": {"code": -1, "msg": "token invalid"}})
    with pytest.raises(ProviderError, match="token invalid"):
        asyncio.run(p.quote("600519.SH"))


def test_quote_no_data_raises():
    p = _provider({"daily": {"code": 0, "data": {"fields": [], "items": []}}})
    with pytest.raises(ProviderError, match="no daily data"):
        asyncio.run(p.quote("999999.SZ"))


def test_index_maps_chinese_name():
    p = _provider({"daily": _DAILY_OK, "stock_basic": _STOCK_BASIC_OK})
    q = asyncio.run(p.index("上证"))
    assert q.symbol == "000001.SH"  # 上证指数

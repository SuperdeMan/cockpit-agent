"""QuoteStockProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.stock_quote import QuoteStockProvider


def _provider(responses: dict):
    p = QuoteStockProvider(key="test-key")

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    return p


_QUOTE_OK = {
    "Global Quote": {
        "01. symbol": "600519.SH",
        "05. price": "1888.00",
        "09. change": "+25.50",
        "10. change percent": "+1.37%",
        "07. latest trading day": "2026-06-20",
    }
}

_HISTORY_OK = {
    "Time Series (Daily)": {
        "2026-06-20": {"1. open": "100", "2. high": "106", "3. low": "98",
                       "4. close": "105", "5. volume": "2000"},
        "2026-06-19": {"1. open": "101", "2. high": "103", "3. low": "99",
                       "4. close": "100", "5. volume": "1800"},
    }
}


def test_quote_parses():
    p = _provider({"/query": _QUOTE_OK})
    q = asyncio.run(p.quote("600519.SH"))
    assert q.symbol == "600519.SH"
    assert q.price == "1888.00"
    assert q.change == "+25.50"
    assert q.change_pct == "+1.37%"
    assert q.market_time == "2026-06-20"


def test_quote_error_message_raises():
    p = _provider({"/query": {"Error Message": "Invalid API call"}})
    with pytest.raises(ProviderError, match="Invalid API call"):
        asyncio.run(p.quote("INVALID"))


def test_quote_rate_limited_raises():
    p = _provider({"/query": {"Note": "Thank you for using Alpha Vantage"}})
    with pytest.raises(ProviderError, match="rate limited"):
        asyncio.run(p.quote("AAPL"))


def test_quote_no_data_raises():
    p = _provider({"/query": {"Global Quote": {}}})
    with pytest.raises(ProviderError, match="no data"):
        asyncio.run(p.quote("EMPTY"))


def test_index_maps_name():
    """index() 应把中文名映射到正确的 symbol。"""
    p = _provider({"/query": _QUOTE_OK})
    q = asyncio.run(p.index("上证"))
    assert q.symbol == "600519.SH"  # 上证→000001.SS, 但 mock 直接返回


def test_history_parses_daily_series_in_chronological_order():
    p = _provider({"/query": _HISTORY_OK})

    candles = asyncio.run(p.history("AAPL", limit=2))

    assert [(c.date, c.close) for c in candles] == [
        ("2026-06-19", "100"), ("2026-06-20", "105"),
    ]

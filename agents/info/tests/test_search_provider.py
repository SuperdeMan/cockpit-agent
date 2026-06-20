"""BingSearchProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.search_bing import BingSearchProvider


def _provider(responses: dict):
    p = BingSearchProvider(key="test-key")

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    return p


_SEARCH_OK = {
    "webPages": {
        "value": [
            {"name": "人工智能 - 维基百科", "url": "https://zh.wikipedia.org/wiki/AI",
             "snippet": "人工智能是计算机科学的一个分支", "displayUrl": "zh.wikipedia.org/wiki/AI"},
            {"name": "什么是AI", "url": "https://example.com/ai",
             "snippet": "AI即人工智能", "displayUrl": "example.com/ai"},
        ]
    }
}


def test_search_parses_results():
    p = _provider({"/v7.0/search": _SEARCH_OK})
    res = asyncio.run(p.search("人工智能", limit=5))
    assert len(res) == 2
    assert res[0].title == "人工智能 - 维基百科"
    assert "wikipedia" in res[0].url
    assert res[0].snippet
    assert res[1].source == "example.com"


def test_search_error_response_raises():
    err = {"_type": "ErrorResponse", "errors": [{"message": "Invalid key"}]}
    p = _provider({"/v7.0/search": err})
    with pytest.raises(ProviderError, match="Invalid key"):
        asyncio.run(p.search("test"))


def test_search_empty_returns_empty():
    p = _provider({"/v7.0/search": {"webPages": {"value": []}}})
    res = asyncio.run(p.search("不存在的内容"))
    assert res == []

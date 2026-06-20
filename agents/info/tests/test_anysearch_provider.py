"""AnySearchProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.search_any import AnySearchProvider


def _provider(responses: dict):
    p = AnySearchProvider(key="test-key")

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
    "results": [
        {"title": "人工智能最新进展", "url": "https://example.com/ai",
         "snippet": "AI 技术取得重大突破", "source": "example.com"},
        {"title": "AI 应用场景", "url": "https://example.com/ai-apps",
         "snippet": "AI 正在改变各行各业", "source": "example.com"},
    ]
}


def test_search_parses_results():
    p = _provider({"/v1/search": _SEARCH_OK})
    res = asyncio.run(p.search("人工智能", limit=5))
    assert len(res) == 2
    assert res[0].title == "人工智能最新进展"
    assert res[0].url == "https://example.com/ai"
    assert res[0].snippet == "AI 技术取得重大突破"
    assert res[0].source == "example.com"


def test_search_error_raises():
    p = _provider({"/v1/search": {"error": "Invalid API key"}})
    with pytest.raises(ProviderError, match="Invalid API key"):
        asyncio.run(p.search("test"))


def test_search_empty_returns_empty():
    p = _provider({"/v1/search": {"results": []}})
    res = asyncio.run(p.search("不存在的内容"))
    assert res == []


def test_search_alt_data_field():
    """兼容 'data' 字段名（不同版本可能用不同 key）。"""
    alt = {"data": [{"title": "测试", "link": "https://t.cn/1",
                     "description": "描述", "domain": "t.cn"}]}
    p = _provider({"/v1/search": alt})
    res = asyncio.run(p.search("测试"))
    assert len(res) == 1
    assert res[0].title == "测试"
    assert res[0].url == "https://t.cn/1"

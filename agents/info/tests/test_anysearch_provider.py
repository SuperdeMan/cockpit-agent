"""AnySearchProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.search_any import AnySearchProvider


def _provider(responses: dict):
    p = AnySearchProvider(key="test-key")

    async def fake_post_json(url, json_body=None, op="post", headers=None, meta=None):
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.post_json = fake_post_json
    return p


_SEARCH_OK = {
    "code": 0,
    "message": "success",
    "data": {
        "results": [
            {"title": "人工智能最新进展", "url": "https://example.com/ai",
             "snippet": "AI 技术取得重大突破", "source": "example.com"},
            {"title": "AI 应用场景", "url": "https://example.com/ai-apps",
             "snippet": "AI 正在改变各行各业", "source": "example.com"},
        ]
    }
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
    p = _provider({"/v1/search": {"code": 40003, "message": "Invalid request"}})
    with pytest.raises(ProviderError, match="Invalid request"):
        asyncio.run(p.search("test"))


def test_search_empty_returns_empty():
    p = _provider({"/v1/search": {"code": 0, "data": {"results": []}}})
    res = asyncio.run(p.search("不存在的内容"))
    assert res == []


def test_search_allows_a_single_long_lived_request_for_live_results():
    p = AnySearchProvider(key="test-key")

    assert p._http.max_retries == 0
    assert p._http._client.timeout.read == 10.0

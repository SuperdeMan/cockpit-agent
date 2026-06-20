"""NewsAPIProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.news_api import NewsAPIProvider


def _provider(responses: dict):
    p = NewsAPIProvider(key="test-key")

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    return p


_TOP_HEADLINES_OK = {
    "status": "ok",
    "articles": [
        {"title": "今日热点新闻一", "description": "这是摘要内容",
         "source": {"name": "新华社"}, "publishedAt": "2026-06-20T10:00:00Z"},
        {"title": "今日热点新闻二", "description": "第二条摘要",
         "source": {"name": "人民日报"}, "publishedAt": "2026-06-20T09:00:00Z"},
    ]
}

_EVERYTHING_OK = {
    "status": "ok",
    "articles": [
        {"title": "科技新闻一", "description": "科技摘要",
         "source": {"name": "36氪"}, "publishedAt": "2026-06-20T08:00:00Z"},
    ]
}


def test_headlines_no_topic_uses_top_headlines():
    p = _provider({"/top-headlines": _TOP_HEADLINES_OK})
    res = asyncio.run(p.headlines("", limit=5))
    assert len(res) == 2
    assert res[0].title == "今日热点新闻一"
    assert res[0].source == "新华社"
    assert res[0].publish_time.startswith("2026-06-20")


def test_headlines_with_topic_uses_everything():
    p = _provider({"/everything": _EVERYTHING_OK})
    res = asyncio.run(p.headlines("科技", limit=5))
    assert len(res) == 1
    assert "科技" in res[0].title


def test_headlines_bad_status_raises():
    p = _provider({"/top-headlines": {"status": "error", "message": "apiKey invalid"}})
    with pytest.raises(ProviderError, match="apiKey invalid"):
        asyncio.run(p.headlines())


def test_headlines_empty_returns_empty():
    p = _provider({"/top-headlines": {"status": "ok", "articles": []}})
    res = asyncio.run(p.headlines())
    assert res == []

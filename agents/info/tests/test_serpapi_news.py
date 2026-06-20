"""SerpApiNewsProvider 单测：mock 掉底层 HTTP，喂 SerpApi 黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.news_serpapi import SerpApiNewsProvider


def _provider(responses: dict, anysearch=None):
    p = SerpApiNewsProvider(serpapi_key="test-key", anysearch_provider=anysearch)

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        engine = (params or {}).get("engine", "")
        for key, val in responses.items():
            if key == engine:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for engine={engine}")

    p._http.get_json = fake_get_json
    return p


_GOOGLE_NEWS_OK = {
    "news_results": [
        {"title": "AI Breakthrough in 2026", "link": "https://example.com/ai",
         "snippet": "Major AI advances reported",
         "source": {"name": "TechCrunch"}, "iso_date": "2026-06-20T10:00:00Z"},
        {"title": "Global Tech Summit", "link": "https://example.com/summit",
         "snippet": "Leaders gather for tech summit",
         "source": {"name": "Reuters"}, "iso_date": "2026-06-20T09:00:00Z"},
    ]
}

_BAIDU_NEWS_OK = {
    "organic_results": [
        {"title": "国内科技新闻一", "link": "https://example.cn/1",
         "snippet": "这是国内新闻摘要",
         "source": "新华社", "date": "2小时前"},
        {"title": "国内科技新闻二", "link": "https://example.cn/2",
         "snippet": "第二条国内新闻",
         "source": "人民日报", "date": "3小时前"},
    ]
}


def test_google_news_parses():
    p = _provider({"google_news": _GOOGLE_NEWS_OK})
    items = asyncio.run(p.headlines("AI technology", limit=5))
    assert len(items) == 2
    assert items[0].title == "AI Breakthrough in 2026"
    assert items[0].source == "TechCrunch"
    assert items[0].publish_time.startswith("2026-06-20")


def test_baidu_news_for_chinese_topic():
    """中文话题优先走 Baidu News。"""
    p = _provider({"baidu_news": _BAIDU_NEWS_OK})
    items = asyncio.run(p.headlines("科技新闻", limit=5))
    assert len(items) == 2
    assert items[0].source == "新华社"


def test_google_fallback_when_baidu_fails():
    """Baidu 失败时降级到 Google。"""
    p = _provider({
        "baidu_news": ProviderError("baidu failed"),
        "google_news": _GOOGLE_NEWS_OK,
    })
    items = asyncio.run(p.headlines("科技", limit=5))
    assert len(items) == 2
    assert items[0].source == "TechCrunch"


def test_anysearch_fallback_when_both_fail():
    """SerpApi 全部失败时走 AnySearch 兜底。"""
    from agents.info.src.providers.search_any import AnySearchProvider
    any_p = AnySearchProvider(key="test")

    async def fake_search(query, limit=5, meta=None):
        from agents.info.src.providers.base import SearchResult
        return [SearchResult(title="AnySearch 兜底结果", url="https://any.com/1",
                             snippet="兜底摘要", source="any.com")]

    any_p.search = fake_search

    p = _provider({
        "baidu_news": ProviderError("baidu failed"),
        "google_news": ProviderError("google failed"),
    }, anysearch=any_p)
    items = asyncio.run(p.headlines("科技", limit=5))
    assert len(items) == 1
    assert items[0].title == "AnySearch 兜底结果"


def test_all_fail_raises():
    """全部失败（含无 AnySearch）应抛 ProviderError。"""
    p = _provider({
        "baidu_news": ProviderError("baidu failed"),
        "google_news": ProviderError("google failed"),
    }, anysearch=None)
    with pytest.raises(ProviderError, match="all news providers failed"):
        asyncio.run(p.headlines("test", limit=5))

"""ExaSearchProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents.info.src.providers.search_exa import ExaSearchProvider, _domain


def _provider(responses: dict, **kw):
    p = ExaSearchProvider(key="test-key", **kw)

    async def fake_post_json(url, json_body=None, op="post", headers=None, meta=None):
        fake_post_json.last_body = json_body
        fake_post_json.last_headers = headers
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.post_json = fake_post_json
    return p


_SEARCH_OK = {
    "requestId": "r1",
    "results": [
        {"title": "Exa 正文级检索", "url": "https://www.example.com/a",
         "publishedDate": "2026-06-22T08:00:00.000Z",
         "text": "这是网页正文内容，足够长，可供接地合成。", "summary": "摘要A"},
        {"title": "第二条", "url": "https://news.example.org/b",
         "publishedDate": "2026-06-21T10:00:00.000Z",
         "text": "第二篇正文。"},
    ],
}


def test_search_parses_results_with_content_and_published():
    p = _provider({"/search": _SEARCH_OK})
    res = asyncio.run(p.search("人工智能", limit=5))
    assert len(res) == 2
    assert res[0].title == "Exa 正文级检索"
    assert res[0].url == "https://www.example.com/a"
    assert res[0].content == "这是网页正文内容，足够长，可供接地合成。"
    assert res[0].published == "2026-06-22T08:00:00.000Z"
    assert res[0].source == "example.com"      # www. 去掉
    assert res[1].source == "news.example.org"


def test_search_sends_content_recency_and_category():
    p = _provider({"/search": _SEARCH_OK})
    asyncio.run(p.search("今天新闻", limit=3, recency_days=2, category="news"))
    body = p._http.post_json.last_body
    assert body["contents"]["text"]["maxCharacters"] > 0
    assert body["numResults"] == 3
    assert body["category"] == "news"
    assert "startPublishedDate" in body         # 时效窗口已下发
    assert p._http.post_json.last_headers["x-api-key"] == "test-key"


def test_livecrawl_sent_only_when_requested():
    p = _provider({"/search": _SEARCH_OK})
    asyncio.run(p.search("世界杯射手榜", livecrawl="preferred"))
    assert p._http.post_json.last_body["contents"]["livecrawl"] == "preferred"
    p2 = _provider({"/search": _SEARCH_OK})
    asyncio.run(p2.search("普通查询"))
    assert "livecrawl" not in p2._http.post_json.last_body["contents"]


def test_invalid_category_is_dropped():
    p = _provider({"/search": _SEARCH_OK})
    asyncio.run(p.search("x", category="not-a-category"))
    assert "category" not in p._http.post_json.last_body
    assert "startPublishedDate" not in p._http.post_json.last_body  # recency_days=0 不下发


def test_snippet_falls_back_to_text_when_no_summary():
    resp = {"results": [{"title": "t", "url": "https://e.com/x",
                         "text": "正文没有摘要时用正文截断作 snippet。"}]}
    p = _provider({"/search": resp})
    res = asyncio.run(p.search("q"))
    assert res[0].snippet.startswith("正文")


def test_empty_results():
    p = _provider({"/search": {"results": []}})
    assert asyncio.run(p.search("无结果")) == []


def test_missing_key_raises():
    with pytest.raises(ValueError):
        ExaSearchProvider(key="")


def test_domain_helper():
    assert _domain("https://www.fifa.com/x") == "fifa.com"
    assert _domain("https://sub.a.com") == "sub.a.com"
    assert _domain("not a url") == ""

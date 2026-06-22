"""Exa 联网搜索 Provider —— 返回正文级内容，供接地合成（修 R2：不再只读 snippet）。

凭证经 env(EXA_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
工厂/Agent 据此回退 AnySearch/Bing/mock，不击穿主链。

为什么用 Exa：
- ``contents.text`` 直接随搜索返回网页正文（无需二次抓取），是「ChatGPT 级」答复的原料前提。
- neural 检索对自然语言查询友好，省掉 AnySearch/Google 那套关键词拼接（修 R3）。
- ``startPublishedDate`` 做时效过滤，实时类查询不混入历史资料。

API: POST https://api.exa.ai/search
Auth: header ``x-api-key``
Body: {query, type, numResults, contents:{text:{maxCharacters}}, startPublishedDate?, category?}
Resp: {results:[{title,url,publishedDate,author,text,summary,...}]}
docs: https://exa.ai/docs/reference/search
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from agents._sdk.http import AsyncHttpClient
from .base import SearchProvider, SearchResult

logger = logging.getLogger("agent.info.search_exa")

_BASE = "https://api.exa.ai"
# Exa 支持的 category 白名单（仅在命中时透传，避免无效值被拒）
_CATEGORIES = {"news", "company", "research paper", "personal site",
               "financial report", "people"}


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


class ExaSearchProvider(SearchProvider):
    def __init__(self, key: str, base_url: str = "", max_chars: int = 1800):
        if not key:
            raise ValueError("EXA_API_KEY required for ExaSearchProvider")
        self._key = key
        self._base = (base_url or _BASE).rstrip("/")
        self._max_chars = max_chars
        # 取正文比纯搜索慢，给 12s；不重试，避免把网络波动当“没有结果”重发同一查询。
        self._http = AsyncHttpClient(vendor="exa", service="info",
                                     timeout_s=12.0, max_retries=0)

    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None, *,
                     recency_days: int = 0, category: str = "",
                     **kwargs) -> list[SearchResult]:
        body: dict = {
            "query": query,
            "type": "auto",
            "numResults": max(1, min(limit, 15)),
            "contents": {"text": {"maxCharacters": self._max_chars}},
        }
        if recency_days and recency_days > 0:
            start = datetime.now(timezone.utc) - timedelta(days=recency_days)
            body["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if category in _CATEGORIES:
            body["category"] = category

        data = await self._http.post_json(
            f"{self._base}/search", json_body=body,
            headers={"x-api-key": self._key}, op="web_search", meta=meta,
        )
        results_list = data.get("results") or []
        results: list[SearchResult] = []
        for item in results_list[:limit]:
            text = _s(item.get("text"))
            url = _s(item.get("url"))
            # snippet 给短摘要：优先 Exa summary，否则正文截断
            snippet = _s(item.get("summary")) or (text[:200] if text else "")
            results.append(SearchResult(
                title=_s(item.get("title")),
                url=url,
                snippet=snippet,
                source=_domain(url),
                published=_s(item.get("publishedDate")),
                content=text,
            ))
        return results

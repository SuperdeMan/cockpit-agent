"""Bing Web Search 联网搜索 Provider 适配。

凭证经 env(BING_SEARCH_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

docs: https://learn.microsoft.com/en-us/bing/search-apis/bing-web-search/overview
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import SearchProvider, SearchResult

logger = logging.getLogger("agent.info.search_bing")

_BASE = "https://api.bing.microsoft.com"


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class BingSearchProvider(SearchProvider):
    def __init__(self, key: str, base_url: str = _BASE):
        if not key:
            raise ValueError("BING_SEARCH_KEY required for BingSearchProvider")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="bing", service="info")

    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None, **kwargs) -> list[SearchResult]:
        data = await self._http.get_json(
            f"{self._base}/v7.0/search",
            params={"q": query, "count": str(max(1, min(limit, 20))),
                    "mkt": "zh-CN", "responseFilter": "Webpages"},
            headers={"Ocp-Apim-Subscription-Key": self._key},
            op="web_search", meta=meta,
        )
        # Bing 返回 HTTP 200 但 body 可能含 _type=="ErrorResponse"
        if data.get("_type") == "ErrorResponse":
            msg = (data.get("errors") or [{}])[0].get("message", "unknown")
            raise ProviderError(f"bing search failed: {msg}")

        results: list[SearchResult] = []
        for item in (data.get("webPages", {}).get("value") or [])[:limit]:
            results.append(SearchResult(
                title=_s(item.get("name")),
                url=_s(item.get("url")),
                snippet=_s(item.get("snippet")),
                source=_s(item.get("displayUrl", "").split("/")[0]
                          if item.get("displayUrl") else ""),
            ))
        return results

"""AnySearch 联网搜索 Provider 适配。

凭证经 env(ANYSEARCH_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

AnySearch 定位：AI Search Infrastructure for Agents。
API: POST https://api.anysearch.com/v1/search
Body: {"query": "...", "limit": N}
Auth: Bearer token
Response: {"code": 0, "data": {"results": [{title, url, snippet, ...}]}}
docs: https://anysearch.com/docs#search-api
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import SearchProvider, SearchResult

logger = logging.getLogger("agent.info.search_any")

_BASE = "https://api.anysearch.com"


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class AnySearchProvider(SearchProvider):
    def __init__(self, key: str, base_url: str = ""):
        if not key:
            raise ValueError("ANYSEARCH_API_KEY required for AnySearchProvider")
        self._key = key
        self._base = (base_url or _BASE).rstrip("/")
        # 实时赛程等检索常在 3 秒后才返回；保留一条 10 秒请求，避免 3 秒超时后
        # 立即重发相同查询并把短暂网络波动伪装成“没有结果”。
        self._http = AsyncHttpClient(vendor="anysearch", service="info",
                                     timeout_s=10.0, max_retries=0)

    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None) -> list[SearchResult]:
        # AnySearch 用 POST，body 字段为 query（不是 q）
        data = await self._http.post_json(
            f"{self._base}/v1/search",
            json_body={"query": query, "limit": max(1, min(limit, 20))},
            headers={"Authorization": f"Bearer {self._key}"},
            op="web_search", meta=meta,
        )
        # 响应格式：{"code": 0, "data": {"results": [...]}}
        if data.get("code") != 0:
            msg = data.get("message") or data.get("error") or "unknown error"
            raise ProviderError(f"anysearch failed: {msg}")

        results_list = (data.get("data") or {}).get("results") or []
        results: list[SearchResult] = []
        for item in results_list[:limit]:
            results.append(SearchResult(
                title=_s(item.get("title")),
                url=_s(item.get("url") or item.get("link")),
                snippet=_s(item.get("snippet") or item.get("description")),
                source=_s(item.get("source") or item.get("domain", "")),
            ))
        return results

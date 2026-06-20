"""AnySearch 联网搜索 Provider 适配。

凭证经 env(ANYSEARCH_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

AnySearch 定位：AI Search Infrastructure for Agents。
docs: https://anysearch.com/docs#search-api

⚠️ 端点基于常见搜索 API 模式推断，用户需确认实际 endpoint 后按需调整。
   若 endpoint 不同，改 _BASE 和 _SEARCH_PATH 即可。
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import SearchProvider, SearchResult

logger = logging.getLogger("agent.info.search_any")

# AnySearch API 基础地址（用户需确认，可经 ANYSEARCH_BASE_URL env 覆盖）
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
        self._http = AsyncHttpClient(vendor="anysearch", service="info")

    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None) -> list[SearchResult]:
        data = await self._http.get_json(
            f"{self._base}/v1/search",
            params={"q": query, "limit": str(max(1, min(limit, 20)))},
            headers={"Authorization": f"Bearer {self._key}"},
            op="web_search", meta=meta,
        )
        # AnySearch 响应格式推断：{ results: [{ title, url, snippet, source }] }
        # 若实际格式不同，调整下方解析逻辑即可
        results_list = data.get("results") or data.get("data") or []
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"anysearch failed: {data['error']}")

        results: list[SearchResult] = []
        for item in results_list[:limit]:
            results.append(SearchResult(
                title=_s(item.get("title")),
                url=_s(item.get("url") or item.get("link")),
                snippet=_s(item.get("snippet") or item.get("description")),
                source=_s(item.get("source") or item.get("domain", "")),
            ))
        return results

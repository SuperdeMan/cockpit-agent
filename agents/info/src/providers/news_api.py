"""NewsAPI.org 新闻 Provider 适配。

凭证经 env(NEWS_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

docs: https://newsapi.org/docs
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import NewsProvider, NewsItem

logger = logging.getLogger("agent.info.news_api")

_BASE = "https://newsapi.org/v2"


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class NewsAPIProvider(NewsProvider):
    def __init__(self, key: str, base_url: str = _BASE):
        if not key:
            raise ValueError("NEWS_API_KEY required for NewsAPIProvider")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="newsapi", service="info")

    async def headlines(self, topic: str = "", limit: int = 5,
                        meta: dict | None = None) -> list[NewsItem]:
        # 有 topic → everything 端点（关键词搜索）；无 topic → top-headlines（热点）
        if topic:
            data = await self._http.get_json(
                f"{self._base}/everything",
                params={"q": topic, "pageSize": str(max(1, min(limit, 20))),
                        "language": "zh", "sortBy": "publishedAt"},
                headers={"X-Api-Key": self._key},
                op="news_everything", meta=meta,
            )
        else:
            data = await self._http.get_json(
                f"{self._base}/top-headlines",
                params={"country": "cn", "pageSize": str(max(1, min(limit, 20)))},
                headers={"X-Api-Key": self._key},
                op="news_top_headlines", meta=meta,
            )
        # NewsAPI: status!="ok" 即失败
        if data.get("status") != "ok":
            raise ProviderError(f"newsapi failed: {data.get('message', 'unknown')}")

        results: list[NewsItem] = []
        for a in (data.get("articles") or [])[:limit]:
            results.append(NewsItem(
                title=_s(a.get("title")),
                summary=_s(a.get("description")),
                source=_s((a.get("source") or {}).get("name")),
                publish_time=_s(a.get("publishedAt")),
            ))
        return results

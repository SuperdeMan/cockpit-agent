"""SerpApi 新闻 Provider 适配（Google News + Baidu News，AnySearch 兜底）。

凭证经 env(SERPAPI_API_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

策略：国际新闻走 Google News（engine=google_news），国内新闻走 Baidu News
（engine=baidu_news），两者均失败时走 AnySearch 兜底。
docs: https://serpapi.com/google-news-api | https://serpapi.com/baidu-news-api

AnySearch 兜底需 ANYSEARCH_API_KEY，无则跳过。
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import NewsProvider, NewsItem

if TYPE_CHECKING:
    from .search_any import AnySearchProvider

logger = logging.getLogger("agent.info.news_serpapi")

_SERPAPI_BASE = "https://serpapi.com"


def _s(v) -> str:
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class SerpApiNewsProvider(NewsProvider):
    def __init__(self, serpapi_key: str, anysearch_provider=None,
                 base_url: str = _SERPAPI_BASE):
        if not serpapi_key:
            raise ValueError("SERPAPI_API_KEY required for SerpApiNewsProvider")
        self._key = serpapi_key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="serpapi", service="info",
                                     timeout_s=5.0)
        self._anysearch = anysearch_provider  # AnySearch 兜底

    async def _serpapi_search(self, engine: str, query: str, limit: int,
                              meta: dict | None = None) -> list[NewsItem]:
        """调 SerpApi 的 Google/Baidu News 接口。"""
        data = await self._http.get_json(
            f"{self._base}/search",
            params={"engine": engine, "q": query, "api_key": self._key,
                    "hl": "zh-cn", "gl": "cn", "output": "json"},
            op=f"serpapi_{engine}", meta=meta,
        )
        # 检查错误
        if data.get("error"):
            raise ProviderError(f"serpapi {engine} failed: {data['error']}")

        # Google News: news_results[]; Baidu News: organic_results[]
        results_key = ("news_results" if engine == "google_news"
                       else "organic_results")
        raw = data.get(results_key) or []

        items: list[NewsItem] = []
        for a in raw[:limit]:
            # 统一字段映射
            source_obj = a.get("source") or {}
            source_name = (source_obj.get("name") if isinstance(source_obj, dict)
                           else _s(source_obj)) or ""
            items.append(NewsItem(
                title=_s(a.get("title")),
                summary=_s(a.get("snippet") or a.get("description", "")),
                source=source_name,
                publish_time=_s(a.get("iso_date") or a.get("date", "")),
                url=_s(a.get("link") or a.get("url", "")),
            ))
        return items

    async def _anysearch_fallback(self, query: str, limit: int,
                                  meta: dict | None = None) -> list[NewsItem]:
        """AnySearch 兜底：用搜索 API 搜新闻关键词。"""
        if not self._anysearch:
            raise ProviderError("anysearch fallback not configured")
        from .base import SearchProvider
        results = await self._anysearch.search(f"{query} 最新新闻", limit=limit, meta=meta)
        return [
            NewsItem(title=r.title, summary=r.snippet, source=r.source,
                     publish_time="", url=r.url)
            for r in results
        ]

    async def headlines(self, topic: str = "", limit: int = 5,
                        meta: dict | None = None) -> list[NewsItem]:
        """获取新闻头条。**国内具体话题**走 Baidu；**综合要闻(空 topic)/国际话题**走 Google News
        （返回文章级头条而非门户版块页，且更广覆盖）；均失败走 AnySearch。"""
        query = topic or "今日要闻 头条"
        errors: list[str] = []

        # 国内具体话题优先 Baidu News（综合要闻不走 Baidu——其"今日热点"多旧闻/体育速递垃圾）
        if topic and _is_chinese_topic(topic):
            try:
                return await self._serpapi_search("baidu_news", query, limit, meta)
            except (ProviderError, Exception) as e:
                errors.append(f"baidu: {e}")

        # 综合要闻 / 国际话题走 Google News
        try:
            return await self._serpapi_search("google_news", query, limit, meta)
        except (ProviderError, Exception) as e:
            errors.append(f"google: {e}")

        # 两者均失败，走 AnySearch 兜底
        try:
            return await self._anysearch_fallback(query, limit, meta)
        except (ProviderError, Exception) as e:
            errors.append(f"anysearch: {e}")

        raise ProviderError(f"all news providers failed: {'; '.join(errors)}")


def _is_chinese_topic(topic: str) -> bool:
    """粗判是否中文话题（含中文字符→国内优先 Baidu）。"""
    return any('一' <= c <= '鿿' for c in topic)

"""检索编排（共享）——联网检索 + 正文补抓，归一化为来源 dict 列表。

从 info Agent 抽出（原 `_search` 的取数段 + `_enrich_empty_content`），供 info（单轮）与
deep-research（每个子问题一次检索）共用。**注入式**：search_provider/extractor 由调用方传入。

约定：`retrieve` 让 `ProviderError` 向上抛出（调用方决定 FAILED/降级）；正文补抓单条失败静默跳过。
"""
from __future__ import annotations
import logging

from .http import ProviderError
from .grounding import clean_snippet

logger = logging.getLogger("agent.sdk.retrieval")


async def enrich_empty_content(extractor, sources: list[dict], meta=None) -> None:
    """正文为空时用 extractor（如 AnySearch extract）补抓正文（best-effort，前 3 条）。

    单条失败静默跳过——绝不阻断主链/不引入编造。extractor 为 None 直接返回。
    """
    if not extractor:
        return
    for s in sources[:3]:
        if s.get("content") or not s.get("url"):
            continue
        try:
            text = await extractor.extract(s["url"], meta=meta)
            if text:
                s["content"] = text[:1500]
        except (ProviderError, Exception) as e:  # noqa: B014 - 故意吞掉，best-effort
            logger.debug("extract enrich skipped: %s", e)


async def retrieve(search_provider, query: str, *, limit: int = 5,
                   recency_days: int = 0, category: str = "", livecrawl: str = "",
                   extractor=None, meta=None) -> list[dict]:
    """联网检索 → 归一化来源 dict 列表（idx 从 1 起）。正文为空 best-effort 补抓。

    `search_provider.search` 抛 `ProviderError` 时不吞，向上传给调用方。
    """
    results = await search_provider.search(
        query, limit=limit, meta=meta,
        recency_days=recency_days, category=category, livecrawl=livecrawl)
    sources = [{"idx": i + 1, "title": r.title, "url": r.url, "source": r.source,
                "published": r.published, "content": r.content,
                "snippet": clean_snippet(r.snippet)}
               for i, r in enumerate(results)]
    await enrich_empty_content(extractor, sources, meta)
    return sources

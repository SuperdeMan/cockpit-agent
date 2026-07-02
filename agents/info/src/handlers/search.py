"""联网搜索域：Exa 正文级检索 + 接地合成（强制引用/无依据弃权）；命中赛事转 sports。

检索/接地内核走 _sdk 共享件（与 deep-research 同源）。赛事路由 _maybe_sports 在 SportsMixin，
本 mixin 经 self 调用。
"""
from __future__ import annotations
import logging

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.grounding import fallback_brief, grounded_synthesis, latest_published
from agents._sdk.retrieval import retrieve

logger = logging.getLogger("agent.info")

_RECENCY_NOW = ("今天", "今日", "今晚", "现在", "此刻", "实时", "刚刚", "最新", "目前", "当前")
_RECENCY_WEEK = ("本周", "这周", "近期", "最近", "这几天", "这两天", "近几天")
_NEWS_WORDS = ("新闻", "资讯", "头条", "热点")
# 时效敏感（榜单/排名/统计/最新…）：对支持的源启用实时抓取(livecrawl)，避免缓存快照给旧数据
_FRESH_MARKERS = ("榜", "排行", "排名", "纪录", "记录", "统计", "最新", "目前",
                  "现在", "截至", "实时", "今年", "本赛季", "射手", "积分")


def _is_fresh_sensitive(query: str) -> bool:
    return any(m in (query or "") for m in _FRESH_MARKERS)


def _plan_search(query: str) -> tuple[int, str]:
    """规划检索参数：返回 (recency_days, category)。

    取代旧的关键词拼接（``_fresh_search_query``）——Exa 的 neural 检索对自然语言友好，
    不需要把日期/「当日赛程」硬塞进查询串；真正需要的是**时效窗口**让实时类查询
    不混入历史资料，以及新闻类的 category 提示。recency_days=0 表示不限时效。
    """
    if any(w in query for w in _RECENCY_NOW):
        recency_days = 2          # 留一点时区/发布滞后缓冲
    elif any(w in query for w in _RECENCY_WEEK):
        recency_days = 7
    else:
        recency_days = 0
    category = "news" if any(w in query for w in _NEWS_WORDS) else ""
    return recency_days, category


class SearchMixin:
    async def _search(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return AgentResult(status=NEED_SLOT, speech="您想搜什么？",
                               follow_up="请告诉我搜索内容", missing_slots=["query"])
        # 赛事路由：命中已知赛事 + 赛事意图词 → 走结构化数据源，不进通用搜索（杜绝编造比分）
        sports = await self._maybe_sports(query, meta, intent.raw_text)
        if sports is not None:
            return sports
        _broad = any(w in query for w in ("全部", "所有", "每场", "比分", "赛果", "结果"))
        limit = int(intent.slots.get("limit", 6 if _broad else 5) or (6 if _broad else 5))
        recency_days, category = _plan_search(query)
        # 时效敏感（榜单/排名/统计…）→ 让 Exa 抓实时页面，避免缓存快照给旧数据
        livecrawl = "preferred" if _is_fresh_sensitive(query) else ""
        try:
            # 检索 + 正文补抓走 _sdk 共享内核（与 deep-research 同源，改一处全覆盖）
            sources = await retrieve(
                self.search, query, limit=limit, recency_days=recency_days,
                category=category, livecrawl=livecrawl, extractor=self.extractor, meta=meta)
        except ProviderError as e:
            logger.warning("search failed: %s", e)
            return AgentResult(
                status=FAILED,
                speech="联网检索暂时不可用，无法确认最新结果，请稍后再试。",
            )

        if not sources:
            return AgentResult(speech=f"没有找到关于「{query}」的搜索结果。")

        # 接地合成走 _sdk 共享内核（强制引用 + 无依据弃权）；失败诚实兜底
        synth = await grounded_synthesis(self.llm, query, sources)
        if synth:
            speech, confidence = synth["answer"], synth["confidence"]
        else:
            speech, confidence = fallback_brief(query, sources), "low"

        # search_result：气泡给结论，卡片只给证据（来源/时效/置信度）——不放结论文本，
        # 也不放 key_points（要点与气泡结论重复，用户反馈像"又一个总结"）。
        card = {
            "type": "search_result",
            "query": query,
            "sources": [{"title": s["title"], "url": s["url"], "source": s["source"],
                         "published": s["published"]} for s in sources],
            "freshness": latest_published(sources),
            "confidence": confidence,
        }
        return AgentResult(speech=speech, ui_card=card,
                           data={"sources": card["sources"]})

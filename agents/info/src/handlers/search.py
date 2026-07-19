"""联网搜索域：Exa 正文级检索 + 接地合成（强制引用/无依据弃权）；命中赛事转 sports。

检索/接地内核走 _sdk 共享件（与 deep-research 同源）。赛事路由 _maybe_sports 在 SportsMixin，
本 mixin 经 self 调用。
"""
from __future__ import annotations
import logging
import re

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.grounding import fallback_brief, grounded_synthesis, latest_published
from agents._sdk.provenance import attach
from agents._sdk.retrieval import retrieve

from ._util import _shanghai_now
from .sports import _is_predictive

logger = logging.getLogger("agent.info")

# 相对年份词 → 相对当前年的偏移（badcase f11aa344：planner 把「今年世界杯」按训练先验
# 改写成「2024年世界杯」灌进 query，检索整轮被污染答出 2022/2024 旧料）。
_REL_YEAR = (("今年", 0), ("本年", 0), ("去年", -1), ("明年", 1), ("前年", -2), ("后年", 2))
_YEAR_IN_QUERY_RE = re.compile(r"(20\d{2})(?=年)")   # 只动「20XX年」词形，不碰裸数字/型号


def fix_relative_year(query: str, raw: str) -> str:
    """query 年份对齐原话的相对年份词（确定性护栏，planner 日期锚之外的第二道防线）。

    原话含「今年/去年/明年…」而 query 里出现按当前日期换算**不一致**、且原话中也没有
    出现过的「20XX年」→ 改写成换算年份。原话自带的年份（用户明说 2022）原样保留。"""
    q = (query or "")
    r = (raw or "")
    if not q or not r:
        return q
    offset = next((off for w, off in _REL_YEAR if w in r), None)
    if offset is None:
        return q
    target = str(_shanghai_now().year + offset)

    def _sub(m):
        y = m.group(1)
        return y if (y == target or y in r) else target

    fixed = _YEAR_IN_QUERY_RE.sub(_sub, q)
    if fixed != q:
        logger.info("relative-year fix: %r -> %r (raw=%r)", q, fixed, r[:40])
    return fixed

_RECENCY_NOW = ("今天", "今日", "今晚", "现在", "此刻", "实时", "刚刚", "最新", "目前", "当前")
_RECENCY_WEEK = ("本周", "这周", "近期", "最近", "这几天", "这两天", "近几天")
_NEWS_WORDS = ("新闻", "资讯", "头条", "热点")
# 时效敏感（榜单/排名/统计/最新…）：对支持的源启用实时抓取(livecrawl)，避免缓存快照给旧数据
_FRESH_MARKERS = ("榜", "排行", "排名", "纪录", "记录", "统计", "最新", "目前",
                  "现在", "截至", "实时", "今年", "本赛季", "射手", "积分")


def _is_fresh_sensitive(query: str) -> bool:
    return any(m in (query or "") for m in _FRESH_MARKERS)


# 口语引导前缀（薄证据重试的改写用）：长词形在前，剥不动才退「详细介绍」扩展。
# 裸「查」刻意不在列（查理/查尔斯是实体名前缀）。
_COLLOQUIAL_PREFIX = ("麻烦", "帮我", "帮忙", "给我", "请", "上网", "联网",
                      "百度一下", "搜索", "搜一下", "搜下", "搜搜", "搜",
                      "查一查", "查一下", "查查", "查下", "查询", "检索")


def _strip_colloquial(query: str) -> str:
    """迭代剥句首口语引导词，返回剥后内容；没剥到任何东西（或剥空）返回 ""。"""
    orig = (query or "").strip()
    q = orig
    changed = True
    while changed:
        changed = False
        for p in _COLLOQUIAL_PREFIX:
            if q.startswith(p) and len(q) > len(p):
                q = q[len(p):].lstrip("，, 、").lstrip("的")
                changed = True
                break
    return q if q and q != orig else ""


def _has_body(s: dict) -> bool:
    """来源是否有可用正文（content 非空，或 snippet 至少像一段话）。"""
    return bool((s.get("content") or "").strip()) or \
        len((s.get("snippet") or "").strip()) >= 60


def _merge_sources(primary: list[dict], extra: list[dict]) -> list[dict]:
    """按 url（无 url 用 title|source）去重合并，保序，idx 重编为 1..n。"""
    out: list[dict] = []
    seen: set[str] = set()
    for s in list(primary) + list(extra):
        key = s.get("url") or f"{s.get('title', '')}|{s.get('source', '')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    for i, s in enumerate(out, 1):
        s["idx"] = i
    return out


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
    async def _search(self, intent, ctx, meta, skip_sports: bool = False) -> AgentResult:
        """skip_sports：sports provider 故障回落本方法时置 True——防再进结构化源二次吃超时
        （R9；`_maybe_sports` 自身故障返回 None 会自然落到通用检索，无回环）。"""
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return AgentResult(status=NEED_SLOT, speech="您想搜什么？",
                               follow_up="请告诉我搜索内容", missing_slots=["query"])
        # 相对年份纠偏：planner 幻觉年份（「今年」→2024）污染 query 时按当前日期换算改正
        query = fix_relative_year(query, intent.raw_text)
        # 预测/前瞻的赛事句：planner 直路由 info.search 时也要做指代锚定/完赛判定
        # （badcase bfb5d9c7：planner 把「这场」缝成「决赛 法国vs英格兰」幻觉对阵灌进
        # query，且季军赛已完赛仍出预测）。SportsMixin 同 MRO；解析不出 → None 原样检索。
        if not skip_sports and _is_predictive(f"{query} {intent.raw_text or ''}"):
            red = await self._predictive_redirect(intent, ctx, meta)
            if red is not None:
                return red
        # 赛事路由：命中已知赛事 + 赛事意图词 → 走结构化数据源，不进通用搜索（杜绝编造比分）
        sports = None if skip_sports else await self._maybe_sports(query, meta, intent.raw_text)
        if sports is not None:
            await self._save_remindable(ctx, sports)   # 跨域提醒 P1c（SportsMixin，同 MRO）
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
            # 诚实降级用 OK——FAILED 话术会被聚合器吞掉换成裸「处理失败」（R9/scene 同坑）
            return AgentResult(
                speech="联网检索暂时不可用，无法确认最新结果，请稍后再试。",
            )

        # 薄证据一轮重试（仿 deep-research backtrack）：有正文的源不足 2 条时，剥口语
        # 引导词（剥不动才退「详细介绍」扩展）best-effort 再检一轮，按 url 去重合并。
        # 只在证据薄时多一跳，不加常态延迟；重试失败不影响首轮结果。
        if sum(1 for s in sources if _has_body(s)) < 2:
            retry_q = _strip_colloquial(query) or f"{query} 详细介绍"
            try:
                extra = await retrieve(
                    self.search, retry_q, limit=limit, recency_days=recency_days,
                    category=category, livecrawl=livecrawl,
                    extractor=self.extractor, meta=meta)
            except ProviderError as e:
                logger.debug("thin-evidence retry skipped: %s", e)
                extra = []
            if extra:
                sources = _merge_sources(sources, extra)

        if not sources:
            return AgentResult(speech=f"没有找到关于「{query}」的搜索结果。")

        # 接地合成走 _sdk 共享内核（强制引用 + 无依据弃权）；失败诚实兜底。
        # recency_days 透传：时效敏感查询在合成前用「窗口内优先 + 权威」双序重排。
        synth = await grounded_synthesis(self.llm, query, sources,
                                         recency_days=recency_days)
        if synth:
            speech, confidence = synth["answer"], synth["confidence"]
        else:
            speech, confidence = fallback_brief(query, sources), "low"

        # search_result：气泡给结论，卡片只给证据（来源/时效/置信度）——不放结论文本，
        # 也不放 key_points（要点与气泡结论重复，用户反馈像"又一个总结"）。
        card = attach({
            "type": "search_result",
            "query": query,
            "sources": [{"title": s["title"], "url": s["url"], "source": s["source"],
                         "published": s["published"]} for s in sources],
            "freshness": latest_published(sources),
            "confidence": confidence,
        }, self.search)   # 真实性标记（_prov，治理 P1 试点族）
        # 低置信=单轮快查天花板的信号：给口头升级出口（不自动升调研，尊重延迟预期）
        follow_up = ("这轮是快查；想要更全面的结论，可以说「深入调研一下」。"
                     if confidence == "low" else "")
        return AgentResult(speech=speech, ui_card=card,
                           data={"sources": card["sources"]}, follow_up=follow_up)

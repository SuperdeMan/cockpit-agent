"""深度调研 Agent（P0）—— 独立 deep-research 一等 Agent。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」复刻进本 Agent：
`handle` 驱动 pipeline 四段（plan 提议多视角子问题 → investigate 有界并行迭代检索 →
synthesize 分节接地报告 → brief 一段式语音简报 + research_report 卡）。

护城河：接地「我」（位置/电量/画像作研究约束）+ 渐进语音 + 可落地产物。P1：接地位置/画像、
多轮研究上下文（落 memory，「再深入第N点」聚焦深挖不重跑整份调研）、报告可存记忆。
搜索 provider 进程内复用 info（info 拥有搜索 provider，跟随 trip_planner→navigation 先例）。
"""
from __future__ import annotations
import json
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.grounding import shanghai_now
from agents._sdk.location import current_location_from_meta
from agents.info.src.providers import build_search_provider, build_extractor
from agents.info.src.providers.amap_geocoder import build_location_resolver
from .pipeline import plan, investigate, synthesize, brief
from .models import ResearchTask

logger = logging.getLogger("agent.deep_research")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
# 当前活动调研落 memory（多轮深挖复用）；Agent 无状态化，与 trip-planner trip_active 同范式。
_PROFILE_KEY = "research_active"
# 追问深挖标记 + 「第N点/节/部」序号解析（把"再深入第2点"解析到上次报告的第2节）。
# 字符集刻意**不含「条」**——「第N条」专属新闻深挖（_NEWS_ORD_RE），否则会把「详细讲讲第2条新闻」
# 劫持去解析上次研究报告的第2节（实测踩到）。
_DEEPEN_MARK = ("深入", "展开", "详细", "细说", "再讲", "讲讲", "多说", "继续讲", "深挖", "细讲")
_DEEPEN_THIS = ("这部分", "这点", "这块", "这一节", "那部分", "那点", "上面", "刚才", "最后那")
_ORDINAL_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十]+)\s*[点节部]")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 新闻深挖桥接（P2）：『详细讲讲第N条/这条新闻』→ 取上次新闻列表（info 落的 news_active）第N条做小型调研。
_NEWS_ORD_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十]+)\s*条")
_NEWS_THIS = ("这条新闻", "这条", "那条", "这则", "那则", "上面那条", "刚才那条", "这个新闻")
# 把研究结论摘要存为长期记忆（"帮我记下/存一下/收藏"）。
_REMEMBER_MARK = ("记下", "记一下", "存下", "存一下", "收藏", "保存", "记住这", "存到记忆")
# 地理相关研究：仅这类问题才把当前城市作约束（否则反查城市会把无关主题带偏）。
_GEO_MARK = ("本地", "当地", "附近", "周边", "这边", "这里", "哪个城市", "哪里", "哪座城",
             "定居", "落户", "搬到", "搬去", "买房", "租房", "移居", "宜居", "适合居住")


class DeepResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 info 的搜索 + 正文补抓 provider（Exa→AnySearch→Bing→mock 降级链已在工厂内）。
        self.search = build_search_provider()
        self.extractor = build_extractor()
        # 坐标→城市反查（接地「我」：让"本地/这附近"类调研贴合当前城市）。
        self.location_resolver = build_location_resolver()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "research.run":
            return await self._research(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="深度调研助手暂不支持该请求。")

    async def _research(self, intent, ctx, meta) -> AgentResult:
        raw = (intent.raw_text or "").strip()
        question = (intent.slots.get("query") or intent.slots.get("topic")
                    or intent.slots.get("question") or "").strip() or raw
        prior = await self._load_prior(ctx)

        # 存记忆：『帮我记下/存一下这个调研』→ 把上次报告结论写入长期记忆，不重跑调研。
        if prior and any(m in raw for m in _REMEMBER_MARK):
            return await self._remember_report(ctx, prior)

        # 追问深挖：『再深入第N点/展开这部分』→ 取上次报告对应小节标题作聚焦问题（不重跑整份调研）。
        focus = self._resolve_deepen(raw, prior)
        if focus:
            question = f"{focus}——在前述调研基础上深入展开"
        else:
            # 新闻深挖桥接（P2）：『详细讲讲第N条/这条新闻』→ 取上次新闻列表第N条标题做小型调研。
            news_focus = await self._resolve_news_deepen(ctx, raw)
            if news_focus:
                question = f"{news_focus}（事件来龙去脉、背景与影响）"
        if not question:
            return AgentResult(
                status=NEED_SLOT, speech="您想深入调研什么？",
                follow_up="告诉我调研主题，例如『深入调研一下固态电池』",
                missing_slots=["query"])

        constraints = await self._constraints(question, ctx, meta)
        task = ResearchTask(session_id=ctx.session_id or "", user_id=ctx.user_id or "",
                            question=question, constraints=constraints)

        # 四段流水线：事实全部确定性产出，LLM 只在 plan 提议子问题、synthesize 受约束合成。
        task.plan = await plan(self.llm, question, constraints)
        task.status = "investigating"
        await investigate(self.search, self.extractor, task.plan, meta=meta)
        task.status = "synthesizing"
        report = await synthesize(self.llm, question, task.plan, constraints)
        task.status = "done"

        speech, card = brief(report, question)
        await self._save_task(ctx, question, report)   # 落 memory，供下一轮深挖
        # 落地产物提示（座舱差异化=可继续的产物）：可深挖某节 / 可存记忆。
        follow = "想深入某部分说『展开第N点』；想存下结论说『记一下』。" if report.sections else ""
        return AgentResult(
            speech=speech, ui_card=card, follow_up=follow,
            data={"question": question, "sections": len(report.sections),
                  "sources": report.sources, "confidence": report.overall_confidence,
                  "gaps": report.gaps})

    async def _constraints(self, question: str, ctx, meta) -> dict:
        """收集与研究**相关**的处境（接地「我」）。

        **刻意不注入电量**（与研究主题几乎无关、会把无关问题带偏——实测「loop engineering」被带成
        「电量72%自适应控制」）；位置仅在问题涉及本地/选城等地理相关时反查注入；画像走语义召回且
        **高分才注入**。任一步失败静默跳过，不阻断调研。
        """
        c = {"time_now": f"{shanghai_now():%Y年%m月%d日}"}
        # 位置：仅当问题地理相关（本地/选城/宜居…）才反查当前城市，否则不注入（防带偏）
        if any(m in question for m in _GEO_MARK):
            cur = current_location_from_meta(meta)
            if cur:
                try:
                    city = await self.location_resolver.reverse(cur.lng, cur.lat, meta)
                    if city:
                        c["location"] = city
                except Exception as e:
                    logger.debug("research reverse geocode skipped: %s", e)
        # 画像偏好：按研究问题语义召回，min_score 收紧到 0.35（只让确有相关性的偏好注入）
        try:
            hits = await ctx.recall(query=question, top_k=3, min_score=0.35)
            prefs = [str(h.get("text", "")).strip() for h in hits if h.get("text")]
            if prefs:
                c["profile_prefs"] = prefs[:3]
        except Exception as e:
            logger.debug("research recall skipped: %s", e)
        return c

    # ── 多轮研究上下文（落 memory，Agent 无状态化）──────────────────
    async def _load_prior(self, ctx) -> dict | None:
        """读上次活动调研（紧凑：question + summary + 各节标题/摘要）。失败/无 → None。"""
        try:
            vals = await ctx.fetch(f"profile.{_PROFILE_KEY}")
        except Exception as e:
            logger.debug("load prior research skipped: %s", e)
            return None
        raw = vals.get(f"profile.{_PROFILE_KEY}")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return raw if isinstance(raw, dict) else None

    async def _save_task(self, ctx, question: str, report) -> None:
        """紧凑落 memory：保存问题 + 各节标题/正文摘要，供下一轮『展开第N点』定位（best-effort）。"""
        try:
            await ctx.save_profile(_PROFILE_KEY, {
                "question": question,
                "summary": (report.summary or "")[:300],
                "sections": [{"heading": s.heading, "body": (s.body or "")[:400]}
                             for s in report.sections],
                "freshness": report.freshness,
            })
        except Exception as e:
            logger.debug("save research task skipped: %s", e)

    @staticmethod
    def _resolve_deepen(text: str, prior: dict | None) -> str:
        """追问深挖 → 取上次报告对应小节标题作聚焦问题。无 prior / 无深挖词 / 解析不到 → 空串。

        『再深入第2点/展开第二节』→ 第2节；『这部分/上面再展开』→ 最近一节。
        要求同时含深挖词（深入/展开/详细…）才触发，避免普通新调研被当成深挖。
        """
        if not prior or not prior.get("sections"):
            return ""
        if not any(m in text for m in _DEEPEN_MARK):
            return ""
        secs = prior["sections"]
        idx = None
        m = _ORDINAL_RE.search(text)
        if m:
            tok = m.group(1)
            n = int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)
            if n >= 1:
                idx = n - 1
        elif any(w in text for w in _DEEPEN_THIS):
            idx = len(secs) - 1
        if idx is None or not (0 <= idx < len(secs)):
            return ""
        return (secs[idx].get("heading") or "").strip()

    async def _resolve_news_deepen(self, ctx, text: str) -> str:
        """『详细讲讲第N条/这条新闻』→ 取 info 落的 news_active 第N条标题作聚焦调研问题。无法解析返回空。"""
        t = text or ""
        if not (any(m in t for m in _DEEPEN_MARK) or "新闻" in t
                or any(w in t for w in _NEWS_THIS)):
            return ""
        try:
            vals = await ctx.fetch("profile.news_active")
        except Exception as e:
            logger.debug("load news_active skipped: %s", e)
            return ""
        rawv = vals.get("profile.news_active")
        if isinstance(rawv, str):
            try:
                rawv = json.loads(rawv)
            except (json.JSONDecodeError, TypeError):
                return ""
        items = (rawv or {}).get("items") if isinstance(rawv, dict) else None
        if not items:
            return ""
        idx = None
        m = _NEWS_ORD_RE.search(t)
        if m:
            tok = m.group(1)
            n = int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)
            if n >= 1:
                idx = n - 1
        elif any(w in t for w in _NEWS_THIS):
            idx = 0
        if idx is None or not (0 <= idx < len(items)):
            return ""
        return (items[idx].get("title") or "").strip()

    async def _remember_report(self, ctx, prior: dict) -> AgentResult:
        """把上次调研结论存为长期记忆（情景），供以后直接召回。"""
        q = (prior.get("question") or "调研").strip()
        summary = (prior.get("summary") or "").strip()
        text = f"调研过「{q}」：{summary}".rstrip("：")
        ok = False
        try:
            ok = await ctx.remember(text, kind="episodic", scope="research")
        except Exception as e:
            logger.debug("remember research skipped: %s", e)
        speech = (f"好的，已把关于「{q}」的调研结论记下了，以后可以直接问我。"
                  if ok else "抱歉，这条结论暂时没能存进记忆，稍后再试。")
        return AgentResult(speech=speech)

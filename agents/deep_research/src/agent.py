"""深度调研 Agent（P0）—— 独立 deep-research 一等 Agent。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」复刻进本 Agent：
`handle` 驱动 pipeline 四段（plan 提议多视角子问题 → investigate 有界并行迭代检索 →
synthesize 分节接地报告 → brief 一段式语音简报 + research_report 卡）。

护城河：接地「我」（位置/电量/画像作研究约束）+ 渐进语音 + 可落地产物。P1：接地位置/画像、
多轮研究上下文（落 memory，「再深入第N点」聚焦深挖不重跑整份调研）、报告可存记忆。
搜索 provider 进程内复用 info（info 拥有搜索 provider，跟随 trip_planner→navigation 先例）。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.base import Context
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
# 异步「分钟级」深调研触发：用户明示「不急/慢慢查/查完告诉我/要详细完整报告」类**显式延后**信号
# → 立即受理、后台跑更深流水线（不受 90s 网关上限），查完经 NATS 主动播报+推报告卡。
# 刻意只认显式延后/报告类措辞，不认「彻底/认真」（那些仍同步即时答，避免改变既有即时预期）。
_ASYNC_MARK = (
    "不急", "不着急", "别急", "不用急",
    "慢慢查", "慢慢研究", "慢慢来", "慢点查", "慢慢做", "慢慢搞",
    "查完告诉我", "查完通知我", "查完叫我", "查好告诉我", "查好了告诉我", "查完跟我说",
    "好了叫我", "好了告诉我", "完了告诉我", "弄完告诉我", "好了通知我", "等会告诉我",
    "待会告诉我", "花点时间", "多花点时间", "花时间", "慢工出细活",
    "详细报告", "完整报告", "深度报告", "详尽报告", "完整的报告", "详细的报告",
    "出一份详细", "出一份完整", "出一份深度", "出份详细", "出份完整",
)
# 异步请求里的**尾部延后语**（不急/慢慢查/查完告诉我/先忙别的…）是非研究内容的噪声，喂给 plan/
# synthesize 会污染子问题、也会脏报告卡的 question 字段 → 从首个延后语起截到末尾剔除。只剔尾部延后语，
# 不动「详细报告」这类与请求一体的措辞（LLM 自会从中提取主题）。
_ASYNC_NOISE_RE = re.compile(
    r"[，,。.！!、\s]*"
    r"(?:不急|不着急|别急|不用急|慢慢[^，。]*|慢点[^，。]*|花点时间|多花点时间|花时间[^，。]*|"
    r"慢工出细活|先忙[^，。]*|"
    r"(?:查完|查好|弄完|好了|完了|等会|待会)[^，。]{0,6}(?:告诉|通知|叫|跟我说)[^，。]*)"
    r".*$")


class DeepResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 info 的搜索 + 正文补抓 provider（Exa→AnySearch→Bing→mock 降级链已在工厂内）。
        self.search = build_search_provider()
        self.extractor = build_extractor()
        # 坐标→城市反查（接地「我」：让"本地/这附近"类调研贴合当前城市）。
        self.location_resolver = build_location_resolver()
        # 异步分钟级深调研：NATS 连接（on_start 建）+ 后台 task 引用集（持引用防 GC，完成自动 discard）。
        self._nc = None
        self._bg_tasks: set = set()

    async def on_start(self) -> None:
        """连 NATS 供异步深调研完成后主动推送（agent.proactive）。无 NATS_URL/连接失败 → 静默禁用：
        异步调研仍会跑、仍落 memory（用户可再问取回），仅不主动推送。本 Agent 只发布、不订阅。"""
        nats_url = os.getenv("NATS_URL", "")
        if not nats_url:
            logger.info("deep-research: NATS_URL 未设置，异步调研主动推送禁用")
            return
        try:
            import nats
            self._nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
            logger.info("deep-research: NATS 已连接，异步深调研可主动推送报告")
        except Exception as e:
            logger.warning("deep-research: NATS 连接失败，异步推送禁用：%s", e)

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

        # 异步分钟级深调研：用户明示「不急/慢慢查/查完告诉我/要详细完整报告」→ 立即受理，后台跑
        # 更深流水线（deep=True，不受 90s 网关上限），查完经 NATS agent.proactive 主动播报+推报告卡。
        if self._is_async_request(raw):
            return self._kickoff_async(question, constraints, ctx, meta)

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
        # 落地产物提示（座舱差异化=可继续的产物）：可深挖某节 / 转异步更深版 / 可存记忆。
        # 异步引导刻意放进同步 follow_up——异步是「显式延后语」触发，否则用户猜不到，可发现性差。
        follow = ("想深入某部分说『展开第N点』；想要更深更完整的报告，说『慢慢查、查完告诉我』，"
                  "我后台慢慢查完主动推给你；想存下结论说『记一下』。") if report.sections else ""
        return AgentResult(
            speech=speech, ui_card=card, follow_up=follow,
            data={"question": question, "sections": len(report.sections),
                  "sources": report.sources, "confidence": report.overall_confidence,
                  "gaps": report.gaps})

    # ── 异步分钟级深调研（解同步 90s 上限封顶的报告深度）──────────────────
    @staticmethod
    def _is_async_request(raw: str) -> bool:
        """命中显式延后/报告类信号 → 走异步深调研。仅认明示措辞，避免改变普通调研的即时预期。"""
        return any(m in (raw or "") for m in _ASYNC_MARK)

    @staticmethod
    def _strip_async_noise(question: str) -> str:
        """剔除尾部延后语（不急/慢慢查/查完告诉我/先忙别的…），返回干净研究问题。

        LLM planner 通常已把 slots.query 抽成干净主题；但确定性兜底 `_ensure_research_step` 会把
        **整句原话**塞进 query（含延后语噪声）→ 这里兜底清一遍，防其污染子问题与报告卡 question 字段。
        清理后若过短（疑误删）则回退原句。
        """
        cleaned = _ASYNC_NOISE_RE.sub("", question or "").strip(" ，,。.！!、")
        return cleaned if len(cleaned) >= 4 else (question or "").strip()

    @staticmethod
    def _topic(question: str) -> str:
        """从问题取简短主题（去掉深挖后缀『——…』），用于话术/推送标题。"""
        return question.split("——")[0].split("（")[0].strip()[:24] or "这个主题"

    def _kickoff_async(self, question: str, constraints: dict, ctx, meta) -> AgentResult:
        """受理异步深调研：spawn 后台 task（持引用防 GC），立即返回受理话术。

        ctx 是请求级句柄，后台任务用 self.memory（Agent 级持久）重建 Context 落记忆，
        故此处只捕获身份标识（session/user/vehicle）与 meta 的纯 dict 拷贝交给后台。
        """
        question = self._strip_async_noise(question)   # 清尾部延后语，防噪声污染子问题/报告卡
        sid, uid, vid = ctx.session_id, ctx.user_id, ctx.vehicle_id
        task = asyncio.create_task(
            self._run_deep_async(question, constraints, sid, uid, vid, dict(meta or {})))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        speech = (f"好的，「{self._topic(question)}」这个我深入查一份完整报告，大概几分钟，"
                  "查完直接语音通知你、把报告推过来，你可以先忙别的。")
        return AgentResult(
            speech=speech,
            follow_up="我查完会主动播报结论并推送可读报告，无需等待。",
            data={"async": True, "question": question})

    async def _run_deep_async(self, question: str, constraints: dict,
                              sid: str, uid: str, vid: str, meta: dict) -> None:
        """后台跑深度流水线（deep=True，不受 90s 上限）→ 落 memory → 经 NATS 主动推送报告。

        全程 best-effort：已脱离请求路径，任何阶段异常都只记日志、发一条简短失败告知，不崩进程。
        """
        try:
            subqs = await plan(self.llm, question, constraints, deep=True)
            await investigate(self.search, self.extractor, subqs, meta=meta, deep=True)
            report = await synthesize(self.llm, question, subqs, constraints, deep=True)
            speech, card = brief(report, question)
            # 落 memory（供后续「展开第N点」深挖）：后台用持久 self.memory 重建 Context。
            try:
                await self._save_task(Context(sid, uid, vid, self.memory), question, report)
            except Exception as e:
                logger.debug("async save research task skipped: %s", e)
            await self._publish_research_done(question, speech, card)
            logger.info("async deep research done: %s（%d 节/%d 源）",
                        self._topic(question), len(report.sections), len(report.sources))
        except Exception as e:
            logger.warning("async deep research failed for '%s': %s", question[:40], e)
            await self._publish_research_failed(question)

    async def _publish_research_done(self, question: str, speech: str, card: dict) -> None:
        """异步调研完成 → 发 NATS agent.proactive（带 card=报告卡）。无 NATS → 仅日志（同 road-safety）。

        edge 网关订阅 agent.proactive 并透传 speech+card 给 HMI（card 为可读分节报告卡）。
        """
        topic = self._topic(question)
        if not self._nc:
            logger.info("async research done (NATS 禁用，未推送): %s", topic)
            return
        payload = {"type": "research_done",
                   "speech": f"关于「{topic}」的深度调研完成了。{speech}",
                   "card": card, "agent_id": self.manifest.agent_id,
                   "ts": int(time.time() * 1000)}
        try:
            await self._nc.publish(
                "agent.proactive", json.dumps(payload, ensure_ascii=False).encode())
            logger.info("async research proactive sent: %s", topic)
        except Exception as e:
            logger.warning("async research publish failed: %s", e)

    async def _publish_research_failed(self, question: str) -> None:
        """异步调研失败 → 发一条简短主动告知（best-effort，无 NATS 静默）。"""
        if not self._nc:
            return
        payload = {"type": "research_failed",
                   "speech": f"抱歉，「{self._topic(question)}」的深度调研没能完成，稍后可以让我再试一次。",
                   "agent_id": self.manifest.agent_id, "ts": int(time.time() * 1000)}
        try:
            await self._nc.publish(
                "agent.proactive", json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:
            logger.debug("async research fail-publish skipped: %s", e)

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

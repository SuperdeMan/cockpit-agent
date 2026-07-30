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
from agents._sdk.ledger import (
    CANCELLED, DONE, ORPHANED, STOP_BUDGET, STOP_DEADLINE, STOP_USER, Duplicate,
)
from agents._sdk.ledger import FAILED as FAILED_STATUS   # 与 AgentResult 的 FAILED 同名，显式区分
from agents._sdk.shared_state import NEWS_ACTIVE, RESEARCH_ACTIVE
from agents._sdk.grounding import shanghai_now
from agents._sdk.location import current_location_from_meta
from runtime.proactive import P_USER_CONTRACT, publish_proactive
from agents.info.src.providers import build_search_provider, build_extractor
from agents.info.src.providers.amap_geocoder import build_location_resolver
from .pipeline import plan, investigate, synthesize, brief, _clean_excerpt, _EXCERPT_CAP
from .models import Evidence, ResearchTask, SubQuestion

logger = logging.getLogger("agent.deep_research")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
# 跨 Agent 会话状态键（当前活动调研 / info 新闻列表）经 agents._sdk.shared_state 常量引用；
# 权威登记见 docs/conventions.md「跨 Agent 状态键」。Agent 无状态化，落 profile KV 多轮复用。
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
# Task Ledger 里本 Agent 的任务类型（同一账本按 kind 分域，供跨 Agent 消歧）。
LEDGER_KIND = "research"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


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
        异步调研仍会跑、仍落 memory（用户可再问取回），仅不主动推送。本 Agent 只发布、不订阅。

        同时初始化 Task Ledger（M2 P0）：在此建池建表，让首次受理不背建连成本；
        失败静默降级（异步调研照跑，只是不承诺可查询/可取消）。
        """
        await self.ledger.init()
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
        # M2 P0：长任务的「还让不让它干 / 干到哪了」——两条都从 Task Ledger 读事实后
        # **确定性作答**，绝不让 LLM 编（墙钟直答同一原则）。
        if intent.name == "research.cancel":
            return await self._cancel_task(intent, ctx)
        if intent.name == "research.status":
            return await self._task_status(ctx)
        return AgentResult(status=FAILED, speech="深度调研助手暂不支持该请求。")

    async def _research(self, intent, ctx, meta) -> AgentResult:
        raw = (intent.raw_text or "").strip()
        question = (intent.slots.get("query") or intent.slots.get("topic")
                    or intent.slots.get("question") or "").strip() or raw
        prior = await self._load_prior(ctx)

        # 存记忆：『帮我记下/存一下这个调研』→ 把上次报告结论写入长期记忆，不重跑调研。
        if prior and any(m in raw for m in _REMEMBER_MARK):
            return await self._remember_report(ctx, prior)

        # 追问深挖：『再深入第N点/展开这部分』→ 取上次报告对应小节标题作聚焦问题（不重跑整份调研），
        # 并把上轮该节引用的正文抓回来作种子证据（复用已核实来源，不从零检索）。
        focus = self._resolve_deepen(raw, prior)
        seed_evidence: list = []
        if focus:
            question = f"{focus}——在前述调研基础上深入展开"
            seed_evidence = await self._seed_from_prior(prior, focus, meta)
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
            return await self._kickoff_async(
                question,
                constraints,
                ctx,
                meta,
                idempotency_goal=raw,
            )

        task = ResearchTask(session_id=ctx.session_id or "", user_id=ctx.user_id or "",
                            question=question, constraints=constraints)

        # 四段流水线：事实全部确定性产出，LLM 只在 plan 提议子问题、synthesize 受约束合成。
        task.plan = await plan(self.llm, question, constraints)
        if seed_evidence:
            # 深挖种子：上轮该节引用正文作首个"已答"子问题（investigate 对带证据的 sq 跳过检索；
            # 全局来源编号 _assign_global_sources 按 url 去重，种子与新检索天然合并）。
            task.plan.insert(0, SubQuestion(
                sq_id="sq0", text=f"上轮结论回顾：{focus}", perspective="背景",
                evidence=seed_evidence, status="answered"))
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

    async def _kickoff_async(
        self,
        question: str,
        constraints: dict,
        ctx,
        meta,
        *,
        idempotency_goal: str = "",
    ) -> AgentResult:
        """受理异步深调研：先向 Task Ledger 开单（幂等），再 spawn 后台 task（持引用防 GC），
        立即返回受理话术。

        ctx 是请求级句柄，后台任务用 self.memory（Agent 级持久）重建 Context 落记忆，
        故此处只捕获身份标识（session/user/vehicle）与 meta 的纯 dict 拷贝交给后台。

        三档话术（M2 P0）：
        - 幂等命中 → 「已经在查了」，**不重复开跑**（连说两遍/重试风暴不双跑）；
        - 开单成功 → 受理话术加「可停可问」（承诺得起才说）；
        - 账本不可用（无 PG）→ 退回原话术，**不承诺**可取消/可查询（诚实降级）。
        """
        question = self._strip_async_noise(question)   # 清尾部延后语，防噪声污染子问题/报告卡
        sid, uid, vid = ctx.session_id, ctx.user_id, ctx.vehicle_id
        topic = self._topic(question)

        entry = await self.ledger.open(
            uid, sid, self.manifest.agent_id, LEDGER_KIND, question,
            budget=self._task_budget(),
            origin_trace_id=(meta or {}).get("trace_id", ""),
            idempotency_goal=idempotency_goal,
        )
        if isinstance(entry, Duplicate):
            prior = entry.existing
            detail = f"，{prior.progress}" if prior.progress else ""
            return AgentResult(
                speech=f"「{self._topic(prior.goal)}」我已经在查了{detail}，查完主动告诉你。",
                follow_up="想知道进度就问「查得怎么样了」，不想查了就说「别查了」。",
                data={"async": True, "question": question, "duplicate": True,
                      "task_id": prior.task_id})

        task_id = entry.task_id if entry is not None else ""
        task = asyncio.create_task(
            self._run_deep_async(question, constraints, sid, uid, vid,
                                 dict(meta or {}), task_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        speech = (f"好的，「{topic}」这个我深入查一份完整报告，大概几分钟，"
                  "查完直接语音通知你、把报告推过来，你可以先忙别的。")
        follow = "我查完会主动播报结论并推送可读报告，无需等待。"
        if task_id:
            follow = ("想知道进度随时问「查得怎么样了」，不想查了就说「别查了」；"
                      "查完我会主动播报并推送可读报告。")
        return AgentResult(
            speech=speech, follow_up=follow,
            data={"async": True, "question": question, "task_id": task_id})

    @staticmethod
    def _task_budget() -> dict:
        """后台深调研预算（Background 档六守卫之 deadline①/预算③）。

        deadline 从受理时刻起算；LLM 次数上限覆盖 plan+synthesize+重试余量；外部检索次数
        上限按 9 子问题 × 3 轮（首轮/回溯/学术兜底）留足。env 可调，供评测钉档。
        """
        return {
            "deadline_ts": time.time() + _env_int("RESEARCH_TASK_DEADLINE_S", 900),
            "llm_calls_max": _env_int("RESEARCH_TASK_LLM_MAX", 6),
            "ext_calls_max": _env_int("RESEARCH_TASK_EXT_MAX", 40),
        }

    async def _run_deep_async(self, question: str, constraints: dict,
                              sid: str, uid: str, vid: str, meta: dict,
                              task_id: str = "") -> None:
        """后台跑深度流水线（deep=True，不受 90s 上限）→ 落 memory → 经 NATS 主动推送报告。

        全程 best-effort：已脱离请求路径，任何阶段异常都只记日志、发一条简短失败告知，不崩进程。

        M2 P0 心跳（task_id 为空=无账本，全部退化成今天的行为）：阶段边界各一次
        + investigate 每子问题收敛一次。心跳返回 cancelled 即自行收尾——这是拉模式
        cancel/deadline/预算截停的**唯一**生效点，不跨进程强杀 asyncio.Task。
        """
        stopped = {"v": False}

        async def beat(progress: str = "", used: dict | None = None) -> bool:
            """打一次心跳；返回 True=继续。无账本恒 True。"""
            if not task_id:
                return True
            status = await self.ledger.heartbeat(task_id, progress=progress, used=used)
            if status == CANCELLED:
                stopped["v"] = True
                return False
            return True

        try:
            if not await beat("正在拆解调研角度"):
                return await self._finish_stopped(task_id, question, meta)
            subqs = await plan(self.llm, question, constraints, deep=True)
            if not await beat(f"已拆成 {len(subqs)} 个角度，开始检索",
                              used={"llm_calls": 1}):
                return await self._finish_stopped(task_id, question, meta)

            async def on_progress(done: int, total: int) -> None:
                await beat(f"检索中 {done}/{total} 个子问题", used={"ext_calls": 1})

            await investigate(self.search, self.extractor, subqs, meta=meta, deep=True,
                              on_progress=on_progress,
                              should_stop=lambda: stopped["v"])
            if stopped["v"] or not await beat("检索完成，正在撰写报告"):
                return await self._finish_stopped(task_id, question, meta)

            report = await synthesize(self.llm, question, subqs, constraints, deep=True)
            speech, card = brief(report, question)
            # 落 memory（供后续「展开第N点」深挖）：后台用持久 self.memory 重建 Context。
            try:
                await self._save_task(Context(sid, uid, vid, self.memory), question, report)
            except Exception as e:
                logger.debug("async save research task skipped: %s", e)
            # 先销单再推送：推送失败（无 NATS）不该让账本停在 running 变成假孤儿。
            await self._close_task(task_id, DONE, progress="已完成", result_ref={
                "sections": len(report.sections), "sources": len(report.sources),
                "summary": (report.summary or "")[:200], "shared_state": RESEARCH_ACTIVE})
            await self._publish_research_done(question, speech, card,
                                              self._task_ref(task_id, meta))
            logger.info("async deep research done: %s（%d 节/%d 源）",
                        self._topic(question), len(report.sections), len(report.sources))
        except Exception as e:
            logger.warning("async deep research failed for '%s': %s", question[:40], e)
            await self._close_task(task_id, FAILED_STATUS, progress=str(e)[:120])
            await self._publish_research_failed(question,
                                                self._task_ref(task_id, meta))

    async def _close_task(self, task_id: str, status: str, *, progress: str = "",
                          result_ref: dict | None = None) -> None:
        if task_id:
            await self.ledger.close(task_id, status, result_ref=result_ref,
                                    progress=progress)

    async def _finish_stopped(self, task_id: str, question: str,
                              meta: dict | None = None) -> None:
        """被截停的收尾：账本已是 cancelled（cancel/deadline/预算三种来源共用此路），
        这里只补一句主动告知——超时/预算截停用户没主动喊停，静默消失是不诚实的。"""
        task = await self.ledger.get(task_id) if task_id else None
        reason = task.stop_reason if task else ""
        logger.info("async deep research stopped (%s): %s", reason or "user",
                    self._topic(question))
        if reason in (STOP_DEADLINE, STOP_BUDGET):
            await self._publish_research_stopped(
                question, reason, self._task_ref(task_id, meta))

    def _task_ref(self, task_id: str, meta: dict | None = None) -> dict:
        """proactive 推送里的任务级归因（RFC §5 obs）：带回 task_id 与**受理轮** trace_id，
        dashboard 可从完成推送下钻回受理那一轮（后台任务脱离请求路径，trace 天然断链）。"""
        ref = {}
        if task_id:
            ref["task_id"] = task_id
        tid = (meta or {}).get("trace_id", "")
        if tid:
            ref["origin_trace_id"] = tid
        return ref

    async def _publish_research_done(self, question: str, speech: str, card: dict,
                                     ref: dict | None = None) -> None:
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
                   "ts": int(time.time() * 1000),
                   # 用户在等这个结果 → user_contract 档（免打扰/负荷/频控豁免，仅参与合并）
                   "priority": P_USER_CONTRACT,
                   "dedup_key": f"research.done|{topic}",
                   **(ref or {})}
        await publish_proactive(self._nc, payload)
        logger.info("async research proactive sent: %s", topic)

    async def _publish_research_failed(self, question: str,
                                       ref: dict | None = None) -> None:
        """异步调研失败 → 发一条简短主动告知（best-effort，无 NATS 静默）。"""
        if not self._nc:
            return
        payload = {"type": "research_failed",
                   "speech": f"抱歉，「{self._topic(question)}」的深度调研没能完成，稍后可以让我再试一次。",
                   "agent_id": self.manifest.agent_id, "ts": int(time.time() * 1000),
                   "priority": P_USER_CONTRACT,
                   "dedup_key": f"research.failed|{self._topic(question)}",
                   **(ref or {})}
        await publish_proactive(self._nc, payload)

    async def _publish_research_stopped(self, question: str, reason: str,
                                        ref: dict | None = None) -> None:
        """预算/超时截停 → 主动告知（用户没喊停，静默消失不诚实）。用户主动 cancel 不走这里
        （他刚说完「别查了」，再播一遍是噪声）。"""
        if not self._nc:
            return
        why = "查得太久超时停了" if reason == STOP_DEADLINE else "查到预算上限停了"
        payload = {"type": "research_stopped",
                   "speech": (f"「{self._topic(question)}」的深度调研{why}，"
                              "换个更聚焦的问题我可以再查一次。"),
                   "agent_id": self.manifest.agent_id, "ts": int(time.time() * 1000),
                   "priority": P_USER_CONTRACT,
                   "dedup_key": f"research.stopped|{self._topic(question)}",
                   **(ref or {})}
        await publish_proactive(self._nc, payload)

    # ── 任务账本消费面（M2 P0）：查询 / 取消 ────────────────────────────────
    async def _task_status(self, ctx) -> AgentResult:
        """「查得怎么样了」——**从账本读事实后确定性作答**，绝不让 LLM 编进度。

        无账本（PG 不可达）时诚实说不知道，不假装在跑（R9 契约：话术型降级走 OK）。
        """
        if not self.ledger.pg_ok and not await self.ledger.init():
            return AgentResult(
                speech="我这边暂时查不到后台任务的状态，抱歉。",
                follow_up="你可以直接让我重新查一次。")
        active = await self.ledger.query_active(ctx.user_id, kind=LEDGER_KIND)
        if active:
            lines = [self._active_line(t) for t in active[:3]]
            return AgentResult(
                speech="".join(lines),
                follow_up="不想查了就说「别查了」。",
                data={"tasks": [{"task_id": t.task_id, "status": t.status,
                                 "progress": t.progress} for t in active]})

        recent = await self.ledger.recent(ctx.user_id, kind=LEDGER_KIND, limit=1)
        if not recent:
            return AgentResult(
                speech="你最近没让我查过什么后台调研。",
                follow_up="想深挖某个主题就说「慢慢查一下 X，查完告诉我」。")
        return AgentResult(speech=self._finished_line(recent[0]),
                           follow_up="要我重新查一份吗？",
                           data={"task_id": recent[0].task_id,
                                 "status": recent[0].status})

    def _active_line(self, task) -> str:
        mins = max(1, int((time.time() - task.created_at) // 60)) if task.created_at else 0
        detail = f"，{task.progress}" if task.progress else ""
        elapsed = f"，已经查了 {mins} 分钟" if mins else ""
        return f"「{self._topic(task.goal)}」还在查{detail}{elapsed}。"

    def _finished_line(self, task) -> str:
        """终态话术：每种终态说清楚是**怎么**结束的——「中断的诚实报告」在此兑现。"""
        topic = self._topic(task.goal)
        if task.status == ORPHANED:
            return f"「{topic}」查到一半中断了（我这边重启过），要不要重新查一份？"
        if task.status == DONE:
            summary = str((task.result_ref or {}).get("summary") or "").strip()
            head = f"「{topic}」已经查完了。"
            return f"{head}{summary}" if summary else head + "要我再说一遍结论吗？"
        if task.status == CANCELLED:
            reason = task.stop_reason
            if reason == STOP_DEADLINE:
                return f"「{topic}」上次查得太久超时停了。"
            if reason == STOP_BUDGET:
                return f"「{topic}」上次查到预算上限停了。"
            return f"「{topic}」上次按你说的停掉了。"
        return f"「{topic}」上次没能查完。"

    async def _cancel_task(self, intent, ctx) -> AgentResult:
        """「别查了」——拉模式取消：置账本为 cancelled，后台任务下一次心跳自行收尾。

        多条同类在跑时先按原话匹配主题，仍消歧不掉才反问（不猜、不一次全停——
        停错了用户要重等几分钟）。
        """
        if not self.ledger.pg_ok and not await self.ledger.init():
            return AgentResult(
                speech="我这边暂时管不了后台任务，抱歉。",
                follow_up="")
        active = await self.ledger.query_active(ctx.user_id, kind=LEDGER_KIND)
        if not active:
            return AgentResult(speech="现在没有正在跑的调研。")
        if len(active) > 1:
            picked = self._match_by_text(active, intent)
            if picked is None:
                names = "、".join(f"「{self._topic(t.goal)}」" for t in active[:3])
                return AgentResult(
                    status=NEED_SLOT,
                    speech=f"现在有 {len(active)} 个调研在跑：{names}，你要停哪个？",
                    follow_up="说主题名字就行。", missing_slots=["query"])
            active = [picked]
        task = active[0]
        ok = await self.ledger.cancel(task.task_id, reason=STOP_USER)
        topic = self._topic(task.goal)
        if not ok:
            return AgentResult(speech=f"「{topic}」这会儿已经结束了，不用停了。")
        return AgentResult(
            speech=f"好的，正在停下「{topic}」的调研。",
            follow_up="", data={"task_id": task.task_id, "cancelled": True})

    @staticmethod
    def _match_by_text(tasks: list, intent) -> object | None:
        """多条在跑时按用户原话/槽位定位唯一一条（「停掉固态电池那个」）。

        只认**恰好一条**命中——两条都沾边说明还是歧义，交回反问；命中判据是
        任务主题里连续 2 字出现在原话中（中文双字词已足够判别，且不误伤单字噪声）。
        """
        text = f"{(intent.raw_text or '')} {' '.join((intent.slots or {}).values())}"
        hits = []
        for t in tasks:
            topic = DeepResearchAgent._topic(t.goal)
            grams = {topic[i:i + 2] for i in range(max(0, len(topic) - 1))}
            if any(g in text for g in grams if len(g) == 2):
                hits.append(t)
        return hits[0] if len(hits) == 1 else None

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
        raw = await ctx.load_shared_state(RESEARCH_ACTIVE)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return raw if isinstance(raw, dict) else None

    async def _save_task(self, ctx, question: str, report) -> None:
        """紧凑落 memory：保存问题 + 各节标题/正文摘要 + 各节引用 urls（≤3，供深挖种子复用），
        供下一轮『展开第N点』定位（best-effort）。"""
        try:
            url_by_idx = {s.get("idx"): (s.get("url") or "")
                          for s in (report.sources or []) if isinstance(s, dict)}
            await ctx.save_shared_state(RESEARCH_ACTIVE, {
                "question": question,
                "summary": (report.summary or "")[:300],
                "sections": [{
                    "heading": s.heading,
                    "body": (s.body or "")[:400],
                    "urls": [u for u in (url_by_idx.get(c) for c in (s.citations or [])[:3]) if u],
                } for s in report.sections],
                "freshness": report.freshness,
            })
        except Exception as e:
            logger.debug("save research task skipped: %s", e)

    async def _seed_from_prior(self, prior: dict | None, focus: str, meta) -> list:
        """深挖聚焦时复用上轮证据：取上轮该节引用的 urls（≤3）并行抓正文作种子 Evidence。

        旧 RESEARCH_ACTIVE 数据无 urls 字段 → 返回空（向后兼容，退回纯重新检索）；
        单条失败/超时静默吞（best-effort），extractor 缺席直接跳过。"""
        if not prior or not focus or self.extractor is None:
            return []
        sec = next((s for s in (prior.get("sections") or [])
                    if (s.get("heading") or "").strip() == focus), None)
        urls = [u for u in ((sec or {}).get("urls") or []) if u][:3]
        if not urls:
            return []

        async def grab(u: str):
            try:
                text = await asyncio.wait_for(self.extractor.extract(u, meta=meta),
                                              timeout=20)
            except Exception as e:
                logger.debug("seed extract skipped %s: %s", u, e)
                return None
            excerpt = _clean_excerpt(text or "")[:_EXCERPT_CAP]
            if not excerpt:
                return None
            return Evidence(title=focus, url=u, source="上轮调研引用", excerpt=excerpt)

        got = await asyncio.gather(*(grab(u) for u in urls))
        return [e for e in got if e is not None]

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
        rawv = await ctx.load_shared_state(NEWS_ACTIVE)
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

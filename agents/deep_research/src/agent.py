"""深度调研 Agent（P0）—— 独立 deep-research 一等 Agent。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」复刻进本 Agent：
`handle` 驱动 pipeline 四段（plan 提议多视角子问题 → investigate 有界并行迭代检索 →
synthesize 分节接地报告 → brief 一段式语音简报 + research_report 卡）。

护城河：接地「我」（位置/电量/画像作研究约束，P0 接时间/电量、P1 接位置/画像）+ 渐进语音 +
可落地产物。搜索 provider 进程内复用 info（info 拥有搜索 provider，跟随 trip_planner→navigation 先例）。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.grounding import shanghai_now
from agents.info.src.providers import build_search_provider, build_extractor
from .pipeline import plan, investigate, synthesize, brief
from .models import ResearchTask

logger = logging.getLogger("agent.deep_research")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class DeepResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 info 的搜索 + 正文补抓 provider（Exa→AnySearch→Bing→mock 降级链已在工厂内）。
        self.search = build_search_provider()
        self.extractor = build_extractor()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "research.run":
            return await self._research(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="深度调研助手暂不支持该请求。")

    async def _research(self, intent, ctx, meta) -> AgentResult:
        question = (intent.slots.get("query") or intent.slots.get("topic")
                    or intent.slots.get("question") or "").strip()
        if not question:
            question = (intent.raw_text or "").strip()
        if not question:
            return AgentResult(
                status=NEED_SLOT, speech="您想深入调研什么？",
                follow_up="告诉我调研主题，例如『深入调研一下固态电池』",
                missing_slots=["query"])

        constraints = self._constraints(meta)
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
        return AgentResult(
            speech=speech, ui_card=card,
            data={"question": question, "sections": len(report.sections),
                  "sources": report.sources, "confidence": report.overall_confidence,
                  "gaps": report.gaps})

    @staticmethod
    def _constraints(meta) -> dict:
        """收集把「我」接地进调研的处境。P0：时间 + 电量；位置/画像偏好留 P1 注入。"""
        c = {"time_now": f"{shanghai_now():%Y年%m月%d日}"}
        battery = str((meta or {}).get("vehicle_battery", "") or "").strip()
        if battery:
            c["vehicle_state"] = f"电量{battery.rstrip('%')}%"
        return c

"""车书 Agent —— 知识类生态 Agent 范本。演示 RAG：retrieve（检索）+ generate（生成）。

Phase 1：使用 Provider 适配层（mock/向量库 可切换）。

2026-08-15（阶段 1 / 卡 Q9）加了四道**确定性**护栏。它们存在的理由是同一句话：
**「不要编造」写在 system prompt 里不是护栏，只是一句请求。**
  ① 检索零命中 → 直接诚实弃权，**一次 LLM 都不调**。
  ② 来源类型（manual/web/mock）随资料一起传到话术层与卡片；
     非真实手册来源**不得**被表述成「本车型手册」。
  ③ 安全信号（警告灯/亮灯/漏气/失灵…）命中 → 先给**确定性分级处置**；
     且在没有真实手册的情况下**不进 LLM**，避免把演示数值说成权威值。
  ④ 卡片盖 `_prov`（此前 manual-rag/road-safety/chitchat 三个 Agent 覆盖为 0）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT
from agents._sdk.provenance import attach
from runtime.safety_signal import alert_advice, alert_level, alert_signal
from .providers import build_knowledge_retriever

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM_MANUAL = (
    "你是车型手册问答助手。只依据【参考资料】回答用户问题，简洁口语化，两三句话内。"
    "若资料中没有相关信息，明确说『手册里没有查到，建议联系客服』，不要编造。"
)
# 非真实手册来源（演示语料/联网检索）：资料**不绑定任何车型**，措辞必须如实。
# 这不是「换个说法」——它决定用户会不会拿一个通用参考值去给自己的车充气。
_SYSTEM_GENERIC = (
    "你是用车知识助手。【参考资料】不是本车的车型手册，而是通用资料，"
    "**不绑定任何具体车型**。只依据【参考资料】回答，简洁口语化，两三句话内。"
    "涉及具体数值时必须说明这是通用参考、请以车辆铭牌或随车手册为准。"
    "资料中没有的内容明确说没有查到，不要编造。"
)

# 安全信号判据的**唯一实现**在 `runtime/safety_signal.py`（road-safety 与
# chitchat 是同一份的另外两个消费方）。这里曾经有一份本地副本——收口发生在
# 第三个消费方出现的**当天**，不是等它错了再收（§4.3 时区族那笔账）。
_UNVERIFIED_NUMBERS = "具体数值请以车辆铭牌或随车手册为准，我这里没有本车型的权威数据。"
_safety_level = alert_level


class ManualRagAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.kb = build_knowledge_retriever()

    @staticmethod
    def _safety_data(level: str, question: str, **extra) -> dict:
        """`data` 载荷。安全信号命中时经**保留键 `_safety_alert`** 声明会话级安全态
        （编排通用消费，登记 conventions §9.1 同族；契约与校验在
        `orchestrator/cloud/context.py::_valid_safety_alert`）。

        为什么要跨轮：QA 轮 SF3 三轮实测——红色机油灯之后第二轮答天气、
        第三轮执行音量。**一次安全警告必须是会话状态，不能是一句话说完就没了。**
        """
        data = {"safety_signal": level, **extra}
        if level:
            # signal 取原话里命中的那个词，不取整句——整句进 prompt 会把
            # 用户的措辞当成告警名字（「慢一点开可以吗」不是一个告警）。
            data["_safety_alert"] = {
                "level": level, "signal": alert_signal(question) or "车辆告警"}
        return data

    def _card(self, chunks, source_type: str) -> dict:
        return attach({
            "type": "manual",
            "source_type": source_type,
            "sources": [c.source for c in chunks if c.source],
            "chunks": [{"content": c.content, "source": c.source} for c in chunks],
        }, self.kb)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        question = intent.raw_text or intent.slots.get("question", "")
        if not question:
            return AgentResult(status=NEED_SLOT, speech="您想了解车辆的哪方面？")

        level = _safety_level(question)
        chunks = await self.kb.retrieve(question)          # 1) retrieve

        # ① 零命中短路：不调 LLM。安全信号仍要给处置建议——**没有资料不等于没有风险**。
        if not chunks:
            speech = "手册里没有查到这方面的内容，建议联系客服或前往服务点确认。"
            if level:
                speech = f"{alert_advice(level)}{speech}"
            return AgentResult(speech=speech,
                               data=self._safety_data(level, question),
                               ui_card=self._card([], ""))

        # 来源类型取本轮实际检索到的资料（混合来源时只要有一条不是真手册，
        # 就按非权威处理——**权威性取最低的那条**，不取最高的）。
        types = {getattr(c, "source_type", "manual") or "manual" for c in chunks}
        source_type = "manual" if types == {"manual"} else sorted(types)[0]
        authoritative = source_type == "manual"

        # ③ 安全信号 + 无权威手册 → **不进 LLM**，只给确定性处置。
        # 理由：这一档下模型唯一能引用的就是演示语料里的数值，说出去就是把
        # 通用参考冒充成本车权威值（QA 轮 I-036 的完整形态）。
        if level and not authoritative:
            advice = alert_advice(level)
            return AgentResult(
                speech=f"{advice}{_UNVERIFIED_NUMBERS}",
                data=self._safety_data(level, question, source_type=source_type),
                ui_card=self._card(chunks, source_type))

        context_block = "\n".join(f"- {c.content}" for c in chunks)
        answer = await self.llm.complete([                  # 2) generate
            {"role": "system",
             "content": _SYSTEM_MANUAL if authoritative else _SYSTEM_GENERIC},
            {"role": "user", "content": f"【参考资料】\n{context_block}\n\n【问题】{question}"},
        ], temperature=0.2, max_tokens=200)

        # 安全信号（有权威手册）：处置建议**前置**，不让它淹没在模型话术里。
        speech = f"{alert_advice(level)}{answer}" \
            if level else answer
        return AgentResult(
            speech=speech,
            data=self._safety_data(level, question, source_type=source_type),
            ui_card=self._card(chunks, source_type),
        )

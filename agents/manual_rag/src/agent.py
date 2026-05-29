"""车书 Agent —— 知识类生态 Agent 范本。演示 RAG：retrieve（检索）+ generate（生成）。

Phase 1：使用 Provider 适配层（mock/向量库 可切换）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT
from .providers import build_knowledge_retriever

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM = (
    "你是车型手册问答助手。只依据【参考资料】回答用户问题，简洁口语化，两三句话内。"
    "若资料中没有相关信息，明确说『手册里没有查到，建议联系客服』，不要编造。"
)


class ManualRagAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.kb = build_knowledge_retriever()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        question = intent.raw_text or intent.slots.get("question", "")
        if not question:
            return AgentResult(status=NEED_SLOT, speech="您想了解车辆的哪方面？")

        chunks = await self.kb.retrieve(question)          # 1) retrieve
        context_block = "\n".join(f"- {c.content}" for c in chunks)
        sources = [c.source for c in chunks if c.source]
        answer = await self.llm.complete([                  # 2) generate
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"【参考资料】\n{context_block}\n\n【问题】{question}"},
        ], temperature=0.2, max_tokens=200)
        return AgentResult(
            speech=answer,
            ui_card={"type": "manual", "sources": sources, "chunks": [{"content": c.content, "source": c.source} for c in chunks]},
        )

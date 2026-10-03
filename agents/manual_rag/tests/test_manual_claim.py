"""内部意图 manual.claim：手册对这句有没有把握——只跑词法、不调模型、不出卡片。

闲聊兜底遇到车辆功能问句时来问（docs/design/2026-10-04-chitchat-defers-to-confident-manual.md）；判据与手册跳过
目录路由用的是同一条（`_confident`）。
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent
from agents.manual_rag.src.providers.base import Chunk


@pytest.fixture(autouse=True)
def _mock_knowledge(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VENDOR", "mock")
    monkeypatch.delenv("REQUIRE_REAL_PROVIDERS", raising=False)


class _KB:
    def __init__(self, chunks, unknown=()):
        self.chunks, self.unknown, self.queries = list(chunks), list(unknown), []

    async def retrieve(self, query, vehicle_model="", top_k=4):
        self.queries.append((query, vehicle_model))
        return list(self.chunks)

    def unknown_subject_terms(self, question):
        return list(self.unknown)


_SURE = Chunk(content="空调温度在中控屏空调控制界面调节", source="空调控制", coverage=0.9, section_hit=True)


def _claim(kb, text="空调温度怎么调？", slots=None):
    agent = ManualRagAgent()
    agent.kb = kb
    agent.llm.complete = AsyncMock(return_value="不该被调用")
    res = asyncio.run(run_handle(agent, "manual.claim", slots=slots, raw_text=text))
    agent.llm.complete.assert_not_awaited()
    assert res.speech == "" and not res.ui_card
    return res.data


def test_claims_when_the_first_page_is_confident():
    kb = _KB([_SURE])
    assert _claim(kb, slots={"vehicle_model": "SU7"}) == {"confident": True}
    assert kb.queries == [("空调温度怎么调？", "SU7")]


def test_no_claim_on_a_body_only_hit_unknown_terms_no_hit_or_no_question():
    assert _claim(_KB([Chunk(content="x", coverage=0.9, section_hit=False)])) == {"confident": False}
    assert _claim(_KB([_SURE], unknown=["奇骏"])) == {"confident": False}
    assert _claim(_KB([])) == {"confident": False}
    assert _claim(_KB([_SURE]), text="") == {"confident": False}

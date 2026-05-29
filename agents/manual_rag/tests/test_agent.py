"""manual-rag 契约测试。验证 RAG 检索命中 + 生成（mock LLM）。"""
import asyncio
from unittest.mock import AsyncMock

from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent


def test_query_retrieves_and_answers():
    agent = ManualRagAgent()
    agent.llm.complete = AsyncMock(return_value="推荐胎压为前后轮 2.4–2.5 bar。")
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "manual"
    # 检索应命中"胎压"条目
    assert any("胎压" in s for s in res.ui_card["sources"])


def test_query_missing_question_asks():
    res = asyncio.run(run_handle(ManualRagAgent(), "manual.query", raw_text=""))
    assert res.status == "need_slot"

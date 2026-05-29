"""chitchat 契约测试。mock 掉 LLM 调用，只验证编排逻辑。"""
import asyncio
from unittest.mock import AsyncMock

from agents._sdk.testing import run_handle
from agents.chitchat.src.agent import ChitchatAgent


def test_talk_returns_speech():
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="哈哈，那我给你讲个冷笑话～")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话"))
    assert res.status == "ok"
    assert res.speech == "哈哈，那我给你讲个冷笑话～"

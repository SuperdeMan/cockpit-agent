"""chitchat 契约测试。mock 掉 LLM 调用，只验证编排逻辑。"""
import asyncio
import os
from unittest.mock import AsyncMock

from agents._sdk.testing import make_context, run_handle
from agents.chitchat.src.agent import ChitchatAgent, _resolve_model, _length, _system


def test_chitchat_injects_recalled_personal_memory():
    """记住宠物名：召回到的个人信息注入 chitchat system prompt，使其答得上。"""
    agent = ChitchatAgent()
    captured = {}

    async def fake_complete(messages, **kw):
        captured["messages"] = messages
        return "您的宠物叫旺财呀～"

    agent.llm.complete = fake_complete
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户的宠物叫旺财", "scope": "profile.person",
         "predicate": "person.pet", "confidence": 0.9}]
    res = asyncio.run(run_handle(agent, "chitchat.talk",
                                 raw_text="我的宠物叫什么名字", ctx=ctx))
    assert res.status == "ok"
    assert "旺财" in captured["messages"][0]["content"]  # 召回的宠物名进了 system


def test_talk_returns_speech():
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="哈哈，那我给你讲个冷笑话～")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话"))
    assert res.status == "ok"
    assert res.speech == "哈哈，那我给你讲个冷笑话～"


# ─── task 4：开放域模型分层 + 话术长度 + 昵称 ───

def test_model_tiering_by_pref():
    old = {k: os.environ.get(k) for k in ("LLM_MODEL_FAST", "LLM_MODEL_PRIMARY")}
    os.environ["LLM_MODEL_FAST"] = "mimo-v2.5"
    os.environ["LLM_MODEL_PRIMARY"] = "mimo-v2.5-pro"
    try:
        assert _resolve_model({"model_pref": "deep"}) == "mimo-v2.5-pro"   # 深度→重模型
        assert _resolve_model({"model_pref": "fast"}) == "mimo-v2.5"       # 快速→快模型
        assert _resolve_model({}) == "mimo-v2.5"                            # 默认开放域走快模型
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_length_and_name_honored():
    assert _length({"answer_length": "short"})[0] == 140
    assert _length({"answer_length": "detailed"})[0] == 440
    assert _length({})[0] == 220
    assert "小航" in _system({"assistant_name": "小航"})


def test_handle_passes_fast_model_and_tokens():
    """handle 把分层模型与长度对应的 max_tokens 透传给 LLM。"""
    agent = ChitchatAgent()
    captured = {}

    async def fake_complete(messages, model="", temperature=0.7, max_tokens=512):
        captured["model"], captured["max_tokens"] = model, max_tokens
        return "好的"

    agent.llm.complete = fake_complete
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话",
                                 meta={"model_pref": "fast", "answer_length": "short"}))
    assert res.speech == "好的"
    assert captured["model"] == os.getenv("LLM_MODEL_FAST", os.getenv("LLM_MODEL_FALLBACK", "mimo-v2.5"))
    assert captured["max_tokens"] == 140

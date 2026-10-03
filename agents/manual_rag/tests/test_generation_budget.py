"""手册生成守住步骤预算：模型慢时赶在执行器切步之前带引用卡片如实降级（固定语料 V201/V202）。

修前生成不传超时（客户端缺省 10 s）、超时后再重试一次，两次之和超过清单 15 s 预算，执行器把整步切掉——
降级分支走不到，用户连卡片都没有。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents._sdk.testing import run_handle
from agents.manual_rag.src import agent as manual
from agents.manual_rag.src.agent import ManualRagAgent


@pytest.fixture(autouse=True)
def _mock_knowledge(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VENDOR", "mock")
    monkeypatch.delenv("REQUIRE_REAL_PROVIDERS", raising=False)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _slow_llm(clock, timeouts):
    async def complete(messages, **kw):
        timeouts.append(kw.get("timeout"))
        clock.now += kw["timeout"]          # 模型一直没回来，直到这次调用的超时
        raise RuntimeError("LLM Gateway error: DEADLINE_EXCEEDED: slow provider")
    return complete


def test_slow_generation_degrades_with_the_card_inside_the_step_budget(monkeypatch):
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(manual, "_clock", clock)
    agent = ManualRagAgent()
    agent.llm.complete = _slow_llm(clock, timeouts)
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    budget_s = agent.manifest.latency_budget_ms / 1000
    assert clock.now - 1000.0 <= budget_s - manual._BUDGET_MARGIN_S      # 赶在执行器切步之前收尾
    assert timeouts and all(t <= manual._LLM_TIMEOUT_S for t in timeouts)
    assert res.ui_card["type"] == "manual" and "摘要生成暂时不可用" in res.speech


def test_no_time_left_skips_generation_and_says_why(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(manual, "_clock", clock)
    agent = ManualRagAgent()
    agent.llm.complete = AsyncMock(return_value="不该被调用")
    answer, state = asyncio.run(agent._generate_answer([], deadline=clock.now + manual._MIN_GENERATION_S - 0.5))
    assert (answer, state) == (None, "out_of_budget")
    agent.llm.complete.assert_not_awaited()


def test_skipped_generation_is_marked_as_a_budget_degrade(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(manual, "_clock", clock)
    agent = ManualRagAgent()
    agent.manifest.latency_budget_ms = 3500          # 余量 1 s 之后只剩 2.5 s：不够一次生成
    agent.llm.complete = AsyncMock(return_value="不该被调用")
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    assert res.data["generation_degraded"] == "step_budget" and res.ui_card["type"] == "manual"
    agent.llm.complete.assert_not_awaited()


def test_without_a_declared_budget_the_client_default_applies():
    agent = ManualRagAgent()
    agent.manifest.latency_budget_ms = 0
    agent.llm.complete = AsyncMock(return_value="推荐胎压为前后轮 2.4–2.5 bar。")
    asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    assert agent.llm.complete.await_args.kwargs["timeout"] == manual._LLM_TIMEOUT_S

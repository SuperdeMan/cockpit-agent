"""契约测试夹具：不起 gRPC server，直接驱动 Agent.handle 做黄金用例断言。

用法（在 agents/<name>/tests/ 中）::

    import pytest
    from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
    from agents.navigation.src.agent import NavigationAgent

    @pytest.mark.asyncio
    async def test_search_poi():
        agent = NavigationAgent()
        res = await run_handle(agent, "navigation.search_poi",
                               slots={"keyword": "充电站"}, raw_text="附近的充电站")
        assert res.status == "ok"
        assert any(a["type"] == "navigate" for a in res.actions) or res.speech

    def test_manifest():
        assert assert_manifest_consistent(NavigationAgent()) is True
"""
from __future__ import annotations
from unittest.mock import AsyncMock
from typing import AsyncIterator

from .base import Context, IntentView
from .result import AgentResult


def make_context(session_id: str = "test-sess", user_id: str = "u1",
                 vehicle_id: str = "v1", context_values: dict | None = None,
                 history: list | None = None) -> Context:
    mem = AsyncMock()
    mem.get_context.return_value = context_values or {}
    mem.get_session.return_value = history or []
    return Context(session_id, user_id, vehicle_id, mem)


async def run_handle(agent, intent_name: str, slots: dict | None = None,
                     raw_text: str = "", confidence: float = 0.9,
                     ctx: Context | None = None, meta: dict | None = None) -> AgentResult:
    iv = IntentView(intent_name, slots or {}, raw_text, confidence)
    return await agent.handle(iv, ctx or make_context(), meta or {})


async def run_handle_stream(agent, intent_name: str, slots: dict | None = None,
                            raw_text: str = "", confidence: float = 0.9,
                            ctx: Context | None = None,
                            meta: dict | None = None) -> list[tuple[str, object]]:
    """运行 handle_stream 并收集所有事件。返回 [(kind, payload), ...]。"""
    iv = IntentView(intent_name, slots or {}, raw_text, confidence)
    events = []
    async for kind, payload in agent.handle_stream(iv, ctx or make_context(), meta or {}):
        events.append((kind, payload))
    return events


def assert_manifest_consistent(agent) -> bool:
    """校验 Agent manifest 一致性：agent_id 存在、有 capabilities、category 合法。"""
    m = agent.manifest
    assert m.agent_id, "manifest.agent_id is empty"
    assert m.version, f"{m.agent_id}: manifest.version is empty"
    assert m.category in ("core", "ecosystem"), f"{m.agent_id}: invalid category {m.category}"
    assert m.trust_level in ("system", "first_party", "third_party"), \
        f"{m.agent_id}: invalid trust_level {m.trust_level}"
    assert m.deployment in ("edge", "cloud"), f"{m.agent_id}: invalid deployment {m.deployment}"
    assert len(m.capabilities) > 0, f"{m.agent_id}: no capabilities declared"
    for cap in m.capabilities:
        assert cap.intent, f"{m.agent_id}: capability has empty intent"
        assert "." in cap.intent, f"{m.agent_id}: intent '{cap.intent}' not in domain.action format"
    return True


def assert_result_valid(res: AgentResult, expected_status: str = None):
    """校验 AgentResult 结构合法性。"""
    assert res.speech, "speech is empty"
    if expected_status:
        assert res.status == expected_status, f"status={res.status}, expected={expected_status}"
    for a in res.actions:
        assert "type" in a, f"action missing 'type': {a}"

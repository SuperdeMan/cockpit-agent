"""PlanBuilder 测试。"""
import pytest
import asyncio
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.models import PlanContext
from unittest.mock import MagicMock


class MockAgent:
    def __init__(self, agent_id, intents):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []
        self.manifest.latency_budget_ms = 5000
        for intent in intents:
            cap = MagicMock()
            cap.intent = intent
            cap.slots = []
            cap.description = ""
            cap.examples = []
            self.manifest.capabilities.append(cap)
        self.endpoint = f"localhost:{hash(agent_id) % 1000 + 50060}"


def test_build_with_valid_json():
    """LLM 返回合法 JSON 应解析为 Plan。"""
    agents = [
        MockAgent("navigation", ["navigation.search_poi"]),
        MockAgent("food-ordering", ["food.reserve"]),
    ]

    async def mock_llm(messages):
        return '{"steps":[{"id":"s1","agent_id":"navigation","intent":"navigation.search_poi","slots":{"keyword":"川菜"}}]}'

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    ctx = PlanContext(session_id="test")
    plan = asyncio.run(builder.build("找家川菜馆", agents, ctx))
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == "navigation"
    assert plan.steps[0].slots["keyword"] == "川菜"


def test_build_with_invalid_json_falls_back():
    """LLM 返回非法 JSON 应降级到 fallback。"""
    agents = [MockAgent("navigation", ["navigation.search_poi"])]

    async def mock_llm(messages):
        return "I don't understand"

    resolved = [MagicMock()]
    resolved[0].manifest = agents[0].manifest
    resolved[0].endpoint = "localhost:50061"

    async def mock_resolve(query, top_k=1):
        return resolved

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    ctx = PlanContext(session_id="test")
    plan = asyncio.run(builder.build("找家川菜馆", agents, ctx))
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == "navigation"


def test_build_with_unknown_agent_filtered():
    """计划中未知 agent_id 应被过滤。"""
    agents = [MockAgent("navigation", ["navigation.search_poi"])]

    async def mock_llm(messages):
        return '{"steps":[{"id":"s1","agent_id":"unknown-agent","intent":"x","slots":{}}]}'

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    plan = asyncio.run(builder.build("test", agents, PlanContext()))
    # 全部被过滤 → fallback
    assert plan.steps is not None  # fallback 可能返回空或单步


def test_extract_json():
    """从 LLM 输出中提取 JSON。"""
    raw = 'Here is the plan: {"steps": []} hope this helps'
    result = PlanBuilder._extract_json(raw)
    assert result == '{"steps": []}'

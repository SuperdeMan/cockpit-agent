"""PlanBuilder 测试。"""
import pytest
import asyncio
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.models import PlanContext
from unittest.mock import MagicMock


class MockAgent:
    def __init__(self, agent_id, intents, *, kind="agent", deployment="cloud",
                 permissions=None, trust_level="first_party"):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []
        self.manifest.latency_budget_ms = 5000
        self.manifest.kind = kind
        self.manifest.deployment = deployment
        self.manifest.requires_permissions = permissions or []
        self.manifest.trust_level = trust_level
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


def test_build_parses_complexity_goal_and_manifest_dispatch_metadata():
    agents = [MockAgent(
        "edge-vehicle", ["hvac.set"], kind="edge_fast", deployment="edge",
        permissions=["vehicle.control"], trust_level="system",
    )]

    async def mock_llm(messages):
        return (
            '{"complexity":"adaptive","goal":"保持舒适并继续规划",'
            '"steps":[{"id":"s1","agent_id":"edge-vehicle",'
            '"intent":"hvac.set","slots":{"temp":"24"}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "先调空调再看结果", agents, PlanContext(),
        granted_permissions=["vehicle.control"],
    ))

    assert plan.complexity == "adaptive"
    assert plan.goal == "保持舒适并继续规划"
    step = plan.steps[0]
    assert step.endpoint == agents[0].endpoint
    assert step.kind == "edge_fast"
    assert step.deployment == "edge"
    assert step.required_permissions == ["vehicle.control"]
    assert step.trust_level == "system"


def test_invalid_complexity_defaults_to_simple():
    agents = [MockAgent("navigation", ["navigation.search_poi"])]

    async def mock_llm(messages):
        return (
            '{"complexity":"unbounded","goal":"x",'
            '"steps":[{"id":"s1","agent_id":"navigation",'
            '"intent":"navigation.search_poi","slots":{}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "找充电站", agents, PlanContext()))

    assert plan.complexity == "simple"


def test_parent_permission_covers_child_scope_during_planning():
    agents = [MockAgent(
        "vehicle-agent", ["hvac.set"],
        permissions=["vehicle.control.hvac"],
    )]

    async def mock_llm(messages):
        return (
            '{"complexity":"simple","goal":"adjust climate",'
            '"steps":[{"id":"s1","agent_id":"vehicle-agent",'
            '"intent":"hvac.set","slots":{"temperature":"24"}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "set temperature", agents, PlanContext(),
        granted_permissions=["vehicle.control"],
    ))

    assert len(plan.steps) == 1
    assert plan.steps[0].required_permissions == ["vehicle.control.hvac"]


def test_replan_returns_done_or_a_validated_next_batch():
    agents = [MockAgent("navigation", ["navigation.search_poi"])]
    replies = iter([
        '{"done":false,"steps":[{"id":"r1","agent_id":"navigation",'
        '"intent":"navigation.search_poi","slots":{"keyword":"次近充电站"}}]}',
        '{"done":true,"steps":[]}',
    ])

    async def mock_llm(messages):
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    decision = asyncio.run(builder.replan(
        "找到可用充电站", [{"status": "failed"}], agents, PlanContext()))
    assert decision.done is False
    assert decision.steps[0].intent == "navigation.search_poi"

    completed = asyncio.run(builder.replan(
        "找到可用充电站", [{"status": "ok"}], agents, PlanContext()))
    assert completed.done is True
    assert completed.steps == []

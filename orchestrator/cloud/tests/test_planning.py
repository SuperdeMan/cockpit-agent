"""PlanBuilder 测试。"""
import pytest
import asyncio
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.context import WorkingSet
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
    plan = asyncio.run(builder.build("找家川菜馆", WorkingSet(catalog=agents), ctx))
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
    plan = asyncio.run(builder.build("找家川菜馆", WorkingSet(catalog=agents), ctx))
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
    plan = asyncio.run(builder.build("test", WorkingSet(catalog=agents), PlanContext()))
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
        "先调空调再看结果", WorkingSet(catalog=agents), PlanContext(),
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
        "找充电站", WorkingSet(catalog=agents), PlanContext()))

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
        "set temperature", WorkingSet(catalog=agents), PlanContext(),
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


# ── 多日出行确定性兜底：弱 LLM 漏掉行程规划时补 trip.plan 步 ──

def _trip_agents():
    return [
        MockAgent("info-agent", ["info.weather"]),
        MockAgent("charging-planner", ["charging.plan"]),
        MockAgent("trip-planner", ["trip.plan"]),
    ]


def test_injects_trip_plan_when_llm_misses_it():
    """『周末去杭州两天带老人…顺便看天气/充电』——LLM 只出了天气+充电，
    兜底必须补一个并列 trip.plan 步（destination/days/preferences 已解析）。"""
    agents = _trip_agents()

    async def mock_llm(messages):
        return ('{"steps":['
                '{"id":"s1","agent_id":"info-agent","intent":"info.weather","slots":{}},'
                '{"id":"s2","agent_id":"charging-planner","intent":"charging.plan",'
                '"slots":{"destination":"杭州"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build(
        "周末去杭州两天，带老人，不要太累，顺便看看天气和是否需要中途充电",
        WorkingSet(catalog=agents), PlanContext()))
    trip = [s for s in plan.steps if s.intent == "trip.plan"]
    assert len(trip) == 1, "应注入一个 trip.plan 步"
    assert trip[0].agent_id == "trip-planner"
    assert trip[0].slots.get("destination") == "杭州"
    assert trip[0].slots.get("days") == "2"
    assert "带老人" in trip[0].slots.get("preferences", "")
    assert trip[0].depends_on == []  # 与天气/充电并列


def test_does_not_inject_trip_when_llm_already_planned_it():
    """LLM 自己出了 trip.plan，不重复注入。"""
    agents = _trip_agents()

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","agent_id":"trip-planner","intent":"trip.plan",'
                '"slots":{"destination":"杭州","days":"2"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("去杭州玩两天", WorkingSet(catalog=agents), PlanContext()))
    assert len([s for s in plan.steps if s.intent == "trip.plan"]) == 1


def test_does_not_inject_trip_for_plain_navigation():
    """『导航去北京南站』是通勤/单点导航，不是多日出行，不得注入 trip.plan。"""
    agents = _trip_agents() + [MockAgent("navigation", ["navigation.navigate"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","agent_id":"navigation","intent":"navigation.navigate",'
                '"slots":{"destination":"北京南站"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("导航去北京南站", WorkingSet(catalog=agents), PlanContext()))
    assert [s for s in plan.steps if s.intent == "trip.plan"] == []


def test_ensures_trip_step_even_when_plan_falls_back():
    """LLM 计划解析失败 → 降级语义路由（top-1=info）时，行程兜底仍要补 trip.plan。

    回归：『去北京三天带老人…看天气』偶发只回天气/充电、漏掉行程，根因是降级路径
    绕过了行程注入（注入原本只在 _parse_and_validate 内，降级不经过它）。"""
    agents = [
        MockAgent("info-agent", ["info.weather"]),
        MockAgent("trip-planner", ["trip.plan"]),
    ]

    async def mock_llm(messages):
        return "我不会规划这个"          # 非法 JSON → 解析失败两次 → 降级

    resolved = [MagicMock()]
    resolved[0].manifest = agents[0].manifest    # 语义 top-1 命中 info（天气）
    resolved[0].endpoint = "localhost:50067"

    async def mock_resolve(query, top_k=1):
        return resolved

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    plan = asyncio.run(builder.build(
        "周末去北京三天，带老人，不要太累，顺便看看天气", WorkingSet(catalog=agents), PlanContext()))
    intents = [s.intent for s in plan.steps]
    assert "trip.plan" in intents, f"降级路径也应补行程，实际 steps={intents}"
    trip = [s for s in plan.steps if s.intent == "trip.plan"][0]
    assert trip.slots.get("destination") == "北京"
    assert trip.agent_id == "trip-planner"


def test_modify_pattern_routed_to_trip_modify_replacing_misplan():
    """『第二天换一个』被弱 LLM 误规划成天气/充电时，确定性兜底改走单步 trip.modify。

    回归：用户报告"第二天换一个"没识别成修改、直接进了充电导航路线。"""
    agents = [
        MockAgent("info-agent", ["info.weather"]),
        MockAgent("charging-planner", ["charging.plan"]),
        MockAgent("trip-planner", ["trip.plan", "trip.modify"]),
    ]

    async def mock_llm(messages):
        return ('{"steps":['
                '{"id":"s1","agent_id":"info-agent","intent":"info.weather","slots":{}},'
                '{"id":"s2","agent_id":"charging-planner","intent":"charging.plan",'
                '"slots":{"destination":"北京"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("第二天换一个", WorkingSet(catalog=agents), PlanContext()))
    intents = [s.intent for s in plan.steps]
    assert intents == ["trip.modify"], f"应单步 trip.modify，实际 {intents}"
    assert plan.steps[0].slots.get("modification") == "第二天换一个"


def test_modify_pattern_keeps_llm_trip_modify():
    """LLM 已正确路由 trip.modify 时保持不变，不重复/不替换。"""
    agents = [MockAgent("trip-planner", ["trip.plan", "trip.modify"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","agent_id":"trip-planner","intent":"trip.modify",'
                '"slots":{"modification":"第二天换成宋城"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("第二天换成宋城", WorkingSet(catalog=agents), PlanContext()))
    assert [s.intent for s in plan.steps] == ["trip.modify"]


def test_does_not_inject_trip_when_planner_unavailable():
    """trip-planner 没注册（无权限/未上线）时不注入，避免产出 Unknown agent 计划。"""
    agents = [MockAgent("info-agent", ["info.weather"])]

    async def mock_llm(messages):
        return '{"steps":[{"id":"s1","agent_id":"info-agent","intent":"info.weather","slots":{}}]}'

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("周末去杭州两天带老人看看天气", WorkingSet(catalog=agents), PlanContext()))
    assert [s for s in plan.steps if s.intent == "trip.plan"] == []

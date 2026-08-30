"""PlanBuilder 测试。"""
import pytest
import asyncio
from orchestrator.cloud.planning import (
    PlanBuilder, _REPLAN_SYSTEM, _assemble_capability_catalog, _planner_system,
)
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.context import WorkingSet
from unittest.mock import MagicMock
import os
from agents._sdk.manifest import load_manifest

# 路由知识已从 planning.py 迁到各 Agent manifest.route_hints（R2.1）。
# 测试用真实 manifest 的 route_hints 驱动路由——既验证引擎接线，也验证迁移后的正则本身。
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
_ROUTE_HINT_MANIFESTS = {
    "deep-research": "agents/deep_research/manifest.yaml",
    "trip-planner": "agents/trip_planner/manifest.yaml",
}


def _load_route_hints(agent_id):
    rel = _ROUTE_HINT_MANIFESTS.get(agent_id)
    if not rel:
        return []
    return list(load_manifest(os.path.join(_REPO_ROOT, rel)).route_hints)


class MockAgent:
    def __init__(self, agent_id, intents, *, kind="agent", deployment="cloud",
                 permissions=None, trust_level="first_party",
                 whole_utterance=(), require_confirm=(), response_only=()):
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
            cap.heavy = False          # 真 bool，避免 MagicMock 恒真误标 step.heavy
            # 同上，理由一模一样：`bool(MagicMock())` 恒真，不显式给假值的话
            # **每个 mock 能力都会被当成整句型**，同 intent 的第二步被静默收掉。
            cap.whole_utterance = intent in (whole_utterance or ())
            cap.require_confirm = intent in (require_confirm or ())
            cap.response_only = intent in (response_only or ())
            self.manifest.capabilities.append(cap)
        # 真实 manifest 的确定性路由提示（R2.1）；未声明的 agent 为空列表。
        self.manifest.route_hints = _load_route_hints(agent_id)
        self.endpoint = f"localhost:{hash(agent_id) % 1000 + 50060}"


def test_mock_agent_confirmation_flags_are_plain_booleans():
    agent = MockAgent(
        "demo",
        ["demo.query", "demo.write"],
        require_confirm=("demo.write",),
        response_only=("demo.query",),
    )

    flags = [cap.require_confirm for cap in agent.manifest.capabilities]
    assert flags == [False, True]
    assert all(type(flag) is bool for flag in flags)
    response_flags = [cap.response_only for cap in agent.manifest.capabilities]
    assert response_flags == [True, False]
    assert all(type(flag) is bool for flag in response_flags)


def test_build_with_valid_json():
    """LLM 返回合法 JSON 应解析为 Plan。"""
    agents = [
        MockAgent("navigation", ["navigation.search_poi"]),
        MockAgent("nearby", ["nearby.order"]),
    ]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001",'
                '"slots":{"keyword":"川菜"},"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    ctx = PlanContext(session_id="test")
    plan = asyncio.run(builder.build("找家川菜馆", WorkingSet(catalog=agents), ctx))
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == "navigation"
    assert plan.steps[0].slots["keyword"] == "川菜"


def test_build_retries_simple_goal_that_declares_sequence_but_returns_one_step():
    agents = [
        MockAgent("alpha", ["alpha.search"]),
        MockAgent("beta", ["beta.detail"]),
    ]
    replies = iter([
        '{"complexity":"simple","goal":"先搜索候选，再查看详情",'
        '"steps":[{"id":"s1","capability_ref":"cap_0001",'
        '"slots":{},"depends_on":[],"slot_refs":{}}]}',
        '{"complexity":"simple","goal":"先搜索候选，再查看详情","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}},'
        '{"id":"s2","capability_ref":"cap_0002","slots":{},'
        '"depends_on":["s1"],"slot_refs":{}}]}',
    ])
    users = []

    async def mock_llm(messages):
        users.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "先搜索候选，再查看详情", WorkingSet(catalog=agents), PlanContext()))

    assert len(users) == 2
    assert [step.intent for step in plan.steps] == ["alpha.search", "beta.detail"]
    assert "逐项对照本轮 capability description/contract" in users[1]


@pytest.mark.parametrize(("complexity", "goal"), [
    ("adaptive", "先查询状态，然后根据结果决定下一步"),
    ("simple", "首先请暂时不要执行前项，再完成后项"),
])
def test_build_does_not_force_deferred_or_negated_sequence_steps(complexity, goal):
    agents = [MockAgent("alpha", ["alpha.one"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return (
            f'{{"complexity":"{complexity}","goal":"{goal}",'
            '"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        goal, WorkingSet(catalog=agents), PlanContext()))

    assert calls == 1
    assert [step.intent for step in plan.steps] == ["alpha.one"]


def test_build_rechecks_but_does_not_split_heavy_capability_owned_sequence():
    agent = MockAgent("alpha", ["alpha.plan"])
    agent.manifest.capabilities[0].heavy = True
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return (
            '{"complexity":"simple","goal":"先判断条件，不足再生成方案",'
            '"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "先判断条件，不足再生成方案",
        WorkingSet(catalog=[agent]), PlanContext()))

    assert calls == 2
    assert [step.intent for step in plan.steps] == ["alpha.plan"]


def test_build_rechecks_a_heavy_step_that_omits_an_explicit_parallel_action():
    primary = MockAgent("alpha", ["alpha.plan"])
    primary.manifest.capabilities[0].heavy = True
    agents = [primary, MockAgent("beta", ["beta.execute"])]
    replies = iter([
        '{"complexity":"simple","goal":"执行甲任务","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
        '{"complexity":"simple","goal":"执行甲任务并完成乙动作","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}},'
        '{"id":"s2","capability_ref":"cap_0002","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
    ])
    prompts = []

    async def mock_llm(messages):
        prompts.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "前往甲处，沿途完成乙动作", WorkingSet(catalog=agents), PlanContext()))

    assert len(prompts) == 2
    assert [step.intent for step in plan.steps] == ["alpha.plan", "beta.execute"]
    assert "heavy" in prompts[1]


def test_build_does_not_recheck_attributive_yantu_single_action():
    primary = MockAgent("alpha", ["alpha.search"])
    primary.manifest.capabilities[0].heavy = True
    agents = [primary, MockAgent("beta", ["beta.execute"])]
    replies = iter([
        '{"complexity":"simple","goal":"查找沿途的加油站","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
        '{"complexity":"simple","goal":"查找沿途的加油站","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}},'
        '{"id":"s2","capability_ref":"cap_0002","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
    ])
    prompts = []

    async def mock_llm(messages):
        prompts.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "查找沿途的加油站", WorkingSet(catalog=agents), PlanContext()))

    assert len(prompts) == 1
    assert [step.intent for step in plan.steps] == ["alpha.search"]


def test_build_rechecks_an_unpunctuated_enroute_stop_that_dropped_the_trip():
    """「导航去X路上找个Y」既没连接词也没逗号，同样是两个动作。

    实测（MiniMax 主模型 `ki.navigation-with-stop.hit`，10 样本 6/10）：失败的 4 次
    全是同一形状——只出 `charging.find`，**用户明说的「导航去公司」整个丢了**。
    `沿途` 那一支要求前面有标点，这句一个标点都没有，于是单步计划从不被复核。
    """
    primary = MockAgent("alpha", ["alpha.find"])
    primary.manifest.capabilities[0].heavy = True
    agents = [primary, MockAgent("beta", ["beta.navigate"])]
    replies = iter([
        '{"complexity":"simple","goal":"沿路找一个充电站","steps":['
        '{"id":"s1","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
        '{"complexity":"simple","goal":"导航到目的地并沿路找充电站","steps":['
        '{"id":"s1","capability_ref":"cap_0002","slots":{},'
        '"depends_on":[],"slot_refs":{}},'
        '{"id":"s2","capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
    ])
    prompts = []

    async def mock_llm(messages):
        prompts.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "导航去公司路上找个充电站", WorkingSet(catalog=agents), PlanContext()))

    assert len(prompts) == 2
    assert [step.intent for step in plan.steps] == ["beta.navigate", "alpha.find"]


@pytest.mark.parametrize("text", [
    "路上堵不堵",              # 「路上」只是话题，没有第二个动作
    "查一下路上的天气",         # 定语用法，找/搜 在连接词**之前**
    "顺路的加油站贵不贵",       # 同上，属性提问
])
def test_build_does_not_recheck_enroute_words_without_a_second_action(text):
    """收窄面的另一半：**要求后面跟检索动词**，否则「路上」这个词到处都是。

    多复核一次的代价只是一次 LLM 调用，但它会把单动作句也拖进复核——
    「每加一个默认值，先问『没有证据』和『证据为否』会不会被压成同一个数」。
    """
    agent = MockAgent("alpha", ["alpha.search"])
    agent.manifest.capabilities[0].heavy = True
    prompts = []

    async def mock_llm(messages):
        prompts.append(messages[1]["content"])
        return ('{"complexity":"simple","goal":"回答问题","steps":['
                '{"id":"s1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        text, WorkingSet(catalog=[agent]), PlanContext()))

    assert len(prompts) == 1, f"{text!r} 不该触发第二次复核"


def test_build_recheck_can_keep_one_heavy_step_when_its_contract_owns_sequence():
    agent = MockAgent("alpha", ["alpha.plan"])
    agent.manifest.capabilities[0].heavy = True
    reply = (
        '{"complexity":"simple","goal":"先收集材料，再生成同一份报告",'
        '"steps":[{"id":"s1","capability_ref":"cap_0001",'
        '"slots":{},"depends_on":[],"slot_refs":{}}]}'
    )
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "先收集材料，再生成同一份报告",
        WorkingSet(catalog=[agent]), PlanContext()))

    assert calls == 2
    assert [step.intent for step in plan.steps] == ["alpha.plan"]


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
        return ('{"steps":[{"id":"s1","capability_ref":"cap_9999","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

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
            '"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{"temp":"24"},"depends_on":[],"slot_refs":{}}]}'
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
            '"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}'
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
            '"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{"temperature":"24"},"depends_on":[],"slot_refs":{}}]}'
        )

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "set temperature", WorkingSet(catalog=agents), PlanContext(),
        granted_permissions=["vehicle.control"],
    ))

    assert len(plan.steps) == 1
    assert plan.steps[0].required_permissions == ["vehicle.control.hvac"]


def test_ensure_research_step_routes_deep_research():
    """弱 LLM 把『深入调研 X』误路由成 info.search → 确定性兜底纠偏到 research.run。"""
    agents = [MockAgent("deep-research", ["research.run"]),
              MockAgent("info", ["info.search"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0002",'
                '"slots":{"query":"固态电池"},"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "深入调研一下固态电池现状", WorkingSet(catalog=agents), PlanContext()))

    assert len(plan.steps) == 1
    assert plan.steps[0].intent == "research.run"
    assert plan.steps[0].agent_id == "deep-research"


def test_plain_search_not_hijacked_by_research_net():
    """普通『搜一下 X』不被深调研兜底劫持，仍走 info.search 单轮快查。"""
    agents = [MockAgent("deep-research", ["research.run"]),
              MockAgent("info", ["info.search"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0002",'
                '"slots":{"query":"固态电池"},"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "搜一下固态电池", WorkingSet(catalog=agents), PlanContext()))

    assert all(s.intent != "research.run" for s in plan.steps)
    assert plan.steps[0].intent == "info.search"


def test_ensure_research_followup_routes_deepen():
    """『展开第2点』等深挖追问也路由 research.run（Agent 取上次报告对应节深挖）。"""
    agents = [MockAgent("deep-research", ["research.run"]),
              MockAgent("info", ["info.search"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0002",'
                '"slots":{"query":"x"},"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    # **报告章节量词**（第N点/节/部、这部分）才路由 research.run
    for text in ("展开第2点", "再深入第二节", "这部分详细讲讲"):
        plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
            text, WorkingSet(catalog=agents), PlanContext()))
        assert any(s.intent == "research.run" for s in plan.steps), text

    # 2026-08-03 泓舟裁定：**「条/则」是列表项量词，不是报告章节量词**。新闻列表后的
    # 「第二条详细讲讲」要的是就地展开（秒级），不是开一张分钟级异步调研单。
    # 而 guard 是纯文本正则、看不到焦点——同一句话在报告场景与新闻场景**逐字相同**，
    # 一条分不开两个相反意图的 replace hint 至少一半时间是错的。故按量词收窄。
    for text in ("详细讲讲第2条", "这条新闻详细讲讲", "那条详细说说"):
        plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
            text, WorkingSet(catalog=agents), PlanContext()))
        assert not any(s.intent == "research.run" for s in plan.steps), text


def test_replan_returns_done_or_a_validated_next_batch():
    agents = [MockAgent("navigation", ["navigation.search_poi"])]
    replies = iter([
        '{"done":false,"steps":[{"id":"r1","capability_ref":"cap_0001",'
        '"slots":{"keyword":"次近充电站"},"depends_on":[],"slot_refs":{}}]}',
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


def test_replan_retries_once_when_first_answer_only_repeats_completed_intent():
    agents = [MockAgent("info", ["info.weather"]),
              MockAgent("reminder", ["reminder.create"])]
    replies = iter([
        '{"done":false,"steps":[{"id":"r1","capability_ref":"cap_0001",'
        '"slots":{},"depends_on":[],"slot_refs":{}}]}',
        '{"done":false,"steps":[{"id":"r2","capability_ref":"cap_0002",'
        '"slots":{"title":"带伞"},"depends_on":[],"slot_refs":{}}]}',
    ])
    messages_seen = []

    async def mock_llm(messages):
        messages_seen.append(messages)
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "明天下雨就提醒带伞",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather",
          "data": {"condition": "小雨"}}],
        agents, PlanContext(),
    ))

    assert len(messages_seen) == 2
    assert [step.intent for step in decision.steps] == ["reminder.create"]
    assert "info.weather" in messages_seen[1][1]["content"]
    assert "已经完成" in messages_seen[1][1]["content"]
    assert "只有观察明确证明条件不成立" in messages_seen[1][1]["content"]
    first_user = messages_seen[0][1]["content"]
    assert (first_user.rindex('"capabilities"')
            < first_user.rindex("最近观察：")
            < first_user.rindex("目标："))


def test_replan_drops_completed_repeat_when_answer_also_has_remaining_step():
    agents = [MockAgent("info", ["info.weather"]),
              MockAgent("reminder", ["reminder.create"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return ('{"done":false,"steps":['
                '{"id":"r1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}},'
                '{"id":"r2","capability_ref":"cap_0002","slots":{},'
                '"depends_on":["r1"],'
                '"slot_refs":{"weather":"r1.data.condition"}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "明天下雨就提醒带伞",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(),
    ))

    assert calls == 1
    assert [step.intent for step in decision.steps] == ["reminder.create"]
    assert decision.steps[0].depends_on == ["s1"]
    assert decision.steps[0].slot_refs == {"weather": "s1.data.condition"}


def test_replan_allows_explicit_same_intent_retry_signal():
    agents = [MockAgent("navigation", ["navigation.search_poi"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return ('{"done":false,"steps":[{"id":"r1",'
                '"capability_ref":"cap_0001","slots":{"keyword":"次近"},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "找到可用充电站",
        [{"step_id": "s1", "status": "ok", "intent": "navigation.search_poi",
          "retry_same_intent": True, "data": {"available": False}}],
        agents, PlanContext(),
    ))

    assert calls == 1
    assert [step.intent for step in decision.steps] == ["navigation.search_poi"]


def test_replan_fails_closed_when_retry_still_only_repeats_completed_intent():
    agents = [MockAgent("info", ["info.weather"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return ('{"done":false,"steps":[{"id":"r1",'
                '"capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "查询天气后按结果继续",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(),
    ))

    assert calls == 2
    assert decision.done is True
    assert decision.steps == []


def test_replan_keeps_ref_rendered_knowledge_next_to_observation_and_goal(monkeypatch):
    agents = [MockAgent("info", ["info.weather"])]
    users = []

    monkeypatch.setattr(
        "orchestrator.cloud.planning._skills.render_for_names",
        lambda *_args, **_kwargs: "skill-marker",
    )
    monkeypatch.setattr(
        "orchestrator.cloud.planning._exemplars.render_for_names",
        lambda *_args, **_kwargs: "exemplar-marker",
    )

    async def mock_llm(messages):
        users.append(messages[1]["content"])
        return '{"done":true,"steps":[]}'

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "按天气结果继续",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(),
        working_set=WorkingSet(history=[{"role": "user", "text": "history-marker"}]),
        skill_names=["full:test"], exemplar_names=["full:test#1"],
    ))

    assert decision.done is True and len(users) == 1
    user = users[0]
    positions = [user.index(marker) for marker in (
        "history-marker", '"capabilities"', "skill-marker", "exemplar-marker",
        "最近观察：", "目标：",
    )]
    assert positions == sorted(positions)


def test_replan_rechecks_the_first_done_decision_for_a_conditional_goal():
    agents = [MockAgent("reminder", ["reminder.create"])]
    replies = iter([
        '{"done":true,"steps":[]}',
        '{"done":false,"steps":[{"id":"r1","capability_ref":"cap_0001",'
        '"slots":{"title":"带伞"},"depends_on":[],"slot_refs":{}}]}',
    ])
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "若下雨则提醒带伞",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather",
          "data": {"condition": "小雨"}}],
        agents, PlanContext(),
    ))

    assert calls == 2
    assert [step.intent for step in decision.steps] == ["reminder.create"]


def test_replan_retries_conditional_done_false_with_empty_steps():
    agents = [MockAgent("charging", ["charging.find"])]
    replies = iter([
        '{"done":false,"steps":[]}',
        '{"done":false,"steps":[{"id":"r1",'
        '"capability_ref":"cap_0001","slots":{},'
        '"depends_on":[],"slot_refs":{}}]}',
    ])
    users = []

    async def mock_llm(messages):
        users.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "先看续航，如果不足就找快充",
        [{"step_id": "s1", "status": "ok", "intent": "charging.status",
          "data": {"range_km": 30}}],
        agents, PlanContext(),
    ))

    assert len(users) == 2
    assert decision.done is False
    assert [step.intent for step in decision.steps] == ["charging.find"]
    assert "done=false 却没有后续 steps" in users[1]


def test_replan_rechecks_an_empty_followup_when_the_first_plan_declared_adaptive():
    """`complexity=adaptive` 是计划自己的声明，replan 收场就与它自相矛盾。

    2026-08-10 实测（findings §22）：`cp.adaptive.weather-outing` 首轮正确排出
    `info.weather` 并自报 adaptive，拿到「中雨」之后 replan 返回 `done=false, steps=[]`
    ——一步都不补。既有的一次性纠偏只认目标文本里的条件词，而「今天的天气适合去哪玩」
    的 goal 里一个条件词都没有，于是这一路从来没被纠偏过。
    """
    agents = [MockAgent("nearby", ["nearby.search"])]
    replies = iter([
        '{"done":false,"steps":[]}',
        '{"done":false,"steps":[{"id":"r1","capability_ref":"cap_0001",'
        '"slots":{"category":"室内"},"depends_on":[],"slot_refs":{}}]}',
    ])
    users = []

    async def mock_llm(messages):
        users.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "根据今天的天气推荐游玩地点",          # 无条件词——旧判据在这里一定不触发
        [{"step_id": "s1", "status": "ok", "intent": "info.weather",
          "data": {"condition": "中雨"}}],
        agents, PlanContext(), adaptive=True,
    ))

    assert len(users) == 2, "adaptive 首轮声明过第二阶段，空 followup 必须重问一次"
    assert [step.intent for step in decision.steps] == ["nearby.search"]
    # 反馈话术必须对得上来源：adaptive 那一路不该收到「请比较条件前件」。
    assert "complexity=adaptive" in users[1]
    assert "条件前件" not in users[1]


def test_replan_rechecks_a_done_verdict_when_the_first_plan_declared_adaptive():
    agents = [MockAgent("nearby", ["nearby.search"])]
    replies = iter([
        '{"done":true,"steps":[]}',
        '{"done":false,"steps":[{"id":"r1","capability_ref":"cap_0001",'
        '"slots":{},"depends_on":[],"slot_refs":{}}]}',
    ])
    users = []

    async def mock_llm(messages):
        users.append(messages[1]["content"])
        return next(replies)

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "根据今天的天气推荐游玩地点",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(), adaptive=True,
    ))

    assert len(users) == 2
    assert [step.intent for step in decision.steps] == ["nearby.search"]


def test_replan_still_accepts_done_after_one_adaptive_recheck():
    """纠偏只买一次重问，**不制造「必须产出步骤」的压力**。

    首轮 adaptive 但结果确实答完了目标（或没有可承接能力）时，第二次仍说 done 就照收。
    否则这条守卫会把「该收场」变成「硬凑一步」，那比原缺陷更贵。
    """
    agents = [MockAgent("nearby", ["nearby.search"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return '{"done":true,"steps":[]}'

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "根据今天的天气推荐游玩地点",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(), adaptive=True,
    ))

    assert calls == 2
    assert decision.done is True
    assert decision.steps == []


def test_replan_without_adaptive_flag_keeps_the_old_single_shot_behaviour():
    """守卫必须由**初规划的声明**打开，不是对所有 replan 都多问一次。

    这条是上面三条的反向构造：同样的目标与观察，不传 `adaptive` 就只调一次模型。
    """
    agents = [MockAgent("nearby", ["nearby.search"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return '{"done":false,"steps":[]}'

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "根据今天的天气推荐游玩地点",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(),
    ))

    assert calls == 1
    assert decision.done is True


def test_replan_accepts_first_done_for_a_nonconditional_goal():
    agents = [MockAgent("info", ["info.weather"])]
    calls = 0

    async def mock_llm(_messages):
        nonlocal calls
        calls += 1
        return '{"done":true,"steps":[]}'

    async def mock_resolve(query, top_k=1):
        return []

    decision = asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        "查询明天天气",
        [{"step_id": "s1", "status": "ok", "intent": "info.weather"}],
        agents, PlanContext(),
    ))

    assert calls == 1
    assert decision.done is True


def test_json_and_replan_prompts_treat_the_live_catalog_as_the_only_allowlist():
    """初规划与再规划都只能消费本轮动态 catalog，不能靠模型常识补能力。"""
    initial = _planner_system()
    for clause in ("capability_ref", "唯一调用权",
                   "不得编造", "不得替换", "缺席"):
        assert clause in initial
    assert '{"addressed":true,"steps":[]}' in initial
    assert initial.rindex("== 本轮动态能力白名单 ==") > initial.rindex("== 通用规则 ==")

    for clause in ("capability_ref", "唯一调用权",
                   "不得编造", "不得替换", "缺席"):
        assert clause in _REPLAN_SYSTEM


def test_user_message_reasserts_the_live_catalog_after_soft_assets_and_context():
    """知识/范例/历史可能提到已下线能力，最终 catalog 必须贴着用户原话重新封口。"""
    agents = [MockAgent("navigation", ["navigation.search_poi"])]
    working_set = WorkingSet(
        catalog=agents,
        history=[{"role": "user", "text": "CONTEXT_MISSING_INTENT"}],
    )

    catalog = _assemble_capability_catalog(agents)
    message = PlanBuilder._planner_user_msg(
        "USER_UTTERANCE", catalog, working_set,
        skills_block="SKILL_MISSING_INTENT",
        exemplars_block="EXEMPLAR_MISSING_INTENT",
    )

    catalog_at = message.rindex(catalog.semantic_mapping_text)
    utterance_at = message.rindex("用户说: USER_UTTERANCE")
    for soft_evidence in ("SKILL_MISSING_INTENT", "EXEMPLAR_MISSING_INTENT",
                          "CONTEXT_MISSING_INTENT"):
        assert message.index(soft_evidence) < catalog_at
    assert catalog_at < utterance_at


# ── 多日出行确定性兜底：弱 LLM 漏掉行程规划时补 trip.plan 步 ──

def _trip_agents():
    return [
        MockAgent("info-agent", ["info.weather"]),
        MockAgent("charging-planner", ["charging.plan"]),
        MockAgent("trip-planner", ["trip.plan"]),
    ]



def test_does_not_inject_trip_when_llm_already_planned_it():
    """LLM 自己出了 trip.plan，不重复注入。"""
    agents = _trip_agents()

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0003",'
                '"slots":{"destination":"杭州","days":"2"},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("去杭州玩两天", WorkingSet(catalog=agents), PlanContext()))
    assert len([s for s in plan.steps if s.intent == "trip.plan"]) == 1


def test_does_not_inject_trip_for_plain_navigation():
    """『导航去北京南站』是通勤/单点导航，不是多日出行，不得注入 trip.plan。"""
    agents = _trip_agents() + [MockAgent("navigation", ["navigation.navigate"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0003",'
                '"slots":{"destination":"北京南站"},"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("导航去北京南站", WorkingSet(catalog=agents), PlanContext()))
    assert [s for s in plan.steps if s.intent == "trip.plan"] == []




def test_modify_pattern_keeps_llm_trip_modify():
    """LLM 已正确路由 trip.modify 时保持不变，不重复/不替换。"""
    agents = [MockAgent("trip-planner", ["trip.plan", "trip.modify"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001",'
                '"slots":{"modification":"第二天换成宋城"},'
                '"depends_on":[],"slot_refs":{}}],'
                '"goal":"执行第二天换成宋城的行程修改"}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("第二天换成宋城", WorkingSet(catalog=agents), PlanContext()))
    assert [s.intent for s in plan.steps] == ["trip.modify"]


def test_fake_agent_gets_deterministic_routing_from_manifest_only():
    """铁律契约（DoD#2）：一个全新 Agent 仅靠 manifest.route_hints 就能获得确定性路由，
    编排核心零改动、planning.py 不含该 Agent 任何字面量。这固化「新增 Agent 不改编排核心」。"""
    from cockpit.agent.v1 import agent_pb2
    widget = MockAgent("widget-agent", ["widget.cast"])
    widget.manifest.route_hints = [agent_pb2.RouteHint(
        pattern="施展魔法|念咒语", intent="widget.cast", policy="replace",
        priority=50, slots={"spell": "$text"})]
    agents = [widget, MockAgent("chitchat", ["chitchat.talk"])]

    async def mock_llm(messages):     # 弱 LLM 误判成闲聊
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "帮我施展魔法", WorkingSet(catalog=agents), PlanContext()))
    assert [s.intent for s in plan.steps] == ["widget.cast"]
    assert plan.steps[0].agent_id == "widget-agent"
    assert plan.steps[0].slots["spell"] == "帮我施展魔法"


def test_heavy_capability_marks_step_heavy_and_complex():
    """P3：capability.heavy=true → step.heavy → is_complex（progress 不再硬编码 HEAVY_INTENTS）。"""
    from orchestrator.cloud.progress import is_complex
    agent = MockAgent("deep-research", ["research.run"])
    agent.manifest.capabilities[0].heavy = True     # 模拟 manifest 声明 heavy

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "固态电池怎么样", WorkingSet(catalog=[agent]), PlanContext()))
    assert plan.steps[0].heavy is True
    assert is_complex(plan) is True


def test_light_capability_step_not_heavy():
    """轻查询能力（heavy 缺省 False）→ step.heavy False，单步不判复杂。"""
    from orchestrator.cloud.progress import is_complex
    agent = MockAgent("info", ["info.weather"])   # cap.heavy=False（MockAgent 默认）

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(mock_llm, mock_resolve).build(
        "今天天气", WorkingSet(catalog=[agent]), PlanContext()))
    assert plan.steps[0].heavy is False
    assert is_complex(plan) is False


def test_does_not_inject_trip_when_planner_unavailable():
    """trip-planner 没注册（无权限/未上线）时不注入，避免产出 Unknown agent 计划。"""
    agents = [MockAgent("info-agent", ["info.weather"])]

    async def mock_llm(messages):
        return ('{"steps":[{"id":"s1","capability_ref":"cap_0001","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(mock_llm, mock_resolve)
    plan = asyncio.run(builder.build("周末去杭州两天带老人看看天气", WorkingSet(catalog=agents), PlanContext()))
    assert [s for s in plan.steps if s.intent == "trip.plan"] == []

# ── M5 P2 退役记录（2026-07-29）────────────────────────────────────────────────

def test_retired_hints_are_gone_by_design():
    """上方原有的「hint 应当补步/改写」断言已删除——它们描述的机制不存在了。

    退役的 hint（跨 minimax:MiniMax-M3 与 deepseek:deepseek-v4-flash 两档、全部命中语料
    全覆盖、各 ×2 轮，摘掉后仍全部落对）：trip-planner#0/#2/#3/#4；
    2026-07-30 追加 **nearby 的两条**（`nearby.search`，13+2 句全覆盖）——它们退役的前提是
    先把**错标的金标**修掉：判定语料里「找个评分高的川菜馆」被 navigation 声称、
    「找个充电站」被 nearby hint 从 charging 手里抢走，`ineffective`（带着 hint 也答错）
    读出来的其实是**金标自相矛盾**，不是规则失效。详见 skills/exemplars/boundaries.yaml。

    **回归保护去哪了，以及它变弱了多少**：命中句已改端到端口径迁入
    `test/eval_corpus/mode_routing_cases.yaml`，由 `eval_mode_routing --live` 覆盖。
    但那是 **live 车道、不在 CI**——原来这些断言被刻意写成阻断 pytest，理由白纸黑字：
    「eval_route_hints 语料在 continue-on-error 观测步，语料回归 CI 不红」。
    所以退役把这部分**召回保护从「CI 阻断」降级成了「人工触发」**。
    这是退役的真实代价，不是可以忽略的细节；要补回来得让 live 车道进 CI（真栈 + LLM，
    成本另议）——已作为余项记在 RFC §5-P2。

    本测试守住两件仍可离线验证的事：①这些 intent 作为**能力**依然存在（域没有消失，
    只是不再有正则替模型做决定）；②没人在没有新证据的情况下把 hint 悄悄加回来。
    """
    import glob as _glob
    import pathlib as _pl

    import yaml as _yaml
    root = _pl.Path(__file__).resolve().parents[3]
    retired = ['trip.plan', 'trip.modify', 'trip.navigate', 'trip.status',
               'nearby.search']
    caps, hints = set(), {}
    for p in sorted(_glob.glob(str(root / "agents" / "*" / "manifest.yaml"))):
        d = _yaml.safe_load(open(p, encoding="utf-8")) or {}
        caps |= {str(c.get("intent")) for c in (d.get("capabilities") or [])}
        for h in (d.get("route_hints") or []):
            hints.setdefault(str(h.get("intent")), []).append(d.get("agent_id"))
    for i in retired:
        assert i in caps or i.startswith("shop."), f"{i} 的能力也消失了——退役只该去掉规则"
        assert i not in hints, (
            f"{i} 的 route_hint 又回来了（{hints.get(i)}）——恢复规则需要新证据："
            f"双臂裸跑显示模型自己做不到，且要写进 manifest 注释")


def test_missing_complexity_on_a_schemaless_channel_is_recorded_not_silently_simple():
    """`wire.get("complexity", "simple")` 把两件事压成了一个值。

    `toolcall` 通道的 schema 把 complexity 列为 required，模型必须给；
    `toolcall_salvage`（模型无视工具直接吐文本）与 `toolcall_fallback` 两条通道
    **没有任何强制**。于是「通道没给」与「模型判了 simple」在 plan 上逐字相同——
    findings §23：`cp.adaptive.weather-outing` 的失败正好全落在 salvage 通道上，
    而没有这个标记就分不清该修模型判断还是该修通道契约。
    """
    builder = PlanBuilder(lambda _m: "", lambda q, top_k=1: [])
    agents = [MockAgent("info", ["info.weather"])]
    catalog = _assemble_capability_catalog(agents)
    ref = next(iter(catalog.ref_to_pair))
    step = {"id": "s1", "capability_ref": ref, "slots": {},
            "depends_on": [], "slot_refs": {}}

    absent = builder._parse_with_context(
        {"goal": "查天气", "addressed": True, "steps": [step]},
        catalog, "今天的天气适合去哪玩", stage="build", attempt=0,
        wire_mode="toolcall_salvage")
    stated = builder._parse_with_context(
        {"complexity": "simple", "goal": "查天气", "addressed": True,
         "steps": [step]},
        catalog, "今天天气怎么样", stage="build", attempt=0,
        wire_mode="toolcall")

    # 行为不变：两者的 complexity 仍然都是 simple（这条改动只加留痕，不改分诊）。
    assert absent.complexity == "simple" and stated.complexity == "simple"
    # 但它们**不再是同一个观测**。
    assert absent.complexity_declared is False
    assert stated.complexity_declared is True


def test_invalid_complexity_value_counts_as_undeclared():
    builder = PlanBuilder(lambda _m: "", lambda q, top_k=1: [])
    agents = [MockAgent("info", ["info.weather"])]
    catalog = _assemble_capability_catalog(agents)
    ref = next(iter(catalog.ref_to_pair))

    plan = builder._parse_with_context(
        {"complexity": "复杂", "goal": "g", "addressed": True,
         "steps": [{"id": "s1", "capability_ref": ref, "slots": {},
                    "depends_on": [], "slot_refs": {}}]},
        catalog, "t", stage="build", attempt=0, wire_mode="toolcall_salvage")

    assert plan.complexity == "simple"
    assert plan.complexity_declared is False

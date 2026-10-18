"""route hint 声明 `complexity: adaptive`（200×5 后续批 §21.5，核心旅程 A1-5「今天的天气适合去哪玩」）。

hint 以 replace 命中、换上它那一步——或模型自己只派了这一种 intent（「只报天气」那种）——时，整份计划标 adaptive、goal 保留原话、
清掉澄清卡、标受话：那一步是观察步，后面的交给 T2 按观察结果再规划。计划里还有别的步时不动；没声明的 hint 行为不变。
"""
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.models import Plan, Step
from orchestrator.cloud.route_hints import RouteHintEngine

_TEXT = "今天的天气适合去哪玩"


def _validate(raws, agent_map):
    return [Step(id=r["id"], agent_id=r["agent_id"], intent=r["intent"], slots=dict(r["slots"])) for r in raws]


def _agents(complexity="adaptive"):
    hint = agent_pb2.RouteHint(pattern=r"(天气|气温)[^。！？!?]{0,10}?(去|出去)?(哪|哪里|哪儿|什么地方)(玩|逛|走走|转转|溜达|耍)",
                               intent="info.weather", policy="replace", priority=58, complexity=complexity)
    return {"info": SimpleNamespace(manifest=SimpleNamespace(agent_id="info", route_hints=[hint]))}


def _plan(*intents, **fields):
    return Plan(steps=[Step(id=f"s{i}", agent_id="x", intent=it) for i, it in enumerate(intents, 1)], **fields)


def _apply(plan, agents=None):
    return RouteHintEngine(_validate).apply(plan, _TEXT, agents or _agents())


def test_a_clarification_is_replaced_by_an_adaptive_weather_observation():
    plan = _plan(clarify={"question": "你想去哪一类地方？", "options": []}, addressed=True)
    assert _apply(plan)
    assert [s.intent for s in plan.steps] == ["info.weather"]
    assert plan.complexity == "adaptive" and plan.goal == _TEXT and plan.clarify is None and plan.addressed


def test_a_plan_that_recommends_without_the_weather_is_replaced():
    """只推荐、不看天气就是把雨天的公园推给用户（旅程注释）：换成先查天气。"""
    plan = _plan("nearby.search")
    _apply(plan)
    assert [s.intent for s in plan.steps] == ["info.weather"] and plan.complexity == "adaptive"


def test_a_weather_only_plan_is_upgraded_to_adaptive():
    """「只报天气」正是原 badcase：模型自己派了天气这一步，同样升成 adaptive，推荐交给 T2。"""
    plan = _plan("info.weather", goal="查今天天气")
    _apply(plan)
    assert [s.intent for s in plan.steps] == ["info.weather"]
    assert plan.complexity == "adaptive" and plan.goal == _TEXT


def test_a_plan_that_already_recommends_on_the_weather_is_left_alone():
    plan = _plan("info.weather", "nearby.search", goal="先查天气再推荐")
    _apply(plan)
    assert [s.intent for s in plan.steps] == ["info.weather", "nearby.search"]
    assert plan.complexity == "simple" and plan.goal == "先查天气再推荐"


def test_a_hint_without_complexity_does_not_touch_the_plan_shape():
    plan = _plan(clarify={"question": "?", "options": []})
    _apply(plan, _agents(complexity=""))
    assert [s.intent for s in plan.steps] == ["info.weather"]
    assert plan.complexity == "simple" and plan.goal == ""


def test_the_planner_turns_a_first_turn_clarification_into_an_adaptive_weather_plan():
    """整链（真实 info manifest 的 hint，经 `PlanBuilder.build`）：模型首轮交澄清卡 ⇒ 计划是天气观察步、adaptive、goal 原话，
    不出澄清卡（A1-5 的断言里反问 = 断链）。"""
    import asyncio
    import json

    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    from orchestrator.cloud.planning import PlanBuilder
    from tests.test_planning import MockAgent

    from pathlib import Path

    from agents._sdk.manifest import load_manifest

    info = MockAgent("info", ["info.weather"])
    info.manifest.route_hints = list(load_manifest(
        str(Path(__file__).resolve().parents[3] / "agents" / "info" / "manifest.yaml")).route_hints)
    agents = [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)), info,
              MockAgent("nearby", ["nearby.search"])]
    clarify = json.dumps({"addressed": True, "steps": [], "clarify": {"question": "你想去哪一类地方？", "options": [
        {"label": "公园", "send_text": "附近的公园"}, {"label": "商场", "send_text": "附近的商场"}]}}, ensure_ascii=False)

    async def llm(messages):
        return clarify

    async def resolve(query, top_k=1):
        return []

    plan = asyncio.run(PlanBuilder(llm_fn=llm, registry_fn=resolve).build(
        _TEXT, WorkingSet(catalog=agents), PlanContext(session_id="t")))
    assert [s.intent for s in plan.steps] == ["info.weather"]
    assert plan.complexity == "adaptive" and plan.goal == _TEXT and plan.clarify is None

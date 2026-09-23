"""能力编号笔误：槽位全在所引能力的契约之外、恰好属于编号只差一个字符的另一个能力 ⇒ 归位（评审三轮追加批 F，F-1）。

真栈 `f9bec423` RS28 第三趟：「推荐三部适合全家看的电影」planner 交出 `cap_0107` + `{depth: deep}`——`depth` 是
chitchat（`cap_0007`）的槽，`cap_0107` 是 `luckin.order`。wire 校验只问 ref 在不在本请求映射里，于是照样解析成
瑞幸下单；executor 记了一条「槽位 ['depth'] 不在能力契约里」就派发了，用户听到「想点哪一款瑞幸饮品？」。

判据与既有「intent 唯一归属时归位」同一族：证据必须唯一——槽位**一个都不属于**所写能力、**恰好一个**别的能力的
契约装得下它们全部、两者编号**只差一个字符**、那个能力**只回答不执行**（`response_only`，归位只许朝「回答」推）。
任何一条不成立都一个字不动（今天的行为）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _capability_pairs

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _with_slots(agent, slots_by_intent):
    for cap in agent.manifest.capabilities:
        cap.slots = list(slots_by_intent.get(cap.intent, []))
    return agent


def _live_catalog(second_depth_owner: bool = False):
    """复刻真栈编号：chitchat 排在 cap_0007、luckin.order 排在 cap_0107。"""
    filler_a = MockAgent("a-filler", [f"a.f{i:02d}" for i in range(1, 7)])          # cap_0001–0006
    chitchat = _with_slots(MockAgent("chitchat", ["chitchat.talk"],
                                     response_only=("chitchat.talk",)),
                           {"chitchat.talk": ["depth"]})                             # cap_0007
    filler_d = MockAgent("d-filler", [f"d.f{i:03d}" for i in range(1, 100)])        # cap_0008–0106
    if second_depth_owner:
        _with_slots(filler_d, {"d.f099": ["depth"]})                                  # cap_0106 也认 depth
    luckin = _with_slots(MockAgent("mcp-bridge", ["luckin.order"]),
                         {"luckin.order": ["item", "size"]})                          # cap_0107
    return [filler_a, chitchat, filler_d, luckin]


def _ref(agents, intent):
    for index, (_aid, name) in enumerate(_capability_pairs(agents), 1):
        if name == intent:
            return f"cap_{index:04d}"
    raise AssertionError(intent)


def _reply(ref, slots):
    return json.dumps({"addressed": True, "steps": [
        {"id": "s1", "capability_ref": ref, "slots": slots,
         "depends_on": [], "slot_refs": {}}]}, ensure_ascii=False)


def _build(agents, reply, text="推荐三部适合全家看的电影"):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))


def test_the_catalog_reproduces_the_live_numbering():
    agents = _live_catalog()
    assert _ref(agents, "chitchat.talk") == "cap_0007"
    assert _ref(agents, "luckin.order") == "cap_0107"


def test_live_shape_is_rehomed_to_the_capability_that_owns_the_slots():
    agents = _live_catalog()
    plan = _build(agents, _reply("cap_0107", {"depth": "deep"}))
    assert [(s.agent_id, s.intent) for s in plan.steps] == [("chitchat", "chitchat.talk")]
    assert plan.steps[0].slots.get("depth") == "deep"
    assert plan.ref_rehomed == ["cap_0107>cap_0007"]


@pytest.mark.parametrize("slots", [
    {"item": "拿铁"},                       # 槽位属于所写能力：它就是对的
    {"depth": "deep", "item": "拿铁"},      # 有一个对得上就不算笔误
    {},                                     # 没带槽：没有证据
])
def test_a_step_with_any_fitting_slot_or_no_slot_is_untouched(slots):
    agents = _live_catalog()
    plan = _build(agents, _reply("cap_0107", slots))
    assert [s.intent for s in plan.steps] == ["luckin.order"]
    assert plan.ref_rehomed == []


def test_two_candidates_one_character_away_are_not_guessed_between():
    agents = _live_catalog(second_depth_owner=True)     # cap_0007 与 cap_0106 都认 depth，都与 cap_0107 差一位
    plan = _build(agents, _reply("cap_0107", {"depth": "deep"}))
    assert [s.intent for s in plan.steps] == ["luckin.order"]
    assert plan.ref_rehomed == []


def test_an_owner_more_than_one_character_away_is_not_a_typo():
    chitchat = _with_slots(MockAgent("chitchat", ["chitchat.talk"],
                                     response_only=("chitchat.talk",)),
                           {"chitchat.talk": ["depth"]})                             # cap_0001
    filler = MockAgent("d-filler", [f"d.f{i:02d}" for i in range(1, 11)])          # cap_0002–0011
    luckin = _with_slots(MockAgent("mcp-bridge", ["luckin.order"]),
                         {"luckin.order": ["item", "size"]})                          # cap_0012
    agents = [chitchat, filler, luckin]
    assert _ref(agents, "luckin.order") == "cap_0012"
    plan = _build(agents, _reply("cap_0012", {"depth": "deep"}))
    assert [s.intent for s in plan.steps] == ["luckin.order"]
    assert plan.ref_rehomed == []


def test_a_step_is_never_rehomed_toward_a_capability_that_acts():
    """④ 只许朝「只回答不执行」的能力归位。第一版没有这一条，当场撞红 `test_engine_sibling_steps`：
    `nearby.search` 带着导航的 `destination` 槽、编号与 `navigation.navigate_to` 只差一位（相邻编号天然只差一位），
    被归位成真的开始导航——槽名是多家共用的词汇，朝写操作改派就是替用户按下按钮。"""
    navigation = _with_slots(MockAgent("navigation", ["navigation.navigate_to"]),
                             {"navigation.navigate_to": ["destination"]})             # cap_0001
    nearby = _with_slots(MockAgent("nearby", ["nearby.search"]),
                         {"nearby.search": ["keyword"]})                              # cap_0002
    agents = [navigation, nearby]
    plan = _build(agents, _reply("cap_0002", {"destination": "学校"}), text="顺便找家店")
    assert [s.intent for s in plan.steps] == ["nearby.search"]
    assert plan.ref_rehomed == []


def test_a_slot_the_written_capability_also_owns_is_not_a_typo():
    """① 所写能力自己认这个槽：它可能就是对的，不改（去掉 ① 这条会被归位到只回答的邻居）。"""
    chitchat = _with_slots(MockAgent("chitchat", ["chitchat.talk"],
                                     response_only=("chitchat.talk",)),
                           {"chitchat.talk": ["depth"]})                             # cap_0001
    research = _with_slots(MockAgent("research", ["research.run"]),
                           {"research.run": ["topic", "depth"]})                     # cap_0002
    agents = [chitchat, research]
    plan = _build(agents, _reply("cap_0002", {"depth": "deep"}), text="深入调研一下固态电池")
    assert [s.intent for s in plan.steps] == ["research.run"]
    assert plan.ref_rehomed == []


def test_the_rehome_is_visible_on_the_planning_span_and_the_talk_agent_is_what_runs(monkeypatch):
    """整机：归位要在 `cloud.planning` span 上看得见，真正被调用的是 chitchat、不是瑞幸下单。"""
    from types import SimpleNamespace

    from observability import events
    from orchestrator.cloud.aggregator import Aggregator
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.executor import DagExecutor
    from orchestrator.cloud.session import SessionStore

    spans = []

    class _Emitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((node, kwargs.get("attrs") or {}))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(events, "get_emitter", lambda service="cloud": _Emitter(), raising=False)

    class _Cap:
        def __init__(self, intent, slots, response_only=False):
            self.intent, self.slots, self.description = intent, slots, intent
            self.response_only = response_only

    def _agents():
        chitchat = SimpleNamespace(manifest=SimpleNamespace(
            agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
            requires_permissions=[], capabilities=[_Cap("chitchat.talk", ["depth"], True)],
        ), endpoint="stub:1")
        bridge = SimpleNamespace(manifest=SimpleNamespace(
            agent_id="mcp-bridge", trust_level="third_party", latency_budget_ms=2000,
            requires_permissions=[], capabilities=[_Cap("luckin.order", ["item", "size"])],
        ), endpoint="stub:2")
        return [chitchat, bridge]                                                     # cap_0001 / cap_0002

    plan_json = json.dumps({"addressed": True, "steps": [
        {"id": "s1", "capability_ref": "cap_0002", "slots": {"depth": "deep"},
         "depends_on": [], "slot_refs": {}}]})

    class _Resp:
        status, follow_up, actions, ui_card, data, missing_slots = 0, "", [], None, None, []

        def __init__(self, speech):
            self.speech = speech

    class _Spy:
        def __init__(self):
            self.calls = []

        async def call_agent(self, endpoint, intent, slots, ctx, meta):
            self.calls.append(intent)
            return _Resp("推荐《寻梦环游记》《飞屋环游记》《机器人总动员》。")

        async def llm(self, messages, **kwargs):
            return plan_json if "任务编排器" in messages[0]["content"] else "聚合话术"

        async def resolve(self, query="", intent="", top_k=1):
            return _agents()

        async def list_agents(self):
            return _agents()

    spy = _Spy()
    engine = PlannerEngine(
        clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent), aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""))
    req = SimpleNamespace(text="推荐三部适合全家看的电影", session_id="sess-f1", request_id="r1",
                          is_confirmation=False, operation_id="",
                          context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    async def collect():
        return [e async for e in engine.run(req)]

    asyncio.run(collect())
    planning = [attrs for node, attrs in spans if node == "cloud.planning"]
    assert planning and planning[0].get("ref_rehomed") == "cap_0002>cap_0001", planning
    assert spy.calls == ["chitchat.talk"], spy.calls

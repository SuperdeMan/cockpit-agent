"""带操作线索、点名了能力的指令不再被逼成澄清卡（200×5 后续批 §20.6，`named_directive_clarified`）。

固定语料：`d6b6b688` 复测 EN09 r3「导航去厦门火车站」首轮零步、goal 写「需要澄清：用户给的是单一名词对象」，随后
`clarify_goal_with_steps` 按裸对象族逼出「你希望我怎么处理『导航去厦门火车站』？」。
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
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog

from tests.test_planning import MockAgent

#: 首轮的真实形态：目标说要澄清、不给步骤（线上原话）
_MARKER = json.dumps({"addressed": True, "goal": "需要澄清：用户给的是单一名词对象，未指明要执行的动作。", "steps": []},
                     ensure_ascii=False)
_CLARIFY = json.dumps({"addressed": True, "steps": [], "clarify": {
    "question": "你希望我怎么处理？", "options": [{"label": "立即出发导航", "send_text": "请导航去厦门火车站"},
                                           {"label": "先看看路线", "send_text": "去厦门火车站要多久"}]}},
    ensure_ascii=False)
_NO_ACTION = json.dumps({"addressed": True, "steps": []}, ensure_ascii=False)

#: 能力描述取真实 manifest 的功能主干——点名判据读的就是它（`step_grounding.step_naming_score`）
_DESCRIPTIONS = {
    "chitchat.talk": "开放域闲聊",
    "charging.find": "查找附近充电站；按距离与可用桩数排序",
    "navigation.navigate_to": "导航到指定目的地；支持常用地点别名",
    "frunk.open": "打开前备箱",
    "hvac.on": "打开空调",
}


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    agents = [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
              MockAgent("navigation", ["navigation.navigate_to"]),
              MockAgent("charging-planner", ["charging.find"]),
              MockAgent("edge-vehicle", ["frunk.open", "hvac.on"], kind="edge_fast", deployment="edge")]
    for agent in agents:
        for cap in agent.manifest.capabilities:
            cap.description = _DESCRIPTIONS[cap.intent]
    return agents


def _build(text, replies):
    queue = list(replies)
    seen = []

    async def llm(messages):
        seen.append(messages[-1]["content"])
        return queue.pop(0) if queue else replies[-1]

    async def resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), PlanContext(session_id="test"))), seen


def _plan(agent_id, intent, text):
    ref = _assemble_capability_catalog(_agents()).pair_to_ref[(agent_id, intent)]
    return json.dumps({"complexity": "simple", "goal": text, "addressed": True, "steps": [
        {"id": "s1", "capability_ref": ref, "slots": {}, "depends_on": [], "slot_refs": {}}]}, ensure_ascii=False)


@pytest.mark.parametrize("first", [_MARKER, _CLARIFY])
def test_a_clarified_named_directive_is_replanned_without_options(first):
    """首轮判成澄清（目标标记或澄清卡）⇒ 校正后下一次只许给计划，不再被逼出「你希望我怎么处理」。"""
    plan, seen = _build("导航去厦门火车站", [first, _plan("navigation", "navigation.navigate_to", "导航去厦门火车站")])
    assert [s.intent for s in plan.steps] == ["navigation.navigate_to"] and plan.clarify is None
    assert "named_directive_clarified" in (plan.retry_policies or [])
    assert "clarify_goal_with_steps" not in (plan.retry_policies or [])
    assert "点名了要对谁做" in seen[-1] and "不要向用户反问" in seen[-1]


def test_a_vehicle_directive_named_by_its_object_is_replanned():
    plan, _ = _build("打开前备箱", [_CLARIFY, _plan("edge-vehicle", "frunk.open", "打开前备箱")])
    assert [s.intent for s in plan.steps] == ["frunk.open"] and plan.clarify is None
    assert "named_directive_clarified" in (plan.retry_policies or [])


@pytest.mark.parametrize("text", ["怎么调", "把那个关了", "打开它", "我只是在问原理，不要打开它"])
def test_directives_that_name_no_capability_still_clarify(text):
    """缺对象、指代（「它」「那个」）⇒ 点不出能力，澄清照旧。"""
    plan, _ = _build(text, [_CLARIFY, _CLARIFY])
    assert "named_directive_clarified" not in (plan.retry_policies or [])


def test_a_true_ambiguity_without_an_operation_cue_still_clarifies():
    """澄清评测语料（`test/eval_corpus/clarify_cases.yaml`）的真歧义：「找个充电的地方」点得出充电找站、也不只是对象名，
    可没说要做什么（周边发现还是沿途补电）——没有操作线索，照旧澄清。"""
    plan, _ = _build("找个充电的地方", [_CLARIFY, _CLARIFY])
    assert "named_directive_clarified" not in (plan.retry_policies or [])
    assert not plan.steps and plan.clarify is not None


def test_a_bare_object_without_an_operation_cue_still_clarifies():
    """裸对象（「空调」）点得出能力，但没有操作线索——正是裸对象族要澄清的那种。"""
    plan, _ = _build("空调", [_MARKER, _CLARIFY])
    assert "named_directive_clarified" not in (plan.retry_policies or [])
    assert not plan.steps and plan.clarify is not None


def test_only_the_first_attempt_is_redirected():
    """末轮不再判掉：首轮零步被催之后第二次才交澄清卡，留着这张带选项的卡，不换成「没听清」的失败话术。"""
    plan, _ = _build("导航去厦门火车站", [_NO_ACTION, _CLARIFY])
    assert "named_directive_clarified" not in (plan.retry_policies or [])
    assert not plan.steps and plan.clarify is not None


def test_a_named_directive_planned_directly_is_untouched():
    plan, _ = _build("打开前备箱", [_plan("edge-vehicle", "frunk.open", "打开前备箱")])
    assert [s.intent for s in plan.steps] == ["frunk.open"]
    assert not plan.retry_policies

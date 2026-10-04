"""完整的信息问题不再被问成「选哪个动作」（docs/design/2026-10-04-information-question-not-clarified.md）。

固定语料：模型把「露营模式是什么意思？」「空调有哪些模式？」「说明书里『打开后备箱』这一节讲的是什么？」判成「对象不明、需要澄清」，
随后被要求给两个动作选项——用户听到「调高一度 / 调低一度」「直接开启 / 了解含义」。
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
from orchestrator.cloud.planning import PlanBuilder
from runtime.question_shape import is_complete_information_question

from tests.test_planning import MockAgent

#: 第一次尝试的真实形态：目标说要澄清、不给步骤。
_MARKER = json.dumps({"addressed": True, "goal": "需要澄清：用户只给了对象名，未说明意图", "steps": []}, ensure_ascii=False)
_CLARIFY = json.dumps({"addressed": True, "steps": [], "clarify": {
    "question": "你想怎么处理？", "options": [{"label": "直接开启", "send_text": "打开露营模式"},
                                         {"label": "了解含义", "send_text": "露营模式是什么意思"}]}}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
            MockAgent("manual-rag", ["manual.query"], response_only=("manual.query",)),
            MockAgent("edge-vehicle", ["hvac.set"], kind="edge_fast", deployment="edge")]


def _build(text, replies, prefs=None):
    queue = list(replies)
    seen = []

    async def llm(messages):
        seen.append(messages[-1]["content"])
        return queue.pop(0) if queue else queue_last[0]

    queue_last = [replies[-1]]

    async def resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve)
    ctx = PlanContext(session_id="test", prefs=dict(prefs or {}))
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), ctx)), seen


def _manual_plan(builder_text="空调有哪些模式？"):
    from orchestrator.cloud.planning import _assemble_capability_catalog
    ref = _assemble_capability_catalog(_agents()).pair_to_ref[("manual-rag", "manual.query")]
    return json.dumps({"complexity": "simple", "goal": builder_text, "addressed": True, "steps": [
        {"id": "s1", "capability_ref": ref, "slots": {"question": builder_text}, "depends_on": [], "slot_refs": {}}]},
        ensure_ascii=False)


@pytest.mark.parametrize("text", ["露营模式是什么意思？", "空调有哪些模式？", "空调温度怎么调？",
                                  "说明书里“打开后备箱”这一节讲的是什么？", "运动模式到底在哪切换？",
                                  "没开定位为什么还有距离", "解释定位原理", "刚才那个红灯我是不是闯了"])
def test_complete_information_questions(text):
    assert is_complete_information_question(text)


@pytest.mark.parametrize("text", ["云岚国际中心", "空调呢？", "露营模式？", "处理一下停车的事", "帮我看看华润大厦",
                                  "附近有什么好玩的帮我安排一下", "打开空调26度", "把温度调高两度", "讲个笑话"])
def test_bare_objects_directives_and_tasks_are_not(text):
    assert not is_complete_information_question(text)


@pytest.mark.parametrize("first", [_MARKER, _CLARIFY])
def test_a_clarified_information_question_is_replanned_without_options(first):
    """第一次判成澄清（目标标记或澄清卡）⇒ 校正后第二次只许给计划。"""
    plan, seen = _build("空调有哪些模式？", [first, _manual_plan()])
    assert [s.intent for s in plan.steps] == ["manual.query"] and plan.clarify is None
    assert "information_question_clarified" in (plan.retry_policies or [])
    assert "完整的信息问题" in seen[-1] and "不要向用户反问" in seen[-1]


@pytest.mark.parametrize("text", ["没开定位为什么还有距离", "刚才那个红灯我是不是闯了", "解释定位原理"])
def test_a_model_that_keeps_clarifying_ends_in_talk_not_an_action_card(text):
    """模型两次都要澄清（旧用例里的例句）：不出动作选项卡，落到兜底 Agent 答一句。"""
    plan, _ = _build(text, [_CLARIFY, _CLARIFY])
    assert [s.intent for s in plan.steps] == ["chitchat.talk"] and plan.clarify is None


@pytest.mark.parametrize("text", ["云岚国际中心", "处理一下停车的事"])
def test_genuinely_unclear_inputs_still_clarify(text):
    plan, _ = _build(text, [_CLARIFY, _CLARIFY])
    assert not plan.steps and plan.clarify is not None
    assert "information_question_clarified" not in (plan.retry_policies or [])

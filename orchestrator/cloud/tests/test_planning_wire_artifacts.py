"""规划器输出里「只可能表示零步」的编码残片规范成 `steps: []`（评审三轮追加批 N，2026-09-24；设计 §17）。

collector 35 个 trace：模型把空数组连同工具编码的收尾标签一起吐进 steps（`["[]</steps>"]` / `"[]</steps>"`）。解析层当
解析失败丢弃——模型明说了的「受话、零步」变成一次失败重试，第二轮再吐坏就是技术失败：「座椅有哪些调节功能」「用两句话介绍一下
深圳」「好的，我会靠边停车检查」都让用户听「没能拆成可以执行的步骤」。规范化只认这一种精确形态、只可能产出零步；别的畸形照旧
整份丢弃重试；`addressed` 写成字符串不规范化（D14 契约：无需动作只认精确 JSON true，见 `test_intent_adversarial_capability_refs`）。
"""
from __future__ import annotations

import pytest

from orchestrator.cloud.planning import _normalize_wire_artifacts
from tests.test_planning_info_request_no_action import GARBAGE, NO_ACTION, REAL_PLAN, _build


@pytest.mark.parametrize("wire, expected", [
    ({"addressed": True, "steps": ["[]</steps>"]}, {"addressed": True, "steps": []}),
    ({"addressed": True, "steps": "[]</steps>"}, {"addressed": True, "steps": []}),
    ({"addressed": True, "steps": " [ ] "}, {"addressed": True, "steps": []}),
    ({"addressed": False, "steps": "[]</steps>"}, {"addressed": False, "steps": []}),
])
def test_the_empty_array_artifact_becomes_an_empty_steps_list(wire, expected):
    assert _normalize_wire_artifacts(wire) == expected


@pytest.mark.parametrize("wire", [
    {"addressed": True, "steps": ["s1"]},                       # 不是空数组的残片：畸形照旧
    {"addressed": True, "steps": "[s1]</steps>"},
    {"addressed": True, "steps": ["[]</steps>", "[]</steps>"]},
    {"addressed": "calc", "steps": []},
    {"addressed": "", "steps": ""},
    {"addressed": "true", "steps": []},                         # D14：字符串 true 不是无需动作
    {"addressed": 1, "steps": []},
    {"addressed": True, "steps": [{"id": "s1"}]},
])
def test_other_malformed_wires_are_left_as_they_are(wire):
    assert _normalize_wire_artifacts(wire) == wire


ARTIFACT = '{"addressed":true,"steps":["[]</steps>"]}'
ARTIFACT_STR = '{"addressed":true,"steps":"[]</steps>"}'


@pytest.mark.parametrize("text, replies, suffix", [
    ("用两句话介绍一下深圳", [ARTIFACT, NO_ACTION], "_no_action"),          # collector 原形：两轮都说无需动作
    ("好的，我会靠边停车检查", [ARTIFACT, NO_ACTION], "_no_action"),
    ("座椅有哪些调节功能", [ARTIFACT, '{"addressed":"","steps":""}'], "_no_action_info"),   # 求信息 + 第二轮真坏
    ("那它的价格呢", [ARTIFACT_STR, GARBAGE], "_no_action_info"),
])
def test_an_artifact_is_the_models_no_action_verdict(text, replies, suffix):
    plan, calls = _build(replies, text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith(suffix), plan.plan_mode


def test_a_directive_is_still_rescued_by_the_retry():
    """「受话、零步」的重试照旧催第二轮：指令句第二轮交出真计划就用真计划（规范化不吞掉重试）。"""
    plan, calls = _build([ARTIFACT, REAL_PLAN], "空调调到24度")
    assert [s.intent for s in plan.steps] == ["hvac.set"], plan.steps
    assert calls["llm"] == 2


def test_a_directive_with_an_artifact_and_garbage_is_still_a_technical_failure():
    """指令句：一次残片（= 零步）+ 一次真坏，照旧是技术失败，不被伪装成谈话。"""
    plan, calls = _build([ARTIFACT, GARBAGE], "空调调到24度")
    assert plan.technical_failure is True


def test_the_tool_channel_normalises_too(monkeypatch):
    """工具通道（生产缺省）：`submit_plan` 的 arguments 在 `_llm_plan_tools` 出口规范化；观测里的 raw 保留原样。"""
    import asyncio

    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    from orchestrator.cloud.planning import PlanBuilder, _SUBMIT_PLAN_NAME
    from tests.test_planning_info_request_no_action import _agents

    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    replies = [("", [{"id": f"c{i}", "name": _SUBMIT_PLAN_NAME,
                      "arguments": {"addressed": True, "steps": ["[]</steps>"]}}]) for i in (1, 2)]

    async def llm_tools(messages, tools):
        return replies.pop(0)

    async def llm(messages):
        raise AssertionError("工具通道两轮都交了参数，不该掉到文本通道")

    async def resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve, llm_tool_fn=llm_tools)
    plan = asyncio.run(builder.build("好的，我会靠边停车检查", WorkingSet(catalog=_agents()),
                                     PlanContext(session_id="t")))
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith("_no_action"), plan.plan_mode
    assert "</steps>" in plan.raw_llm

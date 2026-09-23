"""求信息的请求：模型说过一次「受话了、无需动作」、两轮都没交出合法计划 ⇒ 兜底谈话作答，不是技术失败（追加批 F，F-2）。

真栈 `f9bec423` RS28，cloud-planner 日志逐 trace：
- 「推荐三部适合全家看的电影」第一轮 `{"addressed":false,"steps":[]}`、第二轮 `{"addressed":true,"steps":[]}`；
- 「天窗有什么用」第一轮 `{"addressed":true,"steps":[]}`、第二轮一段非 JSON 的自然语言。
既有规则只认「连说两次无需动作」，两句都落 `_fallback` + `technical_failure`，AR05 F09 让用户听到「这次我没能把
您的请求拆成可以执行的步骤」——而这两句恰恰就该兜底谈话来答。

F09 挡的是**指令句**被兜底谈话伪装成一次成功（`test_one_no_action_plus_one_garbage_is_a_degradation_not_a_judgement`
钉着「打开空调」那一侧，本批一字不动）；求信息的请求本来就是要一段回答，谈话不是伪装。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder

from tests.test_planning import MockAgent

NO_ACTION = '{"addressed":true,"steps":[]}'
NOT_ADDRESSED = '{"addressed":false,"steps":[]}'
GARBAGE = "这句话是在问天窗的用途，属于常识问答，我来提交计划。"
REAL_PLAN = ('{"addressed":true,"steps":[{"id":"s1","capability_ref":"cap_0003",'
             '"slots":{},"depends_on":[],"slot_refs":{}}]}')


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
            MockAgent("hvac", ["hvac.set", "hvac.off"])]


def _build(replies, text):
    calls = {"llm": 0, "fallback": 0}

    async def mock_llm(messages):
        calls["llm"] += 1
        return replies[min(calls["llm"] - 1, len(replies) - 1)]

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    inner = builder._fallback

    async def counted(t, agents=None):
        calls["fallback"] += 1
        return await inner(t, agents)

    builder._fallback = counted
    plan = asyncio.run(builder.build(text, WorkingSet(catalog=_agents()),
                                     PlanContext(session_id="test")))
    return plan, calls


@pytest.mark.parametrize("text, replies", [
    ("推荐三部适合全家看的电影", [NOT_ADDRESSED, NO_ACTION]),   # 真栈第一、二趟的形态
    ("天窗有什么用", [NO_ACTION, GARBAGE]),                    # 真栈第一趟的形态
    ("天窗有什么用", [GARBAGE, NO_ACTION]),
    ("给我推荐几本适合小学生读的书", [NO_ACTION, GARBAGE]),
    ("介绍一下北京有哪些著名的历史建筑", [NO_ACTION, GARBAGE]),
    ("为什么冬天续航会下降", [NO_ACTION, GARBAGE]),
])
def test_information_request_is_answered_by_the_talk_agent(text, replies):
    plan, calls = _build(replies, text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith("_no_action_info"), plan.plan_mode
    assert calls["fallback"] == 0


@pytest.mark.parametrize("text", [
    "打开空调",
    "帮我订一张明天去上海的机票",
    "导航去公司",
])
def test_a_directive_with_the_same_shape_is_still_a_technical_failure(text):
    plan, calls = _build([NO_ACTION, GARBAGE], text)
    assert plan.technical_failure is True
    assert calls["fallback"] == 1
    assert not plan.plan_mode.endswith("_no_action_info")


def test_an_information_request_the_model_never_judged_is_still_a_technical_failure():
    """两轮都坏（模型一次都没说过无需动作）：没有模型自己的判断可以兑现。"""
    plan, calls = _build([GARBAGE, "还是不是 JSON"], "天窗有什么用")
    assert plan.technical_failure is True
    assert calls["fallback"] == 1


def test_an_information_request_still_gets_its_retry():
    """一次空计划仍可能只是抽风：第二轮拿到真计划时照用真计划（需要数据的问句靠这一轮补上工具）。"""
    plan, calls = _build([NO_ACTION, REAL_PLAN], "推荐三部适合全家看的电影")
    assert [s.intent for s in plan.steps] == ["hvac.set"]
    assert calls["llm"] == 2 and calls["fallback"] == 0
    assert not plan.plan_mode.endswith("_no_action_info")

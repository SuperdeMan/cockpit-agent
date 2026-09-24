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


def test_a_clarify_flavoured_empty_plan_on_an_information_request_is_answered_too():
    """真栈 `01564cc2` C 臂 K3 第 1 趟逐字：第一轮 `{"addressed":true,"steps":[],"goal":"需要澄清：用户未指定动作类型
    （介绍/导航/查天气等）"}`、第二轮 `{"addressed":"calc","steps":[]}` ⇒ 技术失败 + `clarify_wanted` ⇒ 用户听到「我听到了
    「介绍一下北京有哪些著名的历史建筑」，但没听清要拿它做什么」——可用户说了「介绍一下」。原话的请求形态是确定性证据，
    模型口中的「没指定动作」与它矛盾；带澄清口吻的合法空计划同样算模型「交了零步」。"""
    marker = ('{"addressed":true,"steps":[],'
              '"goal":"需要澄清：用户未指定动作类型（介绍/导航/查天气等）"}')
    plan, calls = _build([marker, '{"addressed":"calc","steps":[]}'],
                         "介绍一下北京有哪些著名的历史建筑")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False and plan.clarify_wanted is False
    assert plan.plan_mode.endswith("_no_action_info"), plan.plan_mode


def test_a_reference_question_with_some_is_an_information_request_too():
    """追加批 L（设计 §15）：真栈 `8cee1699` trace `256c1c85` 逐字——「开长途前要注意些什么」第一轮 goal「需要澄清：用户给出对象…
    但动作缺失」零步、第二轮 `{"addressed":false,"steps":[]}` ⇒ 技术失败 + `clarify_wanted` ⇒ 用户听到「我听到了…但没听清要拿它做什么」。
    问句判据只收了「注意什么」，没收「注意些什么」，这句不算求信息的请求，F-2 接不住。"""
    marker = ('{"addressed":true,"steps":[],"goal":"需要澄清：用户给出对象\\"开长途前要注意些什么\\"但动作缺失"}')
    plan, calls = _build([marker, '{"addressed":false,"steps":[]}'], "开长途前要注意些什么")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False and plan.clarify_wanted is False
    # JSON 通道里第二轮「不受话」是合法空计划，由 F-2 续（`_not_addressed_info`）接；工具通道里它是违约、走 `_no_action_info`。
    assert plan.plan_mode.endswith("_info"), plan.plan_mode


def test_a_bare_object_with_a_clarify_marker_still_gets_the_honest_unresolved_exit():
    """对照：光说一个地名（不是求信息的请求）时，F09-b 那条「我听到了 X，但没听清要拿它做什么」照旧。"""
    marker = '{"addressed":true,"steps":[],"goal":"需要澄清：用户只给了地点名，未说明要做什么"}'
    plan, calls = _build([marker, "这不是 JSON"], "云岚国际中心")
    assert plan.technical_failure is True and plan.clarify_wanted is True


def _build_with_source(replies, text, input_source):
    calls = {"llm": 0}

    async def mock_llm(messages):
        calls["llm"] += 1
        return replies[min(calls["llm"] - 1, len(replies) - 1)]

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    ctx = PlanContext(session_id="test", prefs={"input_source": input_source} if input_source else {})
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), ctx))


@pytest.mark.parametrize("replies", [
    [NO_ACTION, NOT_ADDRESSED],          # 真栈 `a59b1621` RS29「天窗为什么关不上」第 3 趟逐字
    [NOT_ADDRESSED, NOT_ADDRESSED],
])
def test_a_not_addressed_empty_plan_on_typed_information_request_is_answered(replies):
    """显式输入里「不受话」不成立（拒识只盖语音来源）：最后一轮合法的「不受话、零步」落进 engine 就是「抱歉，我没听清」。"""
    plan = _build_with_source(replies, "天窗为什么关不上", "")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.plan_mode.endswith("_not_addressed_info"), plan.plan_mode


def test_voice_not_addressed_stays_a_rejection():
    """语音来源的「不受话」是拒识的正式出口，一字不动。"""
    plan = _build_with_source([NOT_ADDRESSED], "天窗为什么关不上", "voice_wake")
    assert plan.steps == [] and plan.addressed is False


def test_a_typed_directive_not_addressed_is_not_turned_into_talk():
    plan = _build_with_source([NOT_ADDRESSED, NOT_ADDRESSED], "打开空调", "")
    assert not plan.plan_mode.endswith("_not_addressed_info")


def test_a_typed_bare_object_not_addressed_is_not_turned_into_talk():
    """对照（走得到新规则的那一条）：光说一个地名不是求信息的请求，「不受话、零步」照旧交 engine。"""
    plan = _build_with_source([NOT_ADDRESSED, NOT_ADDRESSED], "云岚国际中心", "")
    assert plan.steps == [] and plan.addressed is False
    assert not plan.plan_mode.endswith("_not_addressed_info")


# ── 追加批 N（N-2，2026-09-24；设计 §17）：「帮我查查 / 查一下 + 疑问框架」也是求信息的请求 ────────────────────

@pytest.mark.parametrize("text", [
    "你帮我查查卤牛肉怎么做呀？",                 # 真实用户（collector app-mh5f6e）
    "帮我查一下小米SU7的官方续航是多少",          # e2e w19c 两趟
])
def test_a_lookup_request_with_a_question_frame_is_answered_by_the_talk_agent(text):
    marker = '{"addressed":true,"steps":[],"goal":"需要澄清：用户只提到对象，未说明要做什么动作"}'
    plan, calls = _build([marker, NO_ACTION], text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith("_no_action_info"), plan.plan_mode


def test_a_lookup_directive_without_a_question_frame_is_still_a_technical_failure():
    plan, calls = _build([NO_ACTION, GARBAGE], "替我查一下路况")
    assert plan.technical_failure is True

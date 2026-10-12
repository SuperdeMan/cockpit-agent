"""条件指令的后件写步按问句拦，条件本身的查询照常执行（2026-10-12 试放行后撤回）。

「如果深圳今天不下雪，就把空调打开」：后件 `hvac.on` 不论条件真假都拦——首轮条件还没查；T2 看过观察之后，再规划也不可靠，
2026-10-12 在 T2 放行时，它在前件为假的情况下照样派出了后件（D02 3/3 开了空调）。查天气这种只读步不受影响。
宁可不执行，也不能条件为假却执行。放行不在这道闸里：T2 的前件判定判为成立、证据复核通过后，由 `loop.py` 放行后件点名的步
（`test_condition_judgement_loop.py`）；这道闸本身对条件指令照旧一律拦。
"""
from __future__ import annotations

import pytest

from orchestrator.cloud.models import Step
from orchestrator.cloud.planning import PlanBuilder

COND_TRUE = "如果深圳今天不下雪，就把空调打开"
COND_FALSE = "如果深圳今天下雪，就把空调打开"


def _hvac_on():
    return Step(id="r1", agent_id="edge-vehicle", intent="hvac.on", deployment="edge", kind="edge_fast",
                capability_description="打开空调")


def _trunk_open():
    return Step(id="r2", agent_id="edge-vehicle", intent="trunk.open", deployment="edge", kind="edge_fast",
                require_confirm=True, capability_description="打开后备箱")


def _weather():
    return Step(id="w1", agent_id="info", intent="info.weather", deployment="cloud", kind="agent",
                capability_description="查询天气")


@pytest.mark.parametrize("origin", [COND_TRUE, COND_FALSE])
def test_the_consequent_of_a_conditional_instruction_is_blocked(origin):
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_hvac_on(), _trunk_open()], origin)
    assert kept == [] and [s.intent for s in blocked] == ["hvac.on", "trunk.open"]


@pytest.mark.parametrize("origin", [
    "如果深圳今天下雪，就帮我把空调打开",   # 核心旅程 D04：修前带「帮我」不算提问，条件为假照样开了空调
    "如果深圳今天下雪，请把空调打开",
    "若深圳今天下雪，就把空调打开",
    "如果深圳今天下雪的话，帮我打开空调",
])
def test_a_conditional_instruction_that_is_not_question_shaped_is_blocked_too(origin):
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_weather(), _hvac_on(), _trunk_open()], origin)
    assert [s.intent for s in kept] == ["info.weather"]
    assert [s.intent for s in blocked] == ["hvac.on", "trunk.open"]


@pytest.mark.parametrize("origin", ["如果可以的话，帮我把空调打开", "帮我把空调打开"])
def test_politeness_and_plain_requests_still_execute(origin):
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_hvac_on()], origin)
    assert [s.intent for s in kept] == ["hvac.on"] and blocked == []


def test_the_read_that_checks_the_condition_still_runs():
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_weather(), _hvac_on()], COND_FALSE)
    assert [s.intent for s in kept] == ["info.weather"] and [s.intent for s in blocked] == ["hvac.on"]

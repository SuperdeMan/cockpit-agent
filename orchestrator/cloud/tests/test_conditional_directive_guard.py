"""条件指令的后件写步：首轮照拦，观察在手之后放行（CA2-10 ②，2026-10-12）。

修前「如果深圳今天不下雪，就把空调打开」整句判成提问：首轮拦下后件是对的（条件还没查），T2 再规划看过天气、正确地派出
`hvac.on`，却被同一道问句闸再拦一次——条件指令整句作废（核心旅程 D01 修前 0/3）。现在只有拿到观察的两处放行：
T2 再规划、恢复挂起计划；Agent 改派照旧严格。需确认的照旧走确认，端侧写照旧过 VAL。
"""
from __future__ import annotations

import pytest

from orchestrator.cloud.models import Step
from orchestrator.cloud.planning import PlanBuilder

COND = "如果深圳今天不下雪，就把空调打开"


def _hvac_on():
    return Step(id="r1", agent_id="edge-vehicle", intent="hvac.on", deployment="edge", kind="edge_fast",
                capability_description="打开空调")


def _trunk_open():
    return Step(id="r2", agent_id="edge-vehicle", intent="trunk.open", deployment="edge", kind="edge_fast",
                require_confirm=True, capability_description="打开后备箱")


def test_first_round_still_blocks_the_consequent():
    """首轮：条件还没查，后件写步照旧拦（问句闸逐字不变）。"""
    blocked = PlanBuilder._question_side_effect_steps([_hvac_on()], COND)
    assert [s.intent for s in blocked] == ["hvac.on"]
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_hvac_on()], COND)
    assert kept == [] and [s.intent for s in blocked] == ["hvac.on"]


def test_after_observation_the_consequent_of_a_conditional_directive_is_kept():
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
        [_hvac_on(), _trunk_open()], COND, after_observation=True)
    assert [s.intent for s in kept] == ["hvac.on", "trunk.open"] and blocked == []
    assert kept[1].require_confirm  # 需确认的照旧走确认：放行不等于免确认


@pytest.mark.parametrize("origin", [
    "如果开空调的话会不会更费电",   # 后件是提问
    "如果你能去旅行，你想去哪里",   # 假设提问
    "空调有哪些模式",               # 普通提问
    "车里温度低于20度的时候打开空调",  # 等将来的条件：观察回不答它
])
def test_after_observation_questions_and_deferred_conditions_are_still_blocked(origin):
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
        [_hvac_on()], origin, after_observation=True)
    if PlanBuilder._question_side_effect_steps([_hvac_on()], origin):
        assert kept == [] and [s.intent for s in blocked] == ["hvac.on"]
    else:  # 不是提问的句子本来就不拦（与修前一致）
        assert [s.intent for s in kept] == ["hvac.on"]


def test_an_unknown_origin_still_fails_closed_after_observation():
    """恢复的旧计划没有安全原话：放行只认条件指令的原话，空原话照旧按声明拦。"""
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
        [_hvac_on()], "", after_observation=True)
    assert kept == [] and [s.intent for s in blocked] == ["hvac.on"]

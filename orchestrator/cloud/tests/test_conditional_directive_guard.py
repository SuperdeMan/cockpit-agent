"""条件指令的后件写步按问句拦，条件本身的查询照常执行（2026-10-12 试放行后撤回）。

「如果深圳今天不下雪，就把空调打开」：后件 `hvac.on` 不论条件真假都拦——首轮条件还没查；T2 看过观察之后，再规划也不可靠，
2026-10-12 在 T2 放行时，它在前件为假的情况下照样派出了后件（D02 3/3 开了空调）。查天气这种只读步不受影响。
条件判定可靠之前，宁可不执行（D01 标 known_red），也不能条件为假却执行。
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


def test_the_read_that_checks_the_condition_still_runs():
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps([_weather(), _hvac_on()], COND_FALSE)
    assert [s.intent for s in kept] == ["info.weather"] and [s.intent for s in blocked] == ["hvac.on"]

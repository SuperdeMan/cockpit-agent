"""补槽答案替换了挂起步里原来的槽值（评审四轮，`4438ea6b` 真栈 RS39）：判据一份，两处消费。"""
from __future__ import annotations

from orchestrator.cloud.models import Step
from orchestrator.cloud.superseded import drop_superseded_steps, rewrite_goal, superseded_values


def test_a_different_answer_replaces_the_value_the_agent_could_not_use():
    assert superseded_values({"destination": "云岚国际中心"}, {"destination": "深圳湾公园"}) == {
        "云岚国际中心": "深圳湾公园"}


def test_refinement_empty_and_repeated_answers_are_not_replacements():
    """「万象城」→「深圳湾万象城」是细化；原来没有值是补上；答了同一个值什么都没换。"""
    assert superseded_values({"destination": "万象城"}, {"destination": "深圳湾万象城"}) == {}
    assert superseded_values({"destination": "深圳湾万象城"}, {"destination": "万象城"}) == {}
    assert superseded_values({}, {"time": "明天早上八点"}) == {}
    assert superseded_values({"destination": ""}, {"destination": "深圳湾公园"}) == {}
    assert superseded_values({"destination": "深圳湾公园"}, {"destination": " 深圳湾公园 "}) == {}


def test_the_goal_follows_the_user_only_where_the_old_value_appears():
    assert rewrite_goal("解析\"云岚国际中心\"为可导航的具体地点后启动导航", {"云岚国际中心": "深圳湾公园"}) == (
        "解析\"深圳湾公园\"为可导航的具体地点后启动导航")
    assert rewrite_goal("导航去目的地", {"云岚国际中心": "深圳湾公园"}) == "导航去目的地"
    assert rewrite_goal("", {"云岚国际中心": "深圳湾公园"}) == ""


def _step(step_id, slots, depends_on=()):
    return Step(id=step_id, agent_id="navigation", intent="navigation.x", slots=dict(slots),
                depends_on=list(depends_on))


def test_steps_still_chasing_the_replaced_value_are_dropped_with_their_dependents():
    steps = [_step("r1", {"keyword": "云岚国际中心"}),
             _step("r2", {"destination": "云岚国际中心附近"}),
             _step("r3", {"city": "深圳"}, depends_on=["r1"]),
             _step("r4", {"destination": "深圳湾公园"})]
    kept, dropped = drop_superseded_steps(steps, {"云岚国际中心": "深圳湾公园"})
    assert [s.id for s in kept] == ["r4"]
    assert dropped == ["r1", "r2", "r3"]


def test_nothing_is_dropped_without_a_replacement():
    steps = [_step("r1", {"keyword": "云岚国际中心"})]
    kept, dropped = drop_superseded_steps(steps, {})
    assert [s.id for s in kept] == ["r1"] and dropped == []

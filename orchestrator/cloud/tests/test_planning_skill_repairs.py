"""Skill 声明式计划归一：软知识被模型忽略时，只补已声明的数据接线。"""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.cloud import planning
from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import Plan, PlanContext, Step
from orchestrator.cloud.planning import PlanBuilder
from tests.test_planning import MockAgent


def _raw(*, item: str = "", slot_refs: dict | None = None) -> str:
    slots = {"item": item} if item else {}
    return json.dumps({
        "addressed": True,
        "steps": [
            {"id": "s1", "capability_ref": "cap_0001",
             "slots": {}, "depends_on": [], "slot_refs": {}},
            {"id": "s2", "capability_ref": "cap_0002",
             "slots": slots, "depends_on": [],
             "slot_refs": dict(slot_refs or {})},
        ],
    })


def _build(monkeypatch, text: str, *, item: str = "", clipped: bool = False,
           slot_refs: dict | None = None):
    async def llm(_messages):
        return _raw(item=item, slot_refs=slot_refs)

    async def resolve(_query, top_k=1):
        return []

    async def plan_skills(_text, *, capability_refs):
        assert capability_refs
        suffix = "!clipped" if clipped else ""
        return "full", [f"full:shop-order-flow@lex:35{suffix}"], "guide block"

    async def plan_exemplars(_text, *, capability_refs):
        assert capability_refs
        return "off", [], ""

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setattr(planning._skills, "plan_skills", plan_skills)
    monkeypatch.setattr(planning._exemplars, "plan_exemplars", plan_exemplars)
    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve)
    agents = [MockAgent("mcp-bridge", ["shop.menu", "shop.order"])]
    return asyncio.run(builder.build(
        text, WorkingSet(catalog=agents), PlanContext(session_id="skill-repair")))


def test_declared_skill_repair_connects_existing_steps(monkeypatch):
    plan = _build(monkeypatch, "看看菜单，然后点一份招牌")
    order = plan.steps[1]
    assert order.depends_on == ["s1"]
    assert order.slot_refs == {"item": "s1.data.items.0.name"}
    assert plan.skill_effects == [
        "shop-order-flow:dependency_slot_ref:shop.menu->shop.order.item"
    ]


def test_declared_skill_repair_replaces_malformed_model_ref(monkeypatch):
    """A non-empty token is not necessarily a reference.

    MiniMax once emitted ``{"item":"item","s1.data.items.0.name":""}``:
    the old truthiness check treated ``item`` as valid and skipped the trusted
    declaration, leaving a data-dependent order disconnected from its menu.
    """
    plan = _build(
        monkeypatch,
        "先看看菜单，再点一杯店里的招牌",
        slot_refs={"item": "item", "s1.data.items.0.name": ""},
    )

    order = plan.steps[1]
    assert order.depends_on == ["s1"]
    assert order.slot_refs == {"item": "s1.data.items.0.name"}
    assert plan.skill_effects == [
        "shop-order-flow:dependency_slot_ref:shop.menu->shop.order.item"
    ]


def test_trigger_word_copied_into_the_slot_is_a_placeholder_not_a_user_value(monkeypatch):
    """把触发词原样抄进槽位，不算「用户已经给了真值」。

    `trigger_any` 列的正是「指向一件还不知道名字的东西」的说法——`招牌` 只有等
    `shop.menu` 回来才有具体值。真栈实测（MiniMax 主模型 `cp.dep.menu-then-order`
    10 样本里 1 条）：模型 `depends_on` 接对了，却把 `item="招牌"` 填进 slots、
    slot_refs 留空，归一被 `real_value` 挡住，字面量一路发到商户侧。

    判据：**声明已经说了这个 token 意味着「值在别处」，就不能反过来拿它当值。**
    """
    plan = _build(monkeypatch, "看看菜单，然后点一份招牌", item="招牌")

    order = plan.steps[1]
    assert order.depends_on == ["s1"]
    assert order.slot_refs == {"item": "s1.data.items.0.name"}
    # 占位符必须被清掉：留着它，执行期就有两个来源在争同一个槽。
    assert "item" not in order.slots
    assert plan.skill_effects == [
        "shop-order-flow:dependency_slot_ref:shop.menu->shop.order.item"
    ]


def test_a_named_dish_that_merely_contains_a_trigger_word_is_still_a_user_value(monkeypatch):
    """只认全等：`招牌牛肉面` 是用户点名的具体商品，不许被改写成 items.0.name。

    收窄面的另一半——放宽成子串匹配就会把真值当占位符吞掉，
    那是「为了让一条用例变绿而扩大改写面」，代价是替用户改单。
    """
    plan = _build(monkeypatch, "看看菜单，然后点一份招牌牛肉面", item="招牌牛肉面")

    order = plan.steps[1]
    assert order.depends_on == []
    assert order.slot_refs == {}
    assert order.slots == {"item": "招牌牛肉面"}
    assert plan.skill_effects == []


def test_nearby_detail_repair_connects_the_selected_search_result():
    """The new guide declaration must reach the generic repair consumer."""
    plan = Plan(steps=[
        Step(id="s1", agent_id="nearby", intent="nearby.search"),
        Step(id="s2", agent_id="nearby", intent="nearby.detail"),
    ])
    effects = planning._skills.apply_plan_repairs(
        plan,
        "搜一下附近的火锅店，再看看第一家的详情",
        ["full:nearby-detail-flow@lex:42"],
    )

    assert plan.steps[1].depends_on == ["s1"]
    assert plan.steps[1].slot_refs == {"poi_id": "s1.data.items.0.id"}
    assert effects == [
        "nearby-detail-flow:dependency_slot_ref:nearby.search->nearby.detail.poi_id"
    ]


@pytest.mark.parametrize("text,item,clipped", [
    ("看看菜单，然后帮我下单", "", False),       # 无声明 trigger：不能擅自选第一件
    ("看看菜单，然后点一杯拿铁", "拿铁", False),  # 用户真值优先：不能被引用覆盖
    ("看看菜单，然后点一份招牌", "", True),       # 被预算裁掉的 guide 不得暗中生效
])
def test_skill_repair_never_guesses_or_overwrites(monkeypatch, text, item, clipped):
    plan = _build(monkeypatch, text, item=item, clipped=clipped)
    order = plan.steps[1]
    assert order.depends_on == []
    assert order.slot_refs == {}
    assert order.slots == ({"item": item} if item else {})
    assert plan.skill_effects == []

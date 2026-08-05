"""Skill 声明式计划归一：软知识被模型忽略时，只补已声明的数据接线。"""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.cloud import planning
from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder
from tests.test_planning import MockAgent


def _raw(*, item: str = "") -> str:
    slots = {"item": item} if item else {}
    return json.dumps({
        "addressed": True,
        "steps": [
            {"id": "s1", "capability_ref": "cap_0001",
             "slots": {}, "depends_on": [], "slot_refs": {}},
            {"id": "s2", "capability_ref": "cap_0002",
             "slots": slots, "depends_on": [], "slot_refs": {}},
        ],
    })


def _build(monkeypatch, text: str, *, item: str = "", clipped: bool = False):
    async def llm(_messages):
        return _raw(item=item)

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

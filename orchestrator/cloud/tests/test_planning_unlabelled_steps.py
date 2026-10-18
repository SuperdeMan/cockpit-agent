"""步骤全没写 `id`、又没有一步引用别的步：按位置补 `s1…sN`（200×5 后续批 §22.4）。

2026-10-18 MiniMax-M3 阵发性漏写步骤 `id`（工具 schema 标了必填，厂商不强制）：线契约整份拒绝、重试又漏，正确的单步导航落成
「没能拆成步骤」（EN09 复测两遍）。没有引用时 `id` 只是标签；有引用、部分写了、顶层出现 `id`、缺 `capability_ref` 的照旧整份拒绝。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.cloud import planning


def _agent(agent_id: str, *intents: str):
    caps = [SimpleNamespace(intent=intent, slots=[], description="", examples=[], heavy=False,
                            require_confirm=False, verification=None)
            for intent in intents]
    manifest = SimpleNamespace(agent_id=agent_id, capabilities=caps, latency_budget_ms=5000, kind="agent",
                               deployment="cloud", requires_permissions=[], trust_level="first_party",
                               context_scopes=[], route_hints=[], category="")
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50051")


@pytest.fixture()
def refs():
    catalog = planning._assemble_capability_catalog(
        [_agent("navigation", "navigation.navigate_to"), _agent("charging-planner", "charging.find")])
    return catalog, catalog.pair_to_ref[("navigation", "navigation.navigate_to")], \
        catalog.pair_to_ref[("charging-planner", "charging.find")]


def _parse(catalog, steps, **top):
    return planning.PlanBuilder(None, None)._parse_and_validate_data(
        {"addressed": True, "complexity": "simple", **top, "steps": steps}, catalog, "q")


def test_a_single_step_without_an_id_is_labelled(refs):
    catalog, nav, _ = refs
    plan = _parse(catalog, [{"slot_refs": {}, "slots": {"destination": "厦门火车站"}, "capability_ref": nav,
                             "depends_on": []}])
    assert plan is not None and [(s.id, s.intent, s.slots) for s in plan.steps] == [
        ("s1", "navigation.navigate_to", {"destination": "厦门火车站"})]


def test_independent_steps_without_ids_are_labelled_by_position(refs):
    catalog, nav, charging = refs
    plan = _parse(catalog, [
        {"slots": {"destination": "深圳北站"}, "capability_ref": nav},
        {"slots": {"destination": "深圳北站"}, "capability_ref": charging, "depends_on": [], "slot_refs": {}}])
    assert plan is not None and [(s.id, s.intent) for s in plan.steps] == [
        ("s1", "navigation.navigate_to"), ("s2", "charging.find")]


@pytest.mark.parametrize("steps", [
    # 有依赖 / 引用：模型心里有编号，补出来的编号是猜的
    lambda nav, ch: [{"slots": {"keyword": "厦门火车站"}, "capability_ref": ch},
                     {"slots": {"destination": "厦门火车站"}, "capability_ref": nav, "depends_on": ["s1"]}],
    lambda nav, ch: [{"slots": {}, "capability_ref": ch},
                     {"slots": {}, "capability_ref": nav, "slot_refs": {"destination": "s1.data.items.0.name"}}],
    lambda nav, ch: [{"slots": {}, "capability_ref": ch},
                     {"slots": {"destination": "${s1.data.items.0.name}"}, "capability_ref": nav}],
    lambda nav, ch: [{"slots": {}, "capability_ref": ch},
                     {"slots": {"destination": "从s1结果选取"}, "capability_ref": nav}],
    lambda nav, ch: [{"slots": {}, "capability_ref": ch},
                     {"slots": {"destination": "${find.data.items.0.name}"}, "capability_ref": nav}],
    lambda nav, ch: [{"slots": {}, "capability_ref": ch},
                     {"slots": {"destination": "$find.data.items.0.name"}, "capability_ref": nav}],
    # 夹着不是对象的元素：补标签不许顺手把它丢掉
    lambda nav, ch: [{"slots": {"destination": "厦门火车站"}, "capability_ref": nav}, "s2: 找充电站"],
    # 只有部分步骤写了 id
    lambda nav, ch: [{"id": "s1", "slots": {}, "capability_ref": ch}, {"slots": {}, "capability_ref": nav}],
    # 缺 capability_ref 是身份缺失
    lambda nav, ch: [{"slots": {"destination": "厦门火车站"}}],
])
def test_ambiguous_unlabelled_plans_are_still_rejected(refs, steps):
    catalog, nav, charging = refs
    assert _parse(catalog, steps(nav, charging)) is None


def test_an_id_at_the_top_level_means_the_values_are_misplaced(refs):
    catalog, nav, _ = refs
    assert _parse(catalog, [{"slots": {"destination": "厦门火车站"}, "capability_ref": nav}], id="s1") is None

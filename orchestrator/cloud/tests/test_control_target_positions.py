"""反向控制继承**目标位置**（评审四轮 R4-04，2026-09-24）。

`_focused_control_ellipsis_plan` 按焦点的 `last_intent` 推出开 / 关方向，却以 `slots: {}` 造新步：
焦点 `window.open` + `positions=['副驾']`，「关掉」⇒ `window.close {}`（全车）。真栈上位置丢得更早——
端侧本地动作 payload 只有 `command`、上云的执行账本只有名字、`apply_control` 一律清空位置、云侧执行过的控制
下一轮被账本覆盖时同样清空。真栈 `ecbeed28` 修前 RS32 三趟每一个「关掉」都没带位置。

判据：**位置与意图取自同一条执行事实**（同轮端侧 / 上一轮本地 / 那一轮自己落盘的 `control_targets`）；
「关掉」每个位置一步、只继承 `positions`，取不到就是空槽（同修前），不猜、不把旧对象的位置粘到新对象上。
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest  # noqa: E402

from orchestrator.cloud.context import (  # noqa: E402
    Focus, WorkingSet, _scan_positions, augment_focus_with_execution, build_context,
    extract_focus, parse_control_targets, target_positions)
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus  # noqa: E402


# ── 解析：端侧签发，仍按不可信输入收 ──────────────────────────────────────────────

def test_parse_accepts_json_and_lists_and_drops_bad_shapes():
    raw = json.dumps([{"command": "window.open", "positions": ["副驾"]},
                      {"command": "", "positions": ["主驾"]},
                      {"command": "seat.heating.on", "positions": "主驾"},
                      {"command": "hvac.on"},
                      "junk", {"command": 3}], ensure_ascii=False)
    assert parse_control_targets(raw) == [
        {"command": "window.open", "positions": ["副驾"]},
        {"command": "seat.heating.on", "positions": ["主驾"]},
        {"command": "hvac.on", "positions": []},
    ]
    assert parse_control_targets("{not json") == []
    assert parse_control_targets({"command": "window.open"}) == []
    assert parse_control_targets(None) == []


def test_parse_bounds_sizes():
    many = [{"command": f"x{i}.open", "positions": [f"p{j}" for j in range(20)]} for i in range(40)]
    parsed = parse_control_targets(many)
    assert len(parsed) == 16 and all(len(t["positions"]) == 8 for t in parsed)


# ── 同一意图的位置合并 ─────────────────────────────────────────────────────────

def test_positions_are_the_union_for_that_intent_in_order():
    targets = [{"command": "seat.heating.on", "positions": ["主驾"]},
               {"command": "hvac.on", "positions": []},
               {"command": "seat.heating.on", "positions": ["副驾", "主驾"]}]
    assert target_positions(targets, "seat.heating.on") == ["主驾", "副驾"]


def test_a_positionless_execution_of_the_same_intent_means_the_wider_scope():
    """同一组里「打开车窗」（缺省范围）与「打开副驾车窗」并存 ⇒ 不能收窄成副驾。"""
    targets = [{"command": "window.open", "positions": ["副驾"]},
               {"command": "window.open", "positions": []}]
    assert target_positions(targets, "window.open") == []


def test_other_intents_positions_never_leak():
    assert target_positions([{"command": "window.open", "positions": ["副驾"]}], "hvac.on") == []


# ── 焦点：位置跟着意图出自同一条事实 ──────────────────────────────────────────────

def test_same_turn_edge_execution_brings_its_positions():
    out = augment_focus_with_execution(
        None, [], ["window.open"],
        edge_executed_targets=[{"command": "window.open", "positions": ["副驾"]}])
    assert out.last_intent == "window.open" and out.positions == ["副驾"]


def test_previous_local_exchange_brings_its_positions():
    out = augment_focus_with_execution(
        None, [], previous_local_exchange="local-1", previous_local_actions=["seat.heating.on"],
        previous_local_targets=[{"command": "seat.heating.on", "positions": ["主驾"]},
                                {"command": "seat.heating.on", "positions": ["副驾"]}])
    assert out.last_intent == "seat.heating.on" and out.positions == ["主驾", "副驾"]
    assert out.origin_exchange_id == "local-1"


def test_history_override_keeps_the_positions_that_exchange_saved():
    """云侧执行过的控制：下一轮账本（只有名字）覆盖焦点时，位置从那一轮自己落盘的目标里取。"""
    focus = Focus(obj="车窗", last_intent="window.open", positions=["副驾"],
                  control_targets=[{"command": "window.open", "positions": ["副驾"]}],
                  origin_exchange_id="x-cloud")
    history = [{"role": "user", "exchange_id": "x-cloud", "actions": []},
               {"role": "assistant", "exchange_id": "x-cloud", "actions": ["window.open"]}]
    out = augment_focus_with_execution(focus, history)
    assert out.last_intent == "window.open" and out.positions == ["副驾"]


def test_history_override_from_another_exchange_does_not_borrow_positions():
    focus = Focus(obj="座椅", last_intent="seat.heating.on", positions=["副驾"],
                  control_targets=[{"command": "seat.heating.on", "positions": ["副驾"]}],
                  origin_exchange_id="x-old")
    history = [{"role": "assistant", "exchange_id": "x-old", "actions": ["seat.heating.on"]},
               {"role": "assistant", "exchange_id": "x-new", "actions": ["seat.heating.on"]}]
    out = augment_focus_with_execution(focus, history)
    assert out.last_intent == "seat.heating.on" and out.positions == []


def test_names_only_ledger_still_clears_unprovable_positions():
    """只有名字的旧轮次：取不到目标 ⇒ 位置为空，行为同修前（旧焦点的「副驾」不粘到新对象上）。"""
    focus = Focus(obj="座椅", positions=["副驾"], last_agent_id="vehicle-control")
    out = augment_focus_with_execution(focus, [{"actions": ["sunroof.open"]}])
    assert out.obj == "天窗" and out.positions == [] and out.last_agent_id == ""


def test_adjacency_break_clears_the_saved_targets_too():
    focus = Focus(last_intent="window.open", obj="车窗", positions=["副驾"],
                  control_targets=[{"command": "window.open", "positions": ["副驾"]}],
                  origin_exchange_id="x-control")
    history = [{"role": "assistant", "exchange_id": "x-control", "actions": ["window.open"]},
               {"role": "user", "exchange_id": "x-chat", "actions": []}]
    out = augment_focus_with_execution(focus, history)
    assert out is None or (not out.positions and not out.control_targets)


def test_build_context_reads_the_edge_signed_targets():
    targets = json.dumps([{"command": "window.open", "positions": ["副驾"]}], ensure_ascii=False)
    request = SimpleNamespace(
        text="关掉", session_id="s1", request_id="r1", is_confirmation=False, operation_id="",
        meta={"_edge_executed": "window.open", "_edge_executed_targets": targets,
              "_edge_previous_local_targets": targets},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))
    ctx = build_context(request)
    assert ctx.edge_executed_targets == [{"command": "window.open", "positions": ["副驾"]}]
    assert ctx.previous_local_targets == [{"command": "window.open", "positions": ["副驾"]}]
    assert "_edge_executed_targets" not in (ctx.prefs or {})


# ── 云侧执行的控制步：焦点记下这一步的目标 ──────────────────────────────────────────

def _ok(step_id):
    return StepResult(step_id=step_id, status=StepStatus.OK)


def test_extract_focus_records_what_each_control_step_targeted():
    plan = Plan(steps=[Step(id="s1", agent_id="edge-vehicle", intent="window.open",
                            slots={"positions": "副驾"})])
    focus = extract_focus(plan, [_ok("s1")])
    assert focus.positions == ["副驾"]
    assert focus.control_targets == [{"command": "window.open", "positions": ["副驾"]}]


def test_a_later_control_step_without_positions_does_not_inherit_the_earlier_ones():
    """修前只在有值时赋值：「打开副驾车窗，再开空调」之后焦点是空调 + 副驾 ⇒「关掉」成 `hvac.off {副驾}`。"""
    plan = Plan(steps=[Step(id="s1", agent_id="edge-vehicle", intent="window.open",
                            slots={"positions": "副驾"}),
                       Step(id="s2", agent_id="edge-vehicle", intent="hvac.on", slots={})])
    focus = extract_focus(plan, [_ok("s1"), _ok("s2")])
    assert focus.last_intent == "hvac.on" and focus.positions == []


def test_scan_positions_does_not_count_one_seat_twice():
    assert _scan_positions({"position": "副驾驶"}) == ["副驾驶"]
    assert _scan_positions({"positions": "主驾驶和副驾"}) == ["主驾驶", "副驾"]


# ── 「关掉」的确定性计划 ─────────────────────────────────────────────────────────

def _agent(*intents):
    caps = [SimpleNamespace(intent=i, slots=[], description="", examples=[], heavy=False,
                            require_confirm=False, verification=None) for i in intents]
    manifest = SimpleNamespace(
        agent_id="edge-vehicle", capabilities=caps, latency_budget_ms=800, kind="edge_fast",
        deployment="edge", requires_permissions=[], trust_level="system", context_scopes=[],
        route_hints=[], category="core")
    return SimpleNamespace(manifest=manifest, endpoint="edge://vehicle")


def _ellipsis(text, focus, *intents):
    from orchestrator.cloud import planning
    catalog = planning._assemble_capability_catalog([_agent(*intents)])
    ws = WorkingSet(catalog=list(catalog.visible_agents), focus=focus)
    return planning.PlanBuilder._focused_control_ellipsis_plan(text, ws, catalog)


@pytest.mark.parametrize("last_intent,target", [
    ("window.open", "window.close"), ("seat.heating.on", "seat.heating.off")])
def test_the_inverse_keeps_the_position(last_intent, target):
    plan = _ellipsis("关掉", Focus(last_intent=last_intent, positions=["副驾"]),
                     last_intent, target)
    assert [(s.intent, s.slots) for s in plan.steps] == [(target, {"positions": "副驾"})]


def test_two_positions_become_two_steps():
    plan = _ellipsis("关掉", Focus(last_intent="seat.heating.on", positions=["主驾", "副驾"]),
                     "seat.heating.on", "seat.heating.off")
    assert [(s.intent, s.slots) for s in plan.steps] == [
        ("seat.heating.off", {"positions": "主驾"}), ("seat.heating.off", {"positions": "副驾"})]
    assert len({s.id for s in plan.steps}) == 2


def test_no_position_stays_one_bare_step():
    plan = _ellipsis("不用了，关掉", Focus(last_intent="sunroof.open"),
                     "sunroof.open", "sunroof.close")
    assert [(s.intent, s.slots) for s in plan.steps] == [("sunroof.close", {})]


def test_only_positions_are_inherited_never_values_or_modes():
    focus = Focus(last_intent="window.open", positions=["副驾"], attr="开度",
                  control_targets=[{"command": "window.open", "positions": ["副驾"]}])
    plan = _ellipsis("关掉", focus, "window.open", "window.close")
    assert all(set(s.slots) == {"positions"} for s in plan.steps)

"""诉求账本（评审 F07 / §3.1 goal_id，W12，2026-09-20）。

模型把这句话里的每个肯定诉求按原话截成 `goals`，每个 step 标 `covers`；系统只做核对：
每一步都填了 covers、某条诉求不在任何一步里、且槽值也不替它作证 ⇒ 完成类 final 上如实补一句
「「X」这部分这次没有处理到」、出 `goal.uncovered`、终态记 partial。任何一环缺席都 fail-open。

⚠ 用例替被测系统提供的前提只有「模型交了什么账本」；漏承接的判定、话术、终态都由系统算。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud import planning
from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.engine import goal_gap
from orchestrator.cloud.models import Plan, PlanContext, Step
from orchestrator.cloud.planning import PlanBuilder, _parse_covers, _parse_goals, _submit_plan_tools

from tests.test_planning import MockAgent
from .test_engine_confirm import _make_engine, _req, _run
from .test_engine_outcome import _outcomes, _script_planner
from .test_obs_spans import _capture_spans


# ── 解析 ──────────────────────────────────────────────────────────────────

def test_parse_goals_keeps_verbatim_spans_bounded():
    assert _parse_goals(["先查瑞幸", " 再点生椰拿铁不加糖 ", "", "先查瑞幸", 3]) == ["先查瑞幸", "再点生椰拿铁不加糖"]
    assert _parse_goals("只有一条") == ["只有一条"]
    assert _parse_goals({"a": 1}) == []
    assert len(_parse_goals([f"g{i}" for i in range(10)])) == 6
    assert _parse_goals(["x" * 80])[0] == "x" * 40


def test_parse_covers_keeps_only_legal_indices():
    assert _parse_covers([1, 2, 2, 0, 9, "3", True, "x"], 3) == [1, 2, 3]
    assert _parse_covers(2, 3) == [2]
    assert _parse_covers("nope", 3) == []
    assert _parse_covers([1], 0) == []


def _agents():
    return [MockAgent("nearby", ["nearby.search"]),
            MockAgent("luckin", ["luckin.order"]),
            MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",))]


def _build(wire: dict, text: str = "先查瑞幸，再点生椰拿铁不加糖"):
    async def llm(messages):
        return json.dumps(wire, ensure_ascii=False)

    async def resolve(query, top_k=1):
        return []
    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), PlanContext(session_id="t")))


def test_wire_goals_and_covers_reach_the_plan():
    # cap_0001=chitchat.talk, cap_0002=luckin.order, cap_0003=nearby.search（按 agent_id 排序）
    plan = _build({"addressed": True, "goals": ["先查瑞幸", "再点生椰拿铁不加糖"], "steps": [
        {"id": "s1", "capability_ref": "cap_0003", "slots": {"keyword": "瑞幸"},
         "depends_on": [], "slot_refs": {}, "covers": [1]}]})
    assert plan.goals == ["先查瑞幸", "再点生椰拿铁不加糖"]
    assert [s.intent for s in plan.steps] == ["nearby.search"]
    assert plan.steps[0].covers == [1]


def test_covers_is_optional_and_unknown_step_keys_are_still_rejected():
    plan = _build({"addressed": True, "steps": [
        {"id": "s1", "capability_ref": "cap_0003", "slots": {"keyword": "瑞幸"},
         "depends_on": [], "slot_refs": {}}]})
    assert plan.steps and plan.steps[0].covers == [] and plan.goals == []
    bad = _build({"addressed": True, "steps": [
        {"id": "s1", "capability_ref": "cap_0003", "slots": {"keyword": "瑞幸"},
         "depends_on": [], "slot_refs": {}, "whatever": 1}]})
    assert [s.intent for s in bad.steps] == ["chitchat.talk"], "未知步键仍整份拒绝 → 兜底"


def test_toolcall_schema_exposes_goals_and_covers_only_when_enabled(monkeypatch):
    from orchestrator.cloud.planning import _assemble_capability_catalog
    catalog = _assemble_capability_catalog(_agents())
    monkeypatch.setenv("PLANNER_GOALS", "on")
    tools = _submit_plan_tools(catalog)
    params = tools["tools"][0]["function"]["parameters"]
    assert "goals" in params["properties"]
    step_props = params["properties"]["steps"]["items"]["properties"]
    assert "covers" in step_props and "covers" not in params["properties"]["steps"]["items"]["required"]
    assert "诉求账本" in planning._planner_system()
    monkeypatch.setenv("PLANNER_GOALS", "off")
    tools = _submit_plan_tools(catalog)
    params = tools["tools"][0]["function"]["parameters"]
    assert "goals" not in params["properties"]
    assert "covers" not in params["properties"]["steps"]["items"]["properties"]
    assert "诉求账本" not in planning._planner_system()


def test_plan_only_contract_accepts_goals():
    from orchestrator.cloud.planning import _trigger_plan_only_contract_violated
    from orchestrator.cloud.retry_policy import PlanAttemptState
    state = PlanAttemptState(attempt=1, wire_mode="toolcall", data={
        "complexity": "simple", "goal": "g", "addressed": True, "steps": [], "goals": ["a"]},
        parsed=None, plan_only_expected=True)
    assert _trigger_plan_only_contract_violated(state) is False


# ── goal_gap 判据 ───────────────────────────────────────────────────────────

def _plan(goals, steps):
    return Plan(steps=[Step(id=f"s{i}", agent_id="x", intent=it, slots=sl, covers=cv)
                       for i, (it, sl, cv) in enumerate(steps, 1)], goals=goals)


def test_gap_names_the_goal_no_step_covers():
    plan = _plan(["先查瑞幸", "再点生椰拿铁不加糖"], [("nearby.search", {"keyword": "瑞幸"}, [1])])
    assert goal_gap(plan, "先查瑞幸，再点生椰拿铁不加糖") == ["再点生椰拿铁不加糖"]


def test_gap_is_empty_when_every_goal_is_covered():
    plan = _plan(["先查瑞幸", "再点生椰拿铁不加糖"],
                 [("nearby.search", {"keyword": "瑞幸"}, [1]), ("luckin.order", {"item": "生椰拿铁"}, [2])])
    assert goal_gap(plan, "先查瑞幸，再点生椰拿铁不加糖") == []


def test_gap_fails_open_when_any_step_left_covers_empty():
    """槽值被转述（瑞幸→luckin）替不了证、covers 又有一步没填 ⇒ 模型没参与账本，不据此判漏。"""
    plan = _plan(["先查瑞幸", "再点生椰拿铁不加糖"],
                 [("nearby.search", {"keyword": "luckin"}, []), ("luckin.order", {"item": "latte"}, [2])])
    assert goal_gap(plan, "先查瑞幸，再点生椰拿铁不加糖") == []
    filled = _plan(["先查瑞幸", "再点生椰拿铁不加糖"],
                   [("nearby.search", {"keyword": "luckin"}, [2]), ("luckin.order", {"item": "latte"}, [2])])
    assert goal_gap(filled, "先查瑞幸，再点生椰拿铁不加糖") == ["先查瑞幸"], "都填了才据账本判"


def test_gap_is_silent_for_a_single_goal_or_no_goals():
    assert goal_gap(_plan(["查天气"], [("info.weather", {"city": "深圳"}, [])]), "查天气") == []
    assert goal_gap(_plan([], [("info.weather", {"city": "深圳"}, [])]), "查天气") == []


def test_slot_values_vouch_for_a_goal_the_model_forgot_to_mark():
    """模型漏标了一步实际负责的诉求：destination=深圳湾公园 替「导航去深圳湾公园」作证。"""
    plan = _plan(["导航去深圳湾公园", "晚上7点前到"],
                 [("navigation.navigate_to", {"destination": "深圳湾公园", "arrive_by": "19:00"}, [2])])
    assert goal_gap(plan, "导航去深圳湾公园，晚上7点前到") == []


def test_whole_utterance_or_passthrough_steps_never_leave_a_gap():
    text = "明天四点提醒我开会，三点半再提醒一次"
    plan = Plan(goals=["明天四点提醒我开会", "三点半再提醒一次"], steps=[
        Step(id="s1", agent_id="reminder", intent="reminder.create_batch", slots={}, covers=[1],
             whole_utterance=True)])
    assert goal_gap(plan, text) == []
    talk = _plan(["讲个笑话", "再讲个故事"], [("chitchat.talk", {"text": "讲个笑话，再讲个故事"}, [1])])
    assert goal_gap(talk, "讲个笑话，再讲个故事") == []


# ── engine 消费：完成类 final 补话术 + issue + partial ─────────────────────

def _gap_engine(covers_second: bool):
    engine, spy, _session = _make_engine()

    async def call_agent(endpoint, intent, slots, ctx, meta):
        return SimpleNamespace(status=0, speech="为您找到 3 家瑞幸。", follow_up="", actions=[],
                               ui_card=None, data={"items": [{"name": "瑞幸(科技园店)"}]},
                               missing_slots=[])
    engine.executor._dispatcher._call = call_agent
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="nearby", intent="nearby.search", slots={"keyword": "瑞幸"},
                    covers=[1, 2] if covers_second else [1])],
        raw_text=text, goals=["先查瑞幸", "再点生椰拿铁不加糖"]))
    return engine


def test_completed_turn_with_a_gap_tells_the_user_and_is_partial(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine = _gap_engine(covers_second=False)

    final = _run(engine, _req("先查瑞幸，再点生椰拿铁不加糖"))[-1]

    assert "「再点生椰拿铁不加糖」这部分这次没有处理到" in final.get("follow_up", "")
    assert final["speech"].startswith("为您找到")
    codes = [i["code"] for i in final.get("issues") or []]
    assert codes == ["goal.uncovered"] and final["issues"][0]["severity"] == "info"
    assert [o.get("kind") for o in _outcomes(spans)] == ["partial"]
    planning_span = next(kw["attrs"] for _, node, kw in spans if node == "cloud.planning")
    assert planning_span.get("goals_declared") == 2 and planning_span.get("goal_gap") == 1


def test_no_statement_when_the_model_says_both_goals_are_covered(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine = _gap_engine(covers_second=True)

    final = _run(engine, _req("先查瑞幸，再点生椰拿铁不加糖"))[-1]

    assert "没有处理到" not in final.get("follow_up", "")
    assert not final.get("issues")
    assert [o.get("kind") for o in _outcomes(spans)] == ["completed"]


def test_gap_statement_is_not_attached_to_a_suspended_final(monkeypatch):
    """挂起 final 没完成任何事，不说「这部分没处理到」（那一句只在完成类终态上）。"""
    _capture_spans(monkeypatch)
    engine, spy, _session = _make_engine()

    async def call_agent(endpoint, intent, slots, ctx, meta):
        return SimpleNamespace(status=2, speech="要找哪一带的？", follow_up="", actions=[],
                               ui_card=None, data={}, missing_slots=["location"])
    engine.executor._dispatcher._call = call_agent
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="nearby", intent="nearby.search", slots={"keyword": "瑞幸"}, covers=[1])],
        raw_text=text, goals=["先查瑞幸", "再点生椰拿铁不加糖"]))

    final = _run(engine, _req("先查瑞幸，再点生椰拿铁不加糖"))[-1]

    assert final.get("operation_id") and "没有处理到" not in (final.get("follow_up") or "")

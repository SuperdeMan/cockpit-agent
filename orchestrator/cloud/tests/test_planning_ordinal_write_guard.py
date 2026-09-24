"""裸序数长不出与所选那一项无关的写步（评审四轮 R4-02 收尾，2026-09-25）。

`2f8f92be` 真栈 RS33：「云岚国际中心」澄清卡 →「附近的咖啡店」列表 →「第二个」，规划器给出 `reminder.cancel {index: 2}`（声明为写、不需确认；
探针用户恰好没有提醒，否则第二条提醒被静默删掉）；历史上同一句还被规划成 `reminder.cancel {title: 第二个}`。「第 N 个」指的是最新那份候选的
第 N 项——写步要么点名那一项，要么是那份列表产生方自己的能力，否则去掉；只读步不管。一步不剩时**确定性收尾**：说出选中的是哪一项、
问要拿它做什么（`8528df0b` 真栈：交给兜底谈话时它拿着候选上下文编了一句「现在为您发起导航到库迪咖啡…」，什么都没执行）；选不中（焦点缺失 /
序号越界）才走兜底谈话。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import Focus, WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog
from orchestrator.cloud.reply_position import reply_position

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _write(agent, *intents):
    for cap in agent.manifest.capabilities:
        if cap.intent in intents:
            cap.effect = "write"
    return agent


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        _write(MockAgent("reminder", ["reminder.cancel", "reminder.list"]), "reminder.cancel"),
        _write(MockAgent("nearby", ["nearby.search", "nearby.detail", "nearby.order"],
                         require_confirm=("nearby.order",)), "nearby.order"),
        _write(MockAgent("navigation", ["navigation.navigate_to", "navigation.estimate"]),
               "navigation.navigate_to"),
    ]


_CAFES = [{"name": "NOWWA挪瓦咖啡(美宜佳深圳华富洋大厦店)", "lat": 22.54, "lng": 113.95},
          {"name": "库迪咖啡(海王银河科技大厦店)", "lat": 22.54, "lng": 113.95},
          {"name": "瑞幸咖啡(麻雀岭东区餐饮街店)", "lat": 22.55, "lng": 113.95}]


def _cafe_focus():
    return Focus(candidate_sets=[{"source_intent": "nearby.search", "agent_id": "nearby", "purpose": "list",
                                  "ts": time.time(), "is_fallback": False, "items": _CAFES}])


def _wire(*steps):
    catalog = _assemble_capability_catalog(_agents())
    return json.dumps({"addressed": True, "complexity": "simple", "steps": [
        {"id": f"s{i}", "capability_ref": catalog.pair_to_ref[(agent_id, intent)], "slots": slots,
         "depends_on": deps, "slot_refs": {}}
        for i, (agent_id, intent, slots, deps) in enumerate(
            [(s + ([],)) if len(s) == 3 else s for s in steps], 1)]}, ensure_ascii=False)


def _build(reply, text, focus=None):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    working_set = WorkingSet(catalog=_agents(), history=[
        {"role": "user", "text": "附近的咖啡店"},
        {"role": "assistant", "text": "为您找到 10 家咖啡厅，推荐：NOWWA挪瓦咖啡、库迪咖啡、瑞幸咖啡。"}], focus=focus)
    return asyncio.run(builder.build(text, working_set, PlanContext(session_id="t")))


def _intents(plan):
    return [step.intent for step in plan.steps]


def _asks_about_the_second_cafe(plan):
    assert plan.steps == [], _intents(plan)
    assert "_ordinal_write_blocked" in plan.plan_mode, plan.plan_mode
    assert plan.clarify is not None
    assert "库迪咖啡(海王银河科技大厦店)" in plan.clarify["question"], plan.clarify
    assert [o["label"] for o in plan.clarify["options"]] == ["看详情", "导航过去"], plan.clarify
    assert plan.clarify["options"][1]["send_text"] == "导航去库迪咖啡(海王银河科技大厦店)"


# ── 与所选那一项无关的写步 ⇒ 去掉 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("slots", [{"index": "2"}, {"title": "第二个"}])   # 真栈 `2f8f92be` RS33 / 历史 2026-09-22
def test_a_bare_ordinal_after_a_list_does_not_cancel_a_reminder(slots):
    plan = _build(_wire(("reminder", "reminder.cancel", slots)), "第二个", _cafe_focus())
    _asks_about_the_second_cafe(plan)


def test_the_voice_form_with_a_full_stop_is_the_same_reply():
    """真实 App 会话带句号上云（客户端整句锚定的改写没接住）。"""
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"})), "第二个。", _cafe_focus())
    _asks_about_the_second_cafe(plan)


def test_with_no_list_to_point_at_a_bare_ordinal_authorizes_no_write():
    """选不中（焦点缺失——没有列表时引擎在规划之前就确定性弃权了）⇒ 兜底谈话。"""
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"})), "第二个", Focus())
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)
    assert plan.clarify is None


def test_an_out_of_range_ordinal_authorizes_no_write():
    plan = _build(_wire(("navigation", "navigation.navigate_to", {"destination": "库迪咖啡(海王银河科技大厦店)"})),
                  "第九个", _cafe_focus())
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)


def test_only_the_unrelated_write_goes_and_the_read_stays():
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"}),
                        ("nearby", "nearby.detail", {"name": "库迪咖啡(海王银河科技大厦店)"})),
                  "第二个", _cafe_focus())
    assert _intents(plan) == ["nearby.detail"], _intents(plan)
    assert "_ordinal_write_trimmed" in plan.plan_mode, plan.plan_mode


def test_a_step_that_depends_on_a_dropped_write_goes_with_it():
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"}),
                        ("chitchat", "chitchat.talk", {"text": "说一下结果"}, ["s1"])),
                  "第二个", _cafe_focus())
    _asks_about_the_second_cafe(plan)            # 整份确定性收尾，不是留下一个依赖悬空的残步


def test_an_item_without_coordinates_is_asked_about_without_invented_options():
    focus = Focus(candidate_sets=[{"source_intent": "shop.menu", "agent_id": "shop", "purpose": "list",
                                   "ts": time.time(), "is_fallback": False,
                                   "items": [{"name": "拿铁"}, {"name": "美式"}]}])
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"})), "第二个", focus)
    assert plan.steps == [] and plan.clarify is not None
    assert "美式" in plan.clarify["question"] and plan.clarify["options"] == [], plan.clarify


# ── 对照：指向所选那一项的写 / 列表产生方自己的能力 / 只读 / 不是裸序数 ⇒ 照旧 ───────────────────

def test_navigating_to_the_selected_item_is_kept():
    plan = _build(_wire(("navigation", "navigation.navigate_to", {"destination": "库迪咖啡"})),
                  "第二个", _cafe_focus())
    assert _intents(plan) == ["navigation.navigate_to"], _intents(plan)


def test_a_waypoint_naming_the_selected_item_is_kept():
    """真实 App 会话 `app-wyi61a`：沿途咖啡列表之后「第二个。」⇒ 设途经点。"""
    plan = _build(_wire(("navigation", "navigation.navigate_to",
                         {"destination": "深圳国家工程实验室大楼", "waypoint": "库迪咖啡（海王银河科技大厦店）"})),
                  "第二个。", _cafe_focus())
    assert _intents(plan) == ["navigation.navigate_to"], _intents(plan)


def test_an_abbreviated_name_with_its_own_note_still_names_the_item():
    """「库迪(海王银河店)」：去掉括号注记后「库迪」落在「库迪咖啡」里——点名的是同一项。"""
    plan = _build(_wire(("navigation", "navigation.navigate_to", {"destination": "库迪(海王银河店)"})),
                  "第二个", _cafe_focus())
    assert _intents(plan) == ["navigation.navigate_to"], _intents(plan)


def test_the_list_producers_own_write_is_kept():
    plan = _build(_wire(("nearby", "nearby.order", {"poi_id": "2"})), "第二个", _cafe_focus())
    assert _intents(plan) == ["nearby.order"], _intents(plan)


def test_a_read_is_never_touched():
    plan = _build(_wire(("nearby", "nearby.detail", {"poi_id": "2"})), "第二个", _cafe_focus())
    assert _intents(plan) == ["nearby.detail"], _intents(plan)


def test_a_sentence_that_says_what_to_do_is_not_a_bare_ordinal():
    plan = _build(_wire(("reminder", "reminder.cancel", {"index": "2"})), "取消第二个提醒", _cafe_focus())
    assert _intents(plan) == ["reminder.cancel"], _intents(plan)


@pytest.mark.parametrize("text,index", [
    ("第二个", 2), ("第二个。", 2), ("第二家", 2), ("选第一个吧", 1), ("2", 2), ("二号方案", 2),
    ("第二天", None), ("导航去第二家", None), ("取消第二个提醒", None), ("好的", None)])
def test_reply_position_shapes(text, index):
    assert reply_position(text) == index


# ── 规划没交出能落到这一项上的东西（`976f174c` 真栈 RS33：落到「我听到了「第二个」，但没听清要拿它做什么」）──────────

def _catalog_ref(agent_id, intent):
    return _assemble_capability_catalog(_agents()).pair_to_ref[(agent_id, intent)]


@pytest.mark.parametrize("reply", [
    "这不是一个计划",                                                          # 两次都解析不了 ⇒ 技术失败
    json.dumps({"addressed": True, "complexity": "simple", "steps": []}),       # 零步
])
def test_an_unusable_plan_on_a_bare_ordinal_asks_about_the_selected_item(reply):
    plan = _build(reply, "第二个", _cafe_focus())
    _asks_about_the_second_cafe_unplanned(plan)


def test_a_talk_only_plan_on_a_bare_ordinal_asks_about_the_selected_item():
    reply = json.dumps({"addressed": True, "complexity": "simple", "steps": [
        {"id": "s1", "capability_ref": _catalog_ref("chitchat", "chitchat.talk"), "slots": {"text": "第二个"},
         "depends_on": [], "slot_refs": {}}]}, ensure_ascii=False)
    plan = _build(reply, "第二个", _cafe_focus())
    _asks_about_the_second_cafe_unplanned(plan)


def test_the_models_own_clarify_is_kept():
    reply = json.dumps({"addressed": True, "steps": [], "clarify": {
        "question": "你想怎么处理第二家库迪咖啡？",
        "options": [{"label": "看详情", "send_text": "看库迪咖啡(海王银河科技大厦店)的详情"},
                    {"label": "点单", "send_text": "在库迪咖啡(海王银河科技大厦店)点一杯"}]}}, ensure_ascii=False)
    plan = _build(reply, "第二个", _cafe_focus())
    assert plan.clarify is not None and plan.clarify["question"] == "你想怎么处理第二家库迪咖啡？", plan.clarify


def test_an_unusable_plan_with_nothing_to_select_is_left_alone():
    plan = _build("这不是一个计划", "第二个", Focus())
    assert "_ordinal_" not in (plan.plan_mode or ""), plan.plan_mode
    assert plan.clarify is None


def _asks_about_the_second_cafe_unplanned(plan):
    assert plan.steps == [], _intents(plan)
    assert "_ordinal_unplanned" in plan.plan_mode, plan.plan_mode
    assert plan.technical_failure is False and plan.clarify_wanted is False
    assert "库迪咖啡(海王银河科技大厦店)" in plan.clarify["question"], plan.clarify
    assert [o["label"] for o in plan.clarify["options"]] == ["看详情", "导航过去"], plan.clarify


def test_a_technical_failure_routed_to_a_non_talk_step_asks_about_the_selected_item():
    """技术失败的兜底也可能落在语义路由 top-1 的非谈话步上（不是只有兜底谈话一种）——同样问一句。"""
    from orchestrator.cloud.models import Plan, Step
    builder = PlanBuilder(llm_fn=None, registry_fn=None)
    plan = Plan(steps=[Step(id="s1", agent_id="nearby", intent="nearby.search", slots={"keyword": "第二个"})],
                raw_text="第二个", technical_failure=True)
    plan = builder._apply_ordinal_write_guard(plan, "第二个", WorkingSet(catalog=_agents(), focus=_cafe_focus()),
                                              _agents())
    _asks_about_the_second_cafe_unplanned(plan)

"""往后推的说法与没有框架词的「X 的话」：当场的端侧车控不做（2026-10-19，200×5 后续批 §23）。

取证：端侧离线复现「下雨的话把车窗关上」「等会儿再把车窗打开」「八点打开空调」「下班的时候打开空调」全被当场执行；送到云端，
规划器（线上 `015acd32`，只规划不执行，6 遍）「等会儿再把车窗打开」5 遍、「八点打开空调」3 遍、「晚上再开空调」3 遍当场派车控。
条件句（「X 的话」起句）走条件指令那一套：后件连同需确认的云侧步一起等 T2 判过前件。往后推的说法只拦当场的端侧车控——
提醒、需确认的场景这类「到时候再做」的安排照常（D03「车里温度低于20度的时候打开空调」建带条件的场景）。
组合句里被一截不往后推的分句点了名的车控照常（时间常常只属于其中一件）。
"""
from __future__ import annotations

import pytest

from orchestrator.cloud.models import Step
from orchestrator.cloud.planning import PlanBuilder


def _edge(intent: str, description: str, step_id: str = "e1") -> Step:
    return Step(id=step_id, agent_id="edge-vehicle", intent=intent, deployment="edge", kind="edge_fast",
                capability_description=description)


def _cloud(intent: str, description: str, *, confirm: bool = False, step_id: str = "c1") -> Step:
    return Step(id=step_id, agent_id="cloud-agent", intent=intent, deployment="cloud", kind="agent",
                require_confirm=confirm, capability_description=description)


def _intents(steps) -> list[str]:
    return [step.intent for step in steps]


def _filter(steps, origin):
    return PlanBuilder._filter_safety_origin_side_effect_steps(steps, origin)


# ── 没有框架词的「X 的话」：同「如果 X 的话」，后件等前件判过 ─────────────────────────────────────────────

@pytest.mark.parametrize("origin", ["深圳今天下雪的话，就把空调打开", "下雪的话帮我把空调打开", "冷的话开一下空调"])
def test_a_bare_if_conditional_waits_like_a_framed_one(origin):
    kept, blocked = _filter([_cloud("info.weather", "查询天气"), _edge("hvac.on", "打开空调"),
                             _cloud("scene.create", "创建场景", confirm=True, step_id="c2")], origin)
    assert _intents(kept) == ["info.weather"]
    assert _intents(blocked) == ["hvac.on", "scene.create"]


@pytest.mark.parametrize("origin", ["可以的话把空调打开", "方便的话帮我把空调打开", "这样的话就把空调打开吧", "那样的话把空调打开"])
def test_politeness_and_an_already_given_condition_still_execute(origin):
    kept, blocked = _filter([_edge("hvac.on", "打开空调")], origin)
    assert _intents(kept) == ["hvac.on"] and blocked == []


# ── 往后推的说法：当场的端侧车控不做，到时候再做的安排照常 ─────────────────────────────────────────────

@pytest.mark.parametrize("origin", [
    "八点打开空调", "明天早上八点打开空调", "明天早上八点，帮我打开空调", "晚上再开空调", "明天再开空调",
    "等到了公司再打开空调", "到了公司再开空调", "一会儿到家了再开空调", "十分钟后再打开空调", "过半小时再开空调",
    "下班的时候打开空调", "到家的时候打开空调",
])
def test_a_deferred_instruction_does_not_run_its_vehicle_action_now(origin):
    kept, blocked = _filter([_edge("hvac.on", "打开空调"), _cloud("reminder.create", "创建提醒")], origin)
    assert _intents(blocked) == ["hvac.on"]
    assert _intents(kept) == ["reminder.create"]


def test_the_window_variant_that_the_planner_ran_now_five_times_in_six():
    kept, blocked = _filter([_edge("window.open", "打开车窗")], "等会儿再把车窗打开")
    assert kept == [] and _intents(blocked) == ["window.open"]


def test_a_scene_with_a_trigger_is_the_right_place_for_a_future_condition():
    """D03（用户 2026-10-16 裁决建成带条件的场景）：场景要确认保存，照常；当场开空调不做。"""
    kept, blocked = _filter([_cloud("scene.create", "创建场景", confirm=True), _edge("hvac.on", "打开空调")],
                            "车里温度低于20度的时候打开空调")
    assert _intents(kept) == ["scene.create"] and _intents(blocked) == ["hvac.on"]


@pytest.mark.parametrize(("origin", "steps", "kept_intents", "blocked_intents"), [
    ("关上车窗，打开空调，查下明天天气，再提醒我七点起床",
     [_edge("window.close", "关闭车窗"), _edge("hvac.on", "打开空调", "e2"), _cloud("reminder.create", "创建提醒")],
     ["window.close", "hvac.on", "reminder.create"], []),
    ("提醒我七点起床，查下明天天气，打开空调，关上车窗",
     [_cloud("reminder.create", "创建提醒"), _edge("hvac.on", "打开空调"), _edge("window.close", "关闭车窗", "e2")],
     ["reminder.create", "hvac.on", "window.close"], []),
    ("音量调小一点，提醒我八点开会",
     [_edge("volume.dec", "调小音量"), _cloud("reminder.create", "创建提醒")], ["volume.dec", "reminder.create"], []),
    ("先开空调，十分钟后再关",
     [_edge("hvac.on", "打开空调"), _edge("hvac.off", "关闭空调", "e2")], ["hvac.on"], ["hvac.off"]),
])
def test_in_a_compound_only_what_the_deferred_part_names_waits(origin, steps, kept_intents, blocked_intents):
    kept, blocked = _filter(steps, origin)
    assert _intents(kept) == kept_intents and _intents(blocked) == blocked_intents


@pytest.mark.parametrize("origin", ["打开空调", "先关窗再开空调", "空调关了再开一下", "把空调调到二十六点五度", "空调调高一点",
                                    "播放明天会更好"])
def test_plain_and_sequenced_instructions_still_execute(origin):
    """「空调关了再开一下」「先关窗再开空调」是当下依次做的两件事（规划器 6 / 6 排对）；小数、「一点」、名字里的日子都不是时间。"""
    steps = [_edge("hvac.off", "关闭空调"), _edge("hvac.on", "打开空调", "e2"), _edge("window.close", "关闭车窗", "e3"),
             _edge("hvac.set", "设置空调温度", "e4"), _edge("media.play", "播放音乐", "e5")]
    kept, blocked = _filter(steps, origin)
    assert blocked == [] and len(kept) == len(steps)


def test_the_guard_logs_which_reason_dropped_the_steps(caplog):
    plan = PlanBuilder.__new__(PlanBuilder)
    from orchestrator.cloud.models import Plan
    built = Plan(steps=[_edge("hvac.on", "打开空调")], goal="八点打开空调")
    with caplog.at_level("WARNING"):
        out = PlanBuilder._apply_question_side_effect_guard(plan, built, "八点打开空调", agents=[])
    assert "Deferred instruction planned into side-effecting step(s) ['hvac.on']" in caplog.text
    assert out.steps == [] or all(step.intent != "hvac.on" for step in out.steps)
    assert (out.plan_mode or "").endswith("_question_write_blocked")


def _reminder_agent():
    """真实的提醒 manifest：预解析步的元数据（效果、确认、能力描述）都从它来。"""
    from pathlib import Path
    from types import SimpleNamespace
    from agents._sdk.manifest import load_manifest
    root = Path(__file__).resolve().parents[3]
    return SimpleNamespace(endpoint="reminder:50051",
                           manifest=load_manifest(str(root / "agents" / "reminder" / "manifest.yaml")))


@pytest.mark.parametrize(("origin", "send"), [("八点打开空调", "提醒我八点打开空调"),
                                              ("等会儿再把车窗打开", "提醒我等会儿再把车窗打开")])
def test_a_deferred_instruction_blocked_empty_ends_with_a_fixed_offer_not_free_talk(origin, send):
    """2026-10-19 复测：拦光后交兜底谈话 6 遍 5 遍答得不像样（空头承诺被剥后剩半截话、转去检索、「没听清」）⇒ 确定性收尾。

    选项带预解析的建提醒步：只带话术、点了再重新规划时又会排成当场开窗、被拦光后答「没听清」（§23.7）。"""
    from orchestrator.cloud.models import Plan, Step
    built = Plan(steps=[_edge("window.open" if "车窗" in origin else "hvac.on", "打开")], goal=origin, technical_failure=True)
    out = PlanBuilder._apply_question_side_effect_guard(PlanBuilder.__new__(PlanBuilder), built, origin,
                                                        agents=[_reminder_agent()])
    assert out.steps == [] and not out.technical_failure
    assert out.clarify["question"] == f"「{origin}」要到时候再做，我现在先不做。要我到时候提醒你吗？"
    [option] = out.clarify["options"]
    assert option["label"] == "到时候提醒我" and option["send_text"] == send
    step = Step(**option["step"])                 # engine 点选时原样还原、零 LLM 执行（W10）
    assert (step.agent_id, step.intent, step.slots) == ("reminder", "reminder.create", {})
    assert step.effect == "write" and not step.require_confirm


def test_without_a_reminder_capability_the_offer_only_says_not_now():
    """本请求目录里没有建提醒（没有提醒权限）⇒ 不出做不到的提议。"""
    from orchestrator.cloud.models import Plan
    built = Plan(steps=[_edge("hvac.on", "打开空调")], goal="八点打开空调")
    out = PlanBuilder._apply_question_side_effect_guard(PlanBuilder.__new__(PlanBuilder), built, "八点打开空调", agents=[])
    assert out.steps == [] and out.clarify == {"question": "「八点打开空调」要到时候再做，我现在先不做。", "options": []}


def test_a_question_blocked_empty_still_goes_to_the_talk_fallback():
    """问句被拦光照旧交兜底谈话答一句（「X灯亮了怎么办」最需要一个回答），确定性收尾只给往后推的说法。"""
    from orchestrator.cloud.models import Plan
    built = Plan(steps=[_edge("hvac.on", "打开空调")], goal="空调要开吗")
    out = PlanBuilder._apply_question_side_effect_guard(PlanBuilder.__new__(PlanBuilder), built, "空调要开吗？", agents=[])
    assert out.clarify is None

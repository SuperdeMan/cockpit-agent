"""问句闸按步骤归属到分句（评审四轮 R4-03，2026-09-24）。

修前 `_question_side_effect_steps` 对**整句**跑一次 `is_non_directive_question`，成立就删掉全部端侧写 / 需确认步：
「打开后备箱，再告诉我空调有哪些模式」整句判成列举问 ⇒ `trunk.open` 被删、确认卡不出（真栈 `ecbeed28` RS35 0/3，只剩 manual-rag
「手册里没有查到」）。这句话不是「完全不应执行的问句」，是「执行 + 询问」。

判据（`step_grounding`，唯一实现）：拆得开「提问分句 + 以指令起句的分句」时，每一步看点名它的是哪一类分句——指令分句点得比任何提问
分句都准才保留；点名不了 / 平手照旧拦。拆不开、全是提问、带假设 / 条件框架的句子行为逐字不变。需确认的步保留下来照旧走确认；
确认续接的安全原点复核走同一个函数（能力描述随挂起持久化）。
"""
from __future__ import annotations

import pytest

from orchestrator.cloud.models import Step, step_record
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.step_grounding import naming_score, object_core, opens_as_instruction


def _edge(intent, description, slots=None, *, confirm=False):
    return Step(id=intent, agent_id="edge-vehicle", intent=intent, slots=dict(slots or {}),
                deployment="edge", kind="edge_fast", require_confirm=confirm,
                capability_description=description)


TRUNK = ("trunk.open", "打开后备箱")
HVAC_ON = ("hvac.on", "打开空调")
HVAC_OFF = ("hvac.off", "关闭空调")
WINDOW = ("window.open", "打开车窗")


def _blocked(text, *steps):
    return [s.intent for s in PlanBuilder._question_side_effect_steps(list(steps), text)]


# ── 修前反例：显式动作不再被整句闸删掉 ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "打开后备箱，再告诉我空调有哪些模式",          # 评审原句
    "打开后备箱，然后告诉我空调有哪些模式",
    "打开后备箱好吗，再告诉我空调有哪些模式",       # 礼貌尾：前一分句仍是请求
    "帮我打开后备箱，顺便说说空调有哪些模式",
    "打开后备箱。空调有哪些模式？",                 # 句号分句
])
def test_the_explicit_trunk_request_survives_the_question_half(text):
    assert _blocked(text, _edge(*TRUNK, confirm=True)) == [], text


def test_the_step_invented_from_the_question_half_is_still_blocked():
    trunk, hvac = _edge(*TRUNK, confirm=True), _edge(*HVAC_ON)
    assert _blocked("打开后备箱，再告诉我空调有哪些模式", trunk, hvac) == ["hvac.on"]


def test_the_window_named_by_the_instruction_with_its_seat_survives():
    window = _edge(*WINDOW, {"positions": "副驾"})
    assert _blocked("请告诉我空调怎么使用，再打开副驾车窗", window) == []


def test_a_tie_between_an_instruction_and_a_question_is_not_enough():
    """「关闭空调，明天天气怎么样呢」：`hvac.off` 由指令分句点名（连动词），编出来的 `hvac.on` 两边都只点到「空调」——平手照拦。"""
    off, on = _edge(*HVAC_OFF), _edge(*HVAC_ON)
    assert _blocked("关闭空调，明天天气怎么样呢", off, on) == ["hvac.on"]
    assert _blocked("关闭空调，再告诉我空调有哪些模式", off, on) == ["hvac.on"]


def test_a_genuine_tie_without_a_direction_word_is_blocked():
    """两边点到的字一样多（都只有「香氛」）、又没有开关方向可判：说不清它依据哪一句 ⇒ 照拦。"""
    scent = _edge("fragrance.set", "调节香氛")
    assert _blocked("设置香氛，再告诉我香氛有哪些模式", scent) == ["fragrance.set"]


def test_the_same_object_in_both_halves_goes_to_the_instruction_that_names_its_verb():
    assert _blocked("打开空调，再告诉我空调有哪些模式", _edge(*HVAC_ON)) == []


# ── 保持修前行为的边界 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "后备箱有哪些打开方式",                          # 单分句的问句
    "空调有哪些模式，后备箱又有哪些功能",             # 全是提问
    "如果下雨，就打开后备箱",                         # 条件：后半句取决于前半句，不拆
    "要是下雨了，打开后备箱会进水吗",                 # 假设框架
    "别打开后备箱，告诉我后备箱怎么开",               # 否定分句不是指令
    "不是打开后备箱，是告诉我后备箱怎么开",           # 纠正框架不以指令起句
    "先不打开后备箱，说说后备箱怎么开",
    "打开后备箱再告诉我空调有哪些模式",               # 无标点且只用「再」连接：拆不开，按整句拦（保守一侧）
])
def test_questions_that_are_not_ask_plus_act_keep_the_old_behaviour(text):
    assert _blocked(text, _edge(*TRUNK, confirm=True)) == ["trunk.open"], text


def test_an_utterance_that_is_not_a_question_is_untouched():
    """反向语序整句不判问句（列举问之后另起的分句带操作动词），本来就不拦——对照成立（修前真栈 3/3 出确认卡）。"""
    assert _blocked("告诉我空调有哪些模式，然后打开后备箱", _edge(*TRUNK, confirm=True)) == []


def test_a_step_without_a_description_cannot_be_grounded_and_stays_blocked():
    bare = Step(id="s1", agent_id="edge-vehicle", intent="trunk.open", deployment="edge",
                kind="edge_fast", require_confirm=True)
    assert _blocked("打开后备箱，再告诉我空调有哪些模式", bare) == ["trunk.open"]


# ── 确认续接：安全原点复核走同一个函数，描述随挂起持久化 ───────────────────────────────────

def test_a_restored_trunk_step_is_still_grounded_in_its_origin():
    restored = Step(**step_record(_edge(*TRUNK, confirm=True)))
    assert restored.capability_description == "打开后备箱"
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
        [restored], "打开后备箱，再告诉我空调有哪些模式")
    assert [s.intent for s in kept] == ["trunk.open"] and blocked == []


def test_a_legacy_restored_step_without_a_description_is_blocked_as_before():
    record = step_record(_edge(*TRUNK, confirm=True))
    record.pop("capability_description")
    kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
        [Step(**record)], "打开后备箱，再告诉我空调有哪些模式")
    assert kept == [] and [s.intent for s in blocked] == ["trunk.open"]


# ── 判据本体 ───────────────────────────────────────────────────────────────

def test_object_core_keeps_nouns_that_contain_operation_characters():
    """单字「调 / 开 / 关」不进操作词表：「空调」的「调」不能被挖掉（追加批 E / F 两次栽在这里）。"""
    assert object_core("打开空调") == "空调"
    assert object_core("调高空调温度") == "空调温度"
    assert object_core("关闭车窗") == "车窗"
    assert object_core("停车缴费（**真的把钱付出去**，涉及支付，需二次确认）") == "停车缴费"


def test_naming_needs_the_object_or_a_slot_value():
    assert naming_score("打开后备箱", "打开空调") == 0          # 只共享动词不算点名
    assert naming_score("再告诉我空调有哪些模式", "打开空调") > 0
    assert naming_score("打开空调", "打开空调") > naming_score("再告诉我空调有哪些模式", "打开空调")
    assert naming_score("导航去深圳湾公园", "", {"destination": "深圳湾公园"}) > 0


@pytest.mark.parametrize("clause, opens", [
    ("打开后备箱", True), ("再打开副驾车窗", True), ("然后打开后备箱", True), ("帮我把车窗关上", True),
    ("请关闭空调", True), ("不是打开后备箱", False), ("先不打开后备箱", False), ("空调开到26度", False),
    ("告诉我空调有哪些模式", False)])
def test_opens_as_instruction(clause, opens):
    assert opens_as_instruction(clause) is opens, clause


# ── 引擎端到端：确认卡出得来，确认后执行 ─────────────────────────────────────────────

def test_the_mixed_request_asks_to_confirm_the_trunk_and_executes_it_after_confirm():
    from orchestrator.cloud.tests.test_engine_ack_attribution import _make, _req, _run
    engine, spy, session = _make()
    first = _run(engine, _req("打开后备箱，再告诉我空调有哪些模式"))[-1]
    assert first.get("need_confirm") and first.get("operation_id"), first.get("speech")
    final = _run(engine, _req("确认"))[-1]
    assert spy.confirmed("trunk.open") == 1, final.get("speech")
    assert first["operation_id"] in final["closed_operation_ids"]


def test_direction_words_decide_which_way_a_clause_points():
    from orchestrator.cloud.step_grounding import inverts_stated_direction
    assert inverts_stated_direction("关闭空调", _edge(*HVAC_ON)) is True
    assert inverts_stated_direction("打开空调", _edge(*HVAC_OFF)) is True
    assert inverts_stated_direction("打开空调", _edge(*HVAC_ON)) is False
    assert inverts_stated_direction("空调有哪些模式", _edge(*HVAC_ON)) is False     # 没说方向不判

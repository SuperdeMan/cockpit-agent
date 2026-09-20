"""`runtime/session_constraints` 的两向断言（C12-B）。

三件事要各自钉住：**说了忌口**、**明确改口**、**这句话没提**——第三种必须写成
「不写键」而不是 `False`，否则跨轮合并会把「没提」读成「说了要辣」，
上一轮的约束一句无关的话就被抹掉（同 `day_offset_of` 那条：认不出与说的是今天
必须分得开）。
"""
from __future__ import annotations

import pytest

from runtime.session_constraints import constraints_in, merge_constraints


@pytest.mark.parametrize("text", [
    "我不吃辣",
    "不要太辣",
    "别太辣",
    "少辣一点",
    "想吃点清淡的",
    "不太能吃辣",
])
def test_dietary_avoidance_is_recorded(text):
    assert constraints_in(text) == {"no_spicy": True}


@pytest.mark.parametrize("text", ["今天想吃辣的", "来点辣的", "越辣越好"])
def test_explicit_reversal_is_recorded_as_false_not_as_absence(text):
    """改口是事实，不是「没说」——它必须能覆盖上一轮的忌口。"""
    assert constraints_in(text) == {"no_spicy": False}


def test_negation_wins_over_the_affirmative_substring():
    """「不想吃辣」里也含「想吃辣」——**分支序就是语义**（N9 那条的同族）。"""
    assert constraints_in("不想吃辣") == {"no_spicy": True}


def test_queue_avoidance_and_combination():
    assert constraints_in("也不想排长队") == {"no_queue": True}
    assert constraints_in("我不吃辣，也不想排长队") == {
        "no_spicy": True, "no_queue": True}


@pytest.mark.parametrize("text", [
    "", "推荐附近适合晚饭的地方", "今天深圳天气怎么样", "特别辣的那家在哪",
])
def test_sentences_without_a_stated_preference_write_no_key(text):
    """没提到就一个键都不写。⚠「**特别辣**」不是忌口——裸「别」会吃掉它，
    词表里的 lookbehind 就是为这条负例加的。"""
    assert constraints_in(text) == {}


def test_merge_keeps_what_was_not_mentioned_and_lets_the_newer_turn_win():
    previous = {"no_spicy": True, "no_queue": True}
    assert merge_constraints(previous, {}) == previous          # 普通轮不抹
    assert merge_constraints(previous, {"no_spicy": False}) == {
        "no_spicy": False, "no_queue": True}                    # 改口只覆盖那一维
    assert merge_constraints(None, {"no_spicy": True}) == {"no_spicy": True}
    # 不改入参（调用方拿的是 Redis 里那份的副本）
    assert previous == {"no_spicy": True, "no_queue": True}


# ── 评审 2026-09-19 F05 / W03：改口、时态、主体、撤销 ─────────────────────

@pytest.mark.parametrize("text", [
    "今天可以排队，等一会儿没关系",
    "排队也行",
    "不介意排队",
    "不怕排队的",
    "等位也可以",
    "排一会儿队没问题",
])
def test_queue_acceptance_is_a_reversal_not_an_absence(text):
    """评审复算：先「不想排队」再「今天可以排队」仍 `no_queue=True`——撤销通道此前不存在。"""
    assert constraints_in(text) == {"no_queue": False}
    assert merge_constraints({"no_queue": True}, constraints_in(text)) == {"no_queue": False}


def test_past_tense_report_is_not_a_current_constraint():
    """「之前不吃辣，今天想吃辣」——前半句是转述过去，整句否定优先曾把它压成 `no_spicy=True`。"""
    assert constraints_in("之前不吃辣，今天想吃辣") == {"no_spicy": False}
    assert constraints_in("以前不爱排队的") == {}
    # 当前框架在场时照常算数（「这次还是不吃辣」）
    assert constraints_in("这次还是不吃辣") == {"no_spicy": True}


def test_a_companions_constraint_never_overwrites_the_speakers_own():
    """「我不吃辣」和「同行的人想吃辣」不能互相覆盖：别人的约束记在 `others` 下。"""
    assert constraints_in("同行的人想吃辣") == {"others": {"no_spicy": False}}
    assert constraints_in("我朋友不吃辣") == {"others": {"no_spicy": True}}
    merged = merge_constraints({"no_spicy": True}, constraints_in("同行的人想吃辣"))
    assert merged == {"no_spicy": True, "others": {"no_spicy": False}}
    # 「我们都不吃辣」是说话人也在内
    assert constraints_in("我们都不吃辣") == {"no_spicy": True}


@pytest.mark.parametrize("text", ["辣不辣无所谓", "不用管辣不辣了", "排不排队都行"])
def test_waiving_a_constraint_deletes_it(text):
    stated = constraints_in(text)
    key = "no_spicy" if "辣" in text else "no_queue"
    assert stated == {key: None}
    assert merge_constraints({"no_spicy": True, "no_queue": True}, stated) == {
        k: True for k in ("no_spicy", "no_queue") if k != key}


def test_merge_never_writes_a_none_value():
    assert merge_constraints({}, {"no_spicy": None}) == {}
    assert merge_constraints({"others": {"no_spicy": True}},
                             {"others": {"no_spicy": None}}) == {}


# ── 批 5 W18-b：「我今天说过不吃辣吗」是问，不是陈述 ────────────────────────────

from runtime.session_constraints import (constraint_recall_answer,  # noqa: E402
                                         is_constraint_recall_question,
                                         is_pure_constraint_statement)


@pytest.mark.parametrize("text", [
    "你还记得我不吃辣吗",
    "我今天说过不吃辣吗",
    "我刚才是不是说过不想排队",
    "我有没有跟你说过不吃辣",
    "记得我说过可以排队吗",
])
def test_a_question_about_a_constraint_is_not_a_statement(text):
    """此前「你还记得我不吃辣吗」被当成纯陈述登记成 `no_spicy=True`——一句问话改写了事实。"""
    assert not is_pure_constraint_statement(text), text
    assert is_constraint_recall_question(text), text


@pytest.mark.parametrize("text", [
    "我不吃辣",
    "今天吃清淡一点",
    "我不想排队，不吃辣",
    "帮我找家不辣的餐厅",      # 请求，不是回问
    "附近有没有不辣的馆子",    # 新检索问句：问的是店，不是自己说过什么
    "你还记得我的车牌号吗",    # 记忆问句，但没谈口味 / 排队
])
def test_constraint_recall_question_is_narrow(text):
    assert not is_constraint_recall_question(text), text


def test_a_recall_question_registers_nothing_but_a_constrained_request_still_does():
    """问自己说过什么 ⇒ 不登记；带约束的请求 / 新检索 ⇒ 照常登记（nearby 当轮要读它）。"""
    assert constraints_in("你还记得我不吃辣吗") == {}
    assert constraints_in("我今天说过不想排队吗") == {}
    assert constraints_in("帮我找家不辣的餐厅") == {"no_spicy": True}
    assert constraints_in("附近有没有不辣的馆子") == {"no_spicy": True}


@pytest.mark.parametrize("text", [
    "帮我找家不辣的餐厅",
    "附近有没有不辣的馆子",
    "推荐个清淡点的",
    "找个不用排队的地方吃饭",
    "哪里有不辣的火锅",
])
def test_a_request_carrying_a_constraint_is_not_a_pure_statement(text):
    """生产缺陷（W13 落地后）：这些句子每个分句都命中「不辣」，被当成纯陈述 ⇒ 答「好的，这次不吃辣」、
    零搜索。陈述里不能带请求。"""
    assert not is_pure_constraint_statement(text), text


@pytest.mark.parametrize("text", ["我不吃辣", "今天吃清淡一点", "我不吃辣，也不想排长队",
                                  "之前不吃辣，今天想吃辣", "排不排队都行"])
def test_pure_statements_are_still_pure(text):
    assert is_pure_constraint_statement(text), text


def test_constraint_recall_answer_reads_the_projection_verbatim():
    assert constraint_recall_answer({"no_spicy": True, "no_queue": False}) == \
        "您这次说过：不吃辣、可以排队。找地方的时候我按这个来。"
    assert constraint_recall_answer({"no_queue": True}) == \
        "您这次说过：不想排队。找地方的时候我按这个来。"
    assert constraint_recall_answer({}) == ""
    assert constraint_recall_answer({"others": {"no_spicy": True}}) == ""

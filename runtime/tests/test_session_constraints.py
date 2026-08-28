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

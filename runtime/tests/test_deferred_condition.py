"""One deferred-condition rule for edge and cloud."""
from __future__ import annotations

import pytest

from runtime.deferred_condition import (COMPLETE_DEFERRED_CONDITION_RE, DEFERRED_CONDITION_RE,
                                        is_conditional_instruction, is_deferred_instruction)


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开", "要是后排有人就把车窗关上", "温度低于20度时打开空调",
    "电量低于20%的时候打开节能模式", "车速超过60就关天窗", "打开空调，要是太冷再关掉",
    "如果冷的话打开座椅加热", "看看电量够不够开到杭州，不够就找个充电站", "if it rains then close the windows",
])
def test_conditions_are_deferred(text):
    assert is_deferred_instruction(text)


@pytest.mark.parametrize("text", ["打开空调", "帮我打开车窗和空调", "把温度调到20度", "车窗关上", "", None])
def test_plain_instructions_are_not(text):
    assert not is_deferred_instruction(text)


# ── 条件指令（2026-10-12，核心旅程 D04）：以假设框架起句、说出真条件和后件 ──────────────────────────────

@pytest.mark.parametrize("text", [
    "如果深圳今天下雪，就帮我把空调打开",       # D04：带「帮我」，整句不算提问
    "如果深圳今天下雪，请把空调打开",
    "如果深圳今天下雪的话，帮我打开空调",       # 「的话」后跟逗号
    "如果深圳今天下雪，帮我把空调打开",         # 没有「就」
    "要是外面下雨，就帮我把车窗关上",
    "若深圳今天下雪，就把空调打开",
    "万一下雨了，帮我把天窗关上",
    "假如电量低于百分之二十，就帮我打开节能模式",
    "那如果下雨的话就帮我关窗",
    "如果深圳今天不下雪，就把空调打开",         # 不带「帮我」的也是（整句另按提问拦）
])
def test_a_leading_condition_with_a_consequent_is_a_conditional_instruction(text):
    assert is_conditional_instruction(text)


@pytest.mark.parametrize("text", [
    "如果可以的话，帮我把空调打开",   # 客气话不是条件
    "如果方便的话帮我开一下窗",       # 「方便」里的「便」不是连接词
    "要是你能的话就把空调打开吧",
    "如果你能把空调打开就好了",       # 后件只是「就好了」
    "打开空调，要是太冷再关掉",       # 条件不在句首：前一半是当下就要做的事
    "打开空调，如果太冷了就关掉",
    "若干个车窗打开",                 # 「若」不是框架词
    "只要打开主驾车窗就行",
    "车里温度低于20度的时候打开空调", # 等将来的条件另有处理（D03），不在此列
    "帮我把空调打开", "如果可以", "", None,
])
def test_politeness_later_conditions_and_plain_requests_are_not(text):
    assert not is_conditional_instruction(text)


def test_the_planner_uses_this_rule_and_no_copy():
    from orchestrator.cloud import planning
    assert planning._DEFERRED_CONDITION_RE is DEFERRED_CONDITION_RE
    assert planning._COMPLETE_DEFERRED_CONDITION_RE is COMPLETE_DEFERRED_CONDITION_RE

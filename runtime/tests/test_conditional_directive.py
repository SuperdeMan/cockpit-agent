"""条件指令判据（`question_shape.is_conditional_directive`，2026-10-12）。

「如果 / 要是 … 就 + 指令」：条件框架 + 后件是给助手的指令。只给「条件已经查过之后」用（T2 再规划、恢复挂起）。
后件是提问的仍是提问；没有完整「如果…就」的（假设提问、等将来的条件）不在此列。
"""
from __future__ import annotations

import pytest

from runtime.question_shape import is_conditional_directive, is_non_directive_question


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开",   # CA2-10 ② / 核心旅程 D01
    "如果深圳今天下雪，就把空调打开",     # D02：条件不成立时由再规划判、不由这里判
    "要是降温了就把座椅加热打开",
    "如果明天下雨，就提醒我带伞",
    "查一下明天深圳会不会下雨，要是下雨就提醒我明早带伞",
    "如果深圳今天不下雪，就导航去深圳湾公园",
    "假如电量低于百分之二十，就帮我找个充电站",
    "如果前面堵车的话，那就换一条路线",
])
def test_condition_plus_instruction_is_a_conditional_directive(text):
    assert is_conditional_directive(text)


@pytest.mark.parametrize("text", [
    "如果你能去旅行，你想去哪里",          # 假设提问：没有「就」，后件也是问句
    "如果开空调的话会不会更费电",          # 后件是能力 / 推测问句
    "如果下雨就关窗吗",                    # 后件带疑问尾
    "要是下雨，车窗要不要关",              # 没有完整的「如果…就」
    "如果我开空调会怎么样",
    "车里温度低于20度的时候打开空调",      # 等将来的条件：现在查不了，不在此列（D03）
    "把空调打开",                          # 没有条件
    "今天下雪吗",
    "",
])
def test_questions_and_non_conditionals_are_not(text):
    assert not is_conditional_directive(text)


def test_the_whole_sentence_is_still_a_question_for_the_first_round():
    """首轮的问句闸靠的是整句判提问：条件还没查，后件不许执行——这一侧逐字不变。"""
    assert is_non_directive_question("如果深圳今天不下雪，就把空调打开")

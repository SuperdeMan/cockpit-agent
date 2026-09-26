"""定义问不许被端侧执行，也不许答成读数（评审四轮待办，2026-09-26）。

问句判据（`runtime.question_shape`）修前不认「X 是什么 / 什么是 X / X 是什么意思」——出口的写操作问句闸看不见它们。
语料 + collector 全量 A/B 读出的修前行为（9 句，零误伤）：

- 「仪表盘上那个红色感叹号是什么意思」「仪表上有个小人拿雨伞的图标是什么意思」执行成打开仪表设置；
- 「说明书里「打开后备箱」这一节讲的是什么」解成 `trunk.open`；「露营模式是什么意思」**激活了露营场景**；
- 「限速提醒是什么意思」开了限速提醒，「屏幕顶上冒出一个斜线小人是啥情况」开了屏幕；
- 「胎压报警灯亮了是什么意思」本地秒回「胎压正常」（collector 真实一轮）——问的是那盏灯的意思，端侧只有读数。

写操作由出口第一道问句闸挡（判据进了 `is_non_directive_question`，云侧问句闸同一份）；只读查询由第五维挡
（`asks_for_explanation` = 原因问或定义问）。点歌里的问词（「播放爱是什么」）不算。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify, classify_structured  # noqa: E402


@pytest.mark.parametrize("text", [
    "仪表盘上那个红色感叹号是什么意思",      # 修前 dashboard.open
    "你看看仪表盘上亮的这个是什么",
    "仪表上有个小人拿雨伞的图标是什么意思",
    "说明书里「打开后备箱」这一节讲的是什么",  # 修前 trunk.open
    "露营模式是什么意思",                     # 修前 scene_mode.set camping
    "限速提醒是什么意思",                     # 修前 speed_limit_assistance.open
    "屏幕顶上冒出一个斜线小人是啥情况",        # 修前 screen.open
    "座椅加热是什么",                         # 修前 seat.heating.on
    "什么是后视镜加热",
    "空调是什么模式",                         # 修前 aircon.open
])
def test_a_definition_question_is_not_executed(text):
    assert classify_structured(text) is None, (text, classify_structured(text))
    assert classify(text) is None, text


def test_a_definition_question_is_not_answered_with_a_reading():
    assert classify_structured("胎压报警灯亮了是什么意思") is None       # 修前 tire_pressure_monitoring.query「胎压正常」
    reading = classify_structured("胎压多少")
    assert reading is not None and reading["data"]["object"] == "tire_pressure_monitoring"


@pytest.mark.parametrize("text, obj", [
    ("打开座椅加热", "seat"),
    ("播放爱是什么", "media"),                # 点歌：问词在歌名里
    ("座椅加热是什么，打开试试", "seat"),      # 定义问 + 另起分句的操作动词 ⇒ 仍是指令
])
def test_directives_with_definition_words_still_run(text, obj):
    result = classify_structured(text)
    assert result is not None and result["data"]["object"] == obj, (text, result)


# ── 调节类方法问（车书口语召回二批，2026-09-26）：「X 怎么调」问的是怎么调，不是去调 ──────────────
@pytest.mark.parametrize("text", [
    "空调温度怎么调",                 # 修前 aircon open
    "座椅加热怎么调",                 # 修前 seat open
    "座椅高度怎么调节",               # 修前 seat open
    "自适应巡航的跟车距离怎么调",      # 修前 cruise_following open
])
def test_an_adjust_how_to_question_is_not_executed(text):
    assert classify_structured(text) is None, (text, classify_structured(text))


@pytest.mark.parametrize("text, operate", [
    ("温度如何调高", "inc"),           # 带方向：既有合同照常执行
    ("把空调调到26度", "set"),
])
def test_adjust_directives_still_run(text, operate):
    result = classify_structured(text)
    assert result is not None and result["data"]["operate"] == operate, (text, result)


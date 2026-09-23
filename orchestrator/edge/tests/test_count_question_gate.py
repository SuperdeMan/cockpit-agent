"""「几个」计数问不许被端侧执行成写车控（评审三轮追加批 H，2026-09-23；设计 §11）。

批 G 收口时本地复算照出：23 句计数问里 13 句在端侧被执行——「空调有几个风量档」调风量、「这车有几个座位」`seat.on`、
「后备箱能放几个行李箱」`trunk.open`、「大灯有几个模式」开大灯。问句判据（`runtime.question_shape`）的数量疑问词只有
「几档 / 几级 / 几种」，批 6 刻意不收「几个」：「开几个车窗」是模糊的祈使。修法在判据里认计数问的**句法头**
（有 / 分 / 共 / 能 / 可以 / 最多 + … + 几 + 量词）；出口的写操作问句闸与云侧安全闸读同一份，本文件只钉端侧出口。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest

from fast_intent import classify, classify_structured
from runtime.intent_effect import is_write_intent


@pytest.mark.parametrize("text", [
    "空调有几个风量档",          # 修前 aircon.wind_speed.set
    "这车有几个座位",            # 修前 seat.on
    "座椅加热有几个档位",        # 修前 seat.heating.on
    "空调有几个出风口",          # 修前 hvac.on
    "座椅加热分几个档",          # 修前 seat.heating.on
    "一共几个座位",              # 修前 seat.on
    "后备箱能放几个行李箱",      # 修前 trunk.open
    "座椅能调几个方向",          # 修前 seat.on
    "香氛有几个味道",            # 修前 fragrance.on
    "大灯有几个模式",            # 修前 headlight.on
    "座椅有几个记忆位",          # 修前 seat.on
    "空调温度能调几度",          # 修前 hvac.on
    "雨刮有几个速度",            # 修前 wiper.on
])
def test_count_question_is_not_executed(text):
    result = classify_structured(text)
    assert result is None or result.get("intent") != "control", (text, result)
    flat = classify(text)
    assert flat is None or not is_write_intent(flat["name"]), (text, flat)


@pytest.mark.parametrize("text, name", [
    ("开几个车窗", "window.open"),          # 「几」= 一些：批 6 不收「几个」的那条理由
    ("放几首歌", "media.play"),
    ("音量调大几格", "volume.inc"),
    ("车窗开了几个，都关上", "window.close"),
    ("座椅加热开到二档", "seat.heating.on"),
])
def test_directives_with_an_indefinite_ji_still_execute(text, name):
    flat = classify(text)
    assert flat is not None and flat["name"] == name, (text, flat)

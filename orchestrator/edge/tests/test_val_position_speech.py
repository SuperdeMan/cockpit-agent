"""VAL 话术里的位置念中文，不念协议标识（评审四轮顺带，2026-09-24）。

真栈 `ecbeed28` 修前 RS32：「打开主驾座椅加热，再打开副驾座椅加热」多意图合并播报原话是
「front_left座椅加热已打开，front_right座椅加热已打开」——`{position}` 占位符直接替换成归一化后的标识。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import split_and_classify  # noqa: E402
from val import VAL  # noqa: E402


def test_multi_intent_speech_names_the_seats_in_chinese():
    val = VAL()
    speeches = [val.execute(cmd, multi=True)[1]
                for cmd in split_and_classify("打开主驾座椅加热，再打开副驾座椅加热")]
    joined = "，".join(speeches)
    assert "front_" not in joined and "rear_" not in joined
    assert "主驾" in speeches[0] and "副驾" in speeches[1]


def test_position_display_reverse_maps_through_the_same_entity_table():
    val = VAL()
    assert val._position_display(["front_right"]) == "副驾"
    assert val._position_display(["rear_left", "rear_right"]) == "后排"
    assert val._position_display(["front_left", "rear_right"]) == "主驾、后排右"
    assert val._position_display("front_left") == "主驾"
    assert val._position_display([]) == ""
    assert val._position_display(["天窗边"]) == "天窗边"      # 查不到就原样念，不编

"""端侧位置抽取：长词优先、互不重叠、按出现顺序全取（评审四轮顺带发现，2026-09-25）。

修前 `_extract_position` 按词表顺序返回**第一个**命中的词：

- 「打开副驾驶位车窗」先撞上词表里排在前面的「驾驶位」⇒ `positions=['驾驶位']` ⇒ 归一化成 `front_left`——用户要副驾那扇，开的是主驾那扇；
- 「打开主驾和副驾车窗」只留「主驾」——两扇窗只开一扇（R4-04 修的是「关掉」继承位置，前提是这一步本来就带全了位置）。

VAL 归一化同时按序去重：「前排和主驾」不再得到两次 `front_left`（同一个位置执行两次）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify_structured  # noqa: E402
from val import VAL  # noqa: E402


def _positions(text):
    structured = classify_structured(text)
    assert structured is not None, text
    return structured["data"].get("positions")


@pytest.mark.parametrize("text, expected", [
    ("打开副驾驶位车窗", ["副驾驶位"]),              # 修前 ['驾驶位'] = 主驾
    ("打开副驾驶位的座椅加热", ["副驾驶位"]),
    ("打开主驾和副驾车窗", ["主驾", "副驾"]),         # 修前 ['主驾']
    ("打开主驾车窗和副驾车窗", ["主驾", "副驾"]),
    ("打开主驾和副驾的座椅加热", ["主驾", "副驾"]),
    ("打开副驾和主驾车窗", ["副驾", "主驾"]),         # 按原话出现顺序
    ("打开副驾驶车窗", ["副驾驶"]),                  # 修前 ['副驾']（归一化相同）；长词认了，不再多出一个「副驾」
])
def test_every_position_the_user_named_in_order(text, expected):
    assert _positions(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("打开驾驶位车窗", ["驾驶位"]),
    ("打开主驾车窗", ["主驾"]),
    ("打开后排车窗", ["后排"]),
    ("打开左后车窗", ["左后"]),
    ("打开车窗", None),
])
def test_single_positions_are_unchanged(text, expected):
    assert _positions(text) == expected


def test_the_passenger_seat_word_normalizes_to_the_passenger_side():
    val = VAL()
    normalized = val._normalize_entities({"positions": _positions("打开副驾驶位车窗")})
    assert normalized["positions"] == ["front_right"]


def test_normalized_positions_are_deduplicated_in_order():
    val = VAL()
    assert val._normalize_entities({"positions": ["前排", "主驾"]})["positions"] == ["front_left", "front_right"]
    assert val._normalize_entities({"positions": ["主驾", "副驾"]})["positions"] == ["front_left", "front_right"]

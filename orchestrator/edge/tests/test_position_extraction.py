"""端侧位置抽取：长词优先、互不重叠、按出现顺序全取（评审四轮顺带发现，2026-09-25）。

修前 `_extract_position` 按词表顺序返回**第一个**命中的词：

- 「打开副驾驶位车窗」先撞上词表里排在前面的「驾驶位」⇒ `positions=['驾驶位']` ⇒ 归一化成 `front_left`——用户要副驾那扇，开的是主驾那扇；
- 「打开主驾和副驾车窗」只留「主驾」——两扇窗只开一扇（R4-04 修的是「关掉」继承位置，前提是这一步本来就带全了位置）。

VAL 归一化同时按序去重：「前排和主驾」不再得到两次 `front_left`（同一个位置执行两次）。

词表合一（评审四轮待办，2026-09-25）：词表与扫描算法都是 `runtime.positions` 那一份（`entities.yaml` 的镜像）。修前端侧规则只有
12 个词，「后排左 / 前排右 / 后排中间」只认出「后排 / 前排」⇒ 两个座位一起执行。补全词表之后「位置 + 功能」简写分支
（排在各对象分支之前）会截走「左侧大灯 / 右侧雾灯」，所以简写收窄成「座位词紧挨功能词」。
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


# ── 词表合一（评审四轮待办，2026-09-25）：端侧规则认得出 entities.yaml 里的每一个位置词 ──────────────

@pytest.mark.parametrize("text, expected, ids", [
    ("打开后排左车窗", ["后排左"], ["rear_left"]),                 # 修前 ['后排'] ⇒ 两扇后窗都开
    ("打开后排右车窗", ["后排右"], ["rear_right"]),
    ("打开前排右座椅加热", ["前排右"], ["front_right"]),            # 修前 ['前排'] ⇒ 两个前座都加热
    ("打开后排中间座椅加热", ["后排中间"], ["rear_center"]),        # 修前 ['后排']
    ("后排中间加热", ["后排中间"], ["rear_center"]),
    ("打开右前车窗", ["右前"], ["front_right"]),                   # 修前认不出 ⇒ 缺省范围
    ("打开左前车窗", ["左前"], ["front_left"]),
    ("折叠右侧后视镜", ["右侧"], ["right"]),
    ("关闭所有位置的车窗", ["所有位置"], ["all"]),
    ("打开主驾驶车窗", ["主驾驶"], ["front_left"]),                # 修前 ['主驾']（同一侧）；「主驾驶」补进了 entities.yaml
])
def test_every_entities_word_is_recognized_and_normalized(text, expected, ids):
    assert _positions(text) == expected
    assert VAL()._normalize_entities({"positions": expected})["positions"] == ids


@pytest.mark.parametrize("text, expected", [
    ("帮我把后排左座椅向右侧调一点", ["后排左"]),        # 「向右侧」是方向
    ("帮我把后排左座椅腿托往右侧调一些", ["后排左"]),
    ("座椅往左侧调一点", None),
])
def test_a_side_word_after_a_direction_marker_is_a_direction(text, expected):
    assert _positions(text) == expected


def test_val_scans_a_compound_value_the_planner_wrote():
    """云侧规划写来的槽值是一段话：修前「主驾和副驾」原样当成**一个**位置标识下发。"""
    val = VAL()
    assert val._normalize_entities({"positions": "主驾和副驾"})["positions"] == ["front_left", "front_right"]
    assert val._normalize_entities({"positions": ["主驾驶和副驾"]})["positions"] == ["front_left", "front_right"]
    assert val._normalize_entities({"positions": ["front_left"]})["positions"] == ["front_left"]   # 协议标识原样
    assert val._normalize_entities({"positions": ["驾驶员"]})["positions"] == ["驾驶员"]           # 认不出：不猜


# ── 「位置 + 功能」简写只收省略了对象的说法 ─────────────────────────────────────────────

def _brief(text):
    structured = classify_structured(text)
    data = (structured or {}).get("data") or {}
    return data.get("object"), data.get("operate"), data.get("mode")


@pytest.mark.parametrize("text, expected", [
    ("后排通风", ("seat", "open", "ventilation")),
    ("前排加热", ("seat", "open", "heating")),
    ("主驾的加热打开", ("seat", "open", "heating")),
    ("关闭前排通风", ("seat", "close", "ventilation")),
    ("打开后排灯", ("ambient_light", "open", None)),
])
def test_the_shorthand_still_reads_an_omitted_object(text, expected):
    assert _brief(text) == expected


@pytest.mark.parametrize("text, expected", [
    # 词表补全之后会被简写分支截走的：点名的对象归对象自己的分支
    ("打开左侧大灯", ("headlight", "open", None)),
    ("打开右侧雾灯", ("fog_light", "open", None)),
    ("打开左前雾灯", ("fog_light", "open", None)),
    ("左侧近光灯打开", ("low_beam", "open", None)),
    ("开前后排雾灯", ("fog_light", "open", None)),                     # 修前落氛围灯
    # 修前就被截走的：座椅 / 氛围灯自己的分支才解得出增减与颜色
    ("前排座椅按摩调高一些", ("seat", "inc", "massage")),               # 修前 open
    ("副驾座椅加热调低一点", ("seat", "dec", "heating")),               # 修前 open
    ("右前座椅按摩调高一些", ("seat", "inc", "massage")),
    ("后排氛围灯调成蓝色", ("ambient_light", "set", None)),             # 修前 open、颜色丢了
])
def test_a_named_object_goes_to_its_own_branch(text, expected):
    assert _brief(text) == expected


def test_a_side_word_is_not_a_seat_for_the_shorthand():
    """「左侧加热」说不出是哪个座椅；「右侧后视镜加热」修前后都不该落座椅。"""
    assert _brief("右侧后视镜加热打开")[0] != "seat"
    assert classify_structured("左侧加热") is None

"""氛围灯亮度方向与「N 挡」取值（评审四轮待办顺带，2026-09-25）。

位置词表合一后「全车 / 前排右边氛围灯亮度…」不再被「位置 + 功能」简写截走、改由氛围灯分支来解，A/B（飞书语料 + 端侧语料 1 万条）
读出这个分支自己的两条缺陷，修前生产就在：

- 方向判据先看「高 / 亮 / 大」，而「亮度」里就有「亮」：「氛围灯亮度调低」「调到最暗」一律执行成**调高**（语料 58 条）；
- 各分支的挡位正则是 `(\\d)\\s*挡`，只抓挡字前面那一位：「亮度调到20挡」⇒ 设成 `0` 挡，VAL 不校验范围、原样执行。

判不出目标值的「亮度调到 50 / 中挡」修前当调高，现在交云端。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify_structured  # noqa: E402


def _brief(text):
    structured = classify_structured(text)
    if structured is None:
        return None
    data = structured.get("data") or {}
    return data.get("object"), data.get("operate"), data.get("mode"), data.get("value")


@pytest.mark.parametrize("text, operate", [
    ("氛围灯亮度调低", "dec"),                 # 修前 inc
    ("全车氛围灯亮度调低10%", "dec"),
    ("氛围灯亮度调到最暗", "dec"),             # 修前 inc
    ("把氛围灯调暗一点", "dec"),
    ("降低氛围灯亮度", "dec"),
    ("氛围灯亮度调高", "inc"),
    ("氛围灯调亮一点", "inc"),
    ("氛围灯亮度调到最亮", "inc"),
])
def test_brightness_direction(text, operate):
    assert _brief(text) == ("ambient_light", operate, "brightness", None)


def test_a_brightness_level_is_set_not_raised():
    assert _brief("氛围灯亮度调到3挡") == ("ambient_light", "set", "brightness", "3")     # 修前 inc


def test_an_unparsed_brightness_target_goes_to_the_cloud():
    assert classify_structured("氛围灯亮度调到50") is None
    assert classify_structured("氛围灯亮度调到中挡") is None


@pytest.mark.parametrize("text, expected", [
    ("请你把驾驶区氛围灯亮度调到20挡", ("ambient_light", "set", "brightness", "20")),   # 修前 0
    ("主驾座椅加热调到12挡", ("seat", "set", "heating", "12")),
    ("主驾座椅加热调到3挡", ("seat", "set", "heating", "3")),
    ("后排加热调到2挡", ("seat", "set", "heating", "2")),
])
def test_a_level_keeps_every_digit(text, expected):
    assert _brief(text) == expected


def test_colour_and_switching_are_unchanged():
    assert _brief("氛围灯调成蓝色")[:2] == ("ambient_light", "set")
    assert _brief("关闭氛围灯")[:2] == ("ambient_light", "close")
    assert _brief("打开氛围灯")[:2] == ("ambient_light", "open")

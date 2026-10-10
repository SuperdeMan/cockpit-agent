"""「和」并列里的方向继承（2026-10-10 核心旅程 P3 C17）。

「关闭座椅加热和方向盘加热」按「和」拆成两段后，第二段只念了对象，分类器按裸对象默认成「开」——
用户要关两样，另一样被打开。真栈收尾实测执行了 `steering_wheel.heating.open`，被车态复位核对抓到。
组里自己带方向的段方向一致时，把方向补给没带方向的段；方向不一致或都没带方向，照旧。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import _to_legacy_name, split_and_classify, split_and_classify_any


def _names(text):
    parts = split_and_classify_any(text) or []
    return [None if p.get("_needs_cloud") else _to_legacy_name(p) for p in parts]


@pytest.mark.parametrize("text, want", [
    # 修前：第二样（或第一样）被打开
    ("关闭座椅加热和方向盘加热", ["seat.heating.off", "steering_wheel.heating.close"]),
    ("关闭车窗和天窗", ["window.close", "sunroof.close"]),
    ("座椅加热和方向盘加热都关掉", ["seat.heating.off", "steering_wheel.heating.close"]),
    ("关掉空调和座椅加热", ["hvac.off", "seat.heating.off"]),
])
def test_the_spoken_direction_reaches_every_object_in_the_group(text, want):
    assert _names(text) == want, text


@pytest.mark.parametrize("text, want", [
    ("打开车窗和天窗", ["window.open", "sunroof.open"]),                    # 方向本来就是开
    ("打开空调和关闭车窗", ["hvac.on", "window.close"]),                     # 各说各的方向：不动
    ("空调和氛围灯", ["hvac.on", "ambient_light.on"]),                       # 都没说方向：照旧
    ("关闭座椅加热和关闭方向盘加热", ["seat.heating.off", "steering_wheel.heating.close"]),
])
def test_groups_that_already_say_their_directions_are_unchanged(text, want):
    assert _names(text) == want, text


def test_the_all_local_split_path_inherits_the_same_way():
    """另一条拆分路径（全有全无的 `split_and_classify`）经同一个「和」再拆分，方向一样补到。"""
    parts = split_and_classify("关闭车窗和天窗") or []
    assert [_to_legacy_name(p) for p in parts] == ["window.close", "sunroof.close"]

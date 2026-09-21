"""出口第四维：回指主语的**查询**不产出本地意图（批 7 ④）。

W19-c 视窗实验（2026-09-20，run 0920b，`b0d79dbe`）三臂恒定的 4 组里 3 组是同一形态：云端刚聊完
「问界 M9」，「它续航多久 / 那它的纯电续航呢 / 那它的续航呢」在端侧命中「续航」⇒ `battery.query`
秒回**本车**电量。指代物只活在云端历史，端侧根本看不见——这一句必须整句上云。
落点与前三维（问句 / 负极性 / 播报语域）同一个：`classify_structured` 出口，判据住 `runtime/anaphora.py`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import classify, classify_structured, split_and_classify_any


@pytest.mark.parametrize("text", [
    "它续航多久",
    "那它的纯电续航呢",
    "那它的续航呢",
    "它的电量有多大",
    "那款车的胎压标准是多少",
])
def test_anaphoric_read_query_goes_to_cloud(text):
    assert classify_structured(text) is None, text
    assert classify(text) is None, text


def test_anaphoric_part_inside_a_mixed_utterance_is_marked_for_cloud():
    """混合拆分：本地那半照旧本地，回指那半标上云——不许把「它」的续航答成本车的。"""
    parts = split_and_classify_any("把空调打开，那它的续航呢")
    assert parts is not None and len(parts) == 2
    local, cloud = parts
    assert local["_needs_cloud"] is False and local["data"]["object"] == "aircon"
    assert cloud["_needs_cloud"] is True and cloud["_raw_text"] == "那它的续航呢"


@pytest.mark.parametrize("text, obj", [
    ("电量还有多少", "battery"),        # 本车查询：端侧秒回不动
    ("续航还剩多少公里", "battery"),
    ("这车还能跑多远", "battery"),      # 近指 = 本车
])
def test_own_vehicle_queries_stay_local(text, obj):
    result = classify_structured(text)
    assert result is not None and result["data"]["object"] == obj, (text, result)
    assert result["intent"] == "query"

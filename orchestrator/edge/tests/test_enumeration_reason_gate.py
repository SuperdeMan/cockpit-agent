"""列举问 / 推荐请求 / 原因问句不许被端侧执行或答成读数（评审三轮追加批 E，2026-09-23）。

时延取样的真栈（`1255f7b2`）里，「推荐三部适合全家看的电影」端侧执行了 `media.play`（视频）、
「给我讲讲新能源车冬天续航为什么会下降」秒回「电量72%」。本地复算把缺口照得更大：问句判据
（`runtime.question_shape`）没有列举问法，「天窗有什么用」会**打开天窗**、「空调有什么模式」会开空调。

三处修法各有落点：列举问进问句判据（出口的写操作问句闸自动生效，云侧同一份）；原因问句是出口的
第五维，只盖**查询**（端侧只有读数，没有解释）；「推荐」是视频分支自己的让路（音乐「推荐一首歌」⇒ 播放
是既有裁决，不动）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import classify, classify_structured, split_and_classify_any


# ── E1：列举问不执行写操作 ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "天窗有什么用",                  # 修前 sunroof.open
    "空调有什么模式",                # 修前 aircon.open
    "氛围灯有哪些颜色",              # 修前 ambient_light.open
    "香氛有什么味道",                # 修前 fragrance.open
    "座椅有哪些模式",
    "有什么好看的电影",              # 修前 media.play（视频）
    "最近有什么好看的电视剧",
    "适合全家看的电影有哪些",
    "哪部电影好看",
])
def test_enumeration_question_is_not_executed(text):
    result = classify_structured(text)
    assert result is None or result.get("intent") != "control", (text, result)
    assert classify(text) is None, text


@pytest.mark.parametrize("text", [
    "推荐三部适合全家看的电影",      # 真栈原句：修前 media.play
    "推荐个电影",
    "给我推荐一部电影",
    "推荐几部好看的电视剧",
])
def test_video_recommendation_goes_to_cloud(text):
    assert classify_structured(text) is None, text
    assert classify(text) is None, text


@pytest.mark.parametrize("text, operate, obj", [
    ("播放电影", "play", "video"),
    ("放个视频", "play", "video"),
    ("看个视频", "play", "video"),
    ("打开视频", "open", "video"),
    ("推荐一首歌", "play", "music"),        # 音乐的推荐即播放：既有裁决不动
    ("打开天窗", "open", "sunroof"),
    ("空调有哪些模式，开个制冷", None, "aircon"),   # 列举问 + 操作动词 ⇒ 仍是指令
])
def test_directives_are_unchanged(text, operate, obj):
    result = classify_structured(text)
    assert result is not None and result["intent"] == "control", (text, result)
    assert result["data"]["object"] == obj, (text, result)
    if operate:
        assert result["data"]["operate"] == operate, (text, result)


# ── E2：原因问句不答成读数 ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "给我讲讲新能源车冬天续航为什么会下降",   # 真栈原句：修前「电量72%」
    "为什么续航下降这么快",
    "电量为什么掉得这么快",
    "电量怎么这么低",
    "胎压为什么报警",                         # 修前读四轮胎压
    "为什么胎压这么低",
])
def test_reason_question_is_not_answered_with_a_reading(text):
    assert classify_structured(text) is None, text
    assert classify(text) is None, text


@pytest.mark.parametrize("text, obj", [
    ("续航还有多少", "battery"),
    ("电量还剩多少", "battery"),
    ("告诉我还剩多少电", "battery"),
    ("续航怎么样", "battery"),
    ("胎压多少", "tire_pressure_monitoring"),
])
def test_value_queries_stay_local(text, obj):
    result = classify_structured(text)
    assert result is not None and result["intent"] == "query", (text, result)
    assert result["data"]["object"] == obj, (text, result)


def test_reason_clause_inside_a_mixed_utterance_goes_to_cloud():
    """混合拆分：本地那半照旧本地，原因问句那半标上云。"""
    parts = split_and_classify_any("把空调打开，为什么续航下降这么快")
    assert parts is not None and len(parts) == 2
    local, cloud = parts
    assert local["_needs_cloud"] is False and local["data"]["object"] == "aircon"
    assert cloud["_needs_cloud"] is True


# ── 追加批 F（F-4）：原因问句不执行写操作 ─────────────────────────────────────
# 修前端侧：「空调为什么不制冷」⇒ hvac.on、「天窗为什么关不上」⇒ sunroof.close（本地复算）。

@pytest.mark.parametrize("text", [
    "空调为什么不制冷",
    "天窗为什么关不上",
    "空调怎么不出风",
    "车窗怎么没关上",
])
def test_reason_question_about_a_device_is_not_executed(text):
    result = classify_structured(text)
    assert result is None or result.get("intent") != "control", (text, result)
    assert classify(text) is None, text


@pytest.mark.parametrize("text", [
    "座椅有哪些调节功能",          # 修前 seat.on
    "车窗有哪些开启方式",          # 修前 window.open
    "空调有哪些调节方式",          # 修前 hvac.on
    "座椅有哪些加热档位",          # 修前 seat.heating.on
])
def test_list_question_whose_noun_phrase_carries_an_operation_char_is_not_executed(text):
    result = classify_structured(text)
    assert result is None or result.get("intent") != "control", (text, result)
    assert classify(text) is None, text

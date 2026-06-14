"""Multi-intent splitting 测试。

覆盖：连接词拆分、逗号拆分、本地/云路由、单意图不拆分。
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import (
    classify_structured,
    split_and_classify,
    structured_to_legacy,
    is_local,
)


# ═══════════════════════════════════════════════════
# 1. 连接词拆分 — 两个本地意图
# ═══════════════════════════════════════════════════

class TestMultiIntentLocalPairs:
    def test_aircon_and_media(self):
        """打开空调并播放音乐 → aircon.open + media.start"""
        result = split_and_classify("打开空调并播放音乐")
        assert result is not None
        assert len(result) == 2
        assert result[0]["data"]["object"] == "aircon"
        assert result[0]["data"]["operate"] == "open"
        assert result[1]["data"]["object"] in ("media", "music")
        assert result[1]["data"]["operate"] in ("start", "play")

    def test_aircon_off_then_window_open(self):
        """关闭空调然后打开车窗 → aircon.close + window.open"""
        result = split_and_classify("关闭空调然后打开车窗")
        assert result is not None
        assert len(result) == 2
        assert result[0]["data"]["object"] == "aircon"
        assert result[0]["data"]["operate"] == "close"
        assert result[1]["data"]["object"] == "window"
        assert result[1]["data"]["operate"] == "open"

    def test_aircon_set_and_seat_heating(self):
        """把空调调到26度，再打开座椅加热 → aircon.set + seat heating.open"""
        result = split_and_classify("把空调调到26度，再打开座椅加热")
        assert result is not None
        assert len(result) == 2
        assert result[0]["data"]["object"] == "aircon"
        assert result[0]["data"]["operate"] == "set"
        assert result[0]["data"]["value"] == "26"
        assert result[1]["data"]["object"] == "seat"
        assert result[1]["data"]["mode"] == "heating"
        assert result[1]["data"]["operate"] == "open"

    def test_media_and_volume(self):
        """播放音乐并把音量调大"""
        result = split_and_classify("播放音乐并把音量调大")
        assert result is not None
        assert len(result) == 2
        assert result[0]["data"]["object"] in ("media", "music")
        assert result[1]["data"]["object"] == "volume"
        assert result[1]["data"]["operate"] == "inc"

    def test_sunroof_and_sunshade(self):
        """打开天窗同时关上遮阳帘"""
        result = split_and_classify("打开天窗同时关上遮阳帘")
        assert result is not None
        assert len(result) == 2
        assert result[0]["data"]["object"] == "sunroof"
        assert result[1]["data"]["object"] == "sunshade"
        assert result[1]["data"]["operate"] == "close"

    def test_aircon_and_weather_goes_cloud(self):
        """打开空调顺便看看今天天气 → None（weather 是 online_only，整句上云）"""
        result = split_and_classify("打开空调顺便看看今天天气")
        assert result is None


# ═══════════════════════════════════════════════════
# 2. 非本地意图 → 返回 None
# ═══════════════════════════════════════════════════

class TestMultiIntentCloudFallback:

    def test_restaurant_booking(self):
        """找川菜馆订今晚的位 → both need cloud → None"""
        result = split_and_classify("找川菜馆订今晚的位")
        assert result is None

    def test_local_and_cloud_mixed(self):
        """播放音乐然后讲个笑话 → chitchat needs cloud → None"""
        result = split_and_classify("播放音乐然后讲个笑话")
        assert result is None


# ═══════════════════════════════════════════════════
# 3. 单意图 → 不拆分，返回 None
# ═══════════════════════════════════════════════════

class TestSingleIntentNoSplit:
    def test_single_aircon(self):
        """打开空调 → 单意图，不拆分"""
        result = split_and_classify("打开空调")
        assert result is None

    def test_single_window(self):
        """关闭车窗 → 单意图"""
        result = split_and_classify("关闭车窗")
        assert result is None

    def test_single_media(self):
        """播放音乐 → 单意图"""
        result = split_and_classify("播放音乐")
        assert result is None


# ═══════════════════════════════════════════════════
# 4. structured_to_legacy 转换
# ═══════════════════════════════════════════════════

class TestStructuredToLegacy:
    def test_aircon_open(self):
        s = classify_structured("打开空调")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "hvac.on"

    def test_aircon_set(self):
        s = classify_structured("空调调到26度")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "hvac.set"
        assert legacy["slots"]["temp"] == "26"

    def test_aircon_close(self):
        s = classify_structured("关闭空调")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "hvac.off"

    def test_window_open(self):
        s = classify_structured("打开车窗")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "window.open"

    def test_media_start(self):
        s = classify_structured("播放音乐")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "media.play"

    def test_seat_heating_with_mode(self):
        s = classify_structured("打开座椅加热")
        legacy = structured_to_legacy(s)
        assert legacy is not None
        assert legacy["name"] == "seat.heating.on"

    def test_unknown_object_returns_none(self):
        s = {"domain": "unknown", "intent": "unknown",
             "data": {"object": "foobar", "operate": "do"}, "confidence": 0.5}
        assert structured_to_legacy(s) is None


# ═══════════════════════════════════════════════════
# 5. 边界情况
# ═══════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_string(self):
        assert split_and_classify("") is None

    def test_whitespace_only(self):
        assert split_and_classify("   ") is None

    def test_unclassifiable_sub_part(self):
        """一个子句可分类，另一个不可分类 → None"""
        result = split_and_classify("打开空调，blah blah blah")
        assert result is None

    def test_comma_split_with_two_local(self):
        """逗号分隔两个本地意图"""
        result = split_and_classify("打开空调，播放音乐")
        assert result is not None
        assert len(result) == 2

    def test_three_intents(self):
        """三个本地意图"""
        result = split_and_classify("打开空调，播放音乐，并且把音量调大")
        assert result is not None
        assert len(result) == 3

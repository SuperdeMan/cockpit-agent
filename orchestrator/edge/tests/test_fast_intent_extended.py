"""Fast Intent 扩展测试。

覆盖：新增规则模式（座椅/天窗/后备箱/车门锁/氛围灯/雨刷/后视镜等）、结构化输出格式。
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify, classify_structured, is_local


# ═══════════════════════════════════════════════════
# 1. 旧接口 classify() 向后兼容
# ═══════════════════════════════════════════════════

class TestClassifyBackwardCompat:
    def test_hvac_set(self):
        r = classify("空调调到26度")
        assert r is not None
        assert r["name"] == "hvac.set"
        assert r["slots"]["temp"] == "26"

    def test_hvac_on(self):
        r = classify("打开空调")
        assert r is not None
        assert r["name"] == "hvac.on"

    def test_hvac_off(self):
        r = classify("关闭空调")
        assert r is not None
        assert r["name"] == "hvac.off"

    def test_window_close(self):
        r = classify("把车窗关上")
        assert r is not None
        assert r["name"] == "window.close"

    def test_window_open(self):
        r = classify("打开车窗")
        assert r is not None
        assert r["name"] == "window.open"

    def test_media_pause(self):
        r = classify("暂停")
        assert r is not None
        assert r["name"] == "media.pause"

    def test_media_next(self):
        r = classify("下一首")
        assert r is not None
        assert r["name"] == "media.next"

    def test_non_local_returns_none(self):
        assert classify("讲个笑话") is None
        assert classify("附近的充电站") is None

    def test_is_local(self):
        assert is_local("hvac.set")
        assert not is_local("chitchat.talk")

    def test_scene_mode_recognized_but_not_local(self):
        """命名场景（露营/小憩/观影…）仍被结构化识别（保留 VAL 知识/语料），
        但刻意不是 LOCAL_INTENTS——由云端 scene-orchestrator 编排，不在端侧截留。"""
        structured = classify_structured("开启露营模式")
        assert structured is not None
        assert structured["data"]["object"] == "scene_mode"
        assert structured["data"]["mode"] == "camping"
        # 关键：不是本地意图 → is_local 为 False → 路由上云 scene.activate
        assert not is_local(classify("开启露营模式")["name"])
        assert not is_local("scene_mode.set")


# ═══════════════════════════════════════════════════
# 2. 新接口 classify_structured() — 座椅
# ═══════════════════════════════════════════════════

class TestSeatPatterns:
    def test_seat_heating_on(self):
        r = classify_structured("打开座椅加热")
        assert r is not None
        assert r["data"]["object"] == "seat"
        assert r["data"]["mode"] == "heating"
        assert r["data"]["operate"] == "open"

    def test_seat_heating_off(self):
        r = classify_structured("关闭座椅加热")
        assert r is not None
        assert r["data"]["object"] == "seat"
        assert r["data"]["operate"] == "close"

    def test_seat_ventilation(self):
        r = classify_structured("打开座椅通风")
        assert r is not None
        assert r["data"]["mode"] == "ventilation"

    def test_seat_massage(self):
        r = classify_structured("打开座椅按摩")
        assert r is not None
        assert r["data"]["mode"] == "massage"

    def test_seat_heating_with_position(self):
        r = classify_structured("主驾座椅加热")
        assert r is not None
        assert "主驾" in r["data"]["positions"]

    def test_seat_heating_with_level(self):
        r = classify_structured("座椅加热调到3挡")
        assert r is not None
        assert r["data"]["value"] == "3"
        assert r["data"]["unit"] == "level"


# ═══════════════════════════════════════════════════
# 3. 新接口 — 天窗/遮阳帘
# ═══════════════════════════════════════════════════

class TestSunroofPatterns:
    def test_sunroof_open(self):
        r = classify_structured("打开天窗")
        assert r is not None
        assert r["data"]["object"] == "sunroof"
        assert r["data"]["operate"] == "open"

    def test_sunroof_close(self):
        r = classify_structured("关闭天窗")
        assert r is not None
        assert r["data"]["operate"] == "close"


class TestSunshadePatterns:
    def test_sunshade_open(self):
        r = classify_structured("打开遮阳帘")
        assert r is not None
        assert r["data"]["object"] == "sunshade"
        assert r["data"]["operate"] == "open"

    def test_sunshade_close(self):
        r = classify_structured("关闭遮阳帘")
        assert r is not None
        assert r["data"]["operate"] == "close"

    def test_sunshade_with_position(self):
        r = classify_structured("后排遮阳帘打开")
        assert r is not None
        assert "后排" in r["data"]["positions"]


# ═══════════════════════════════════════════════════
# 4. 新接口 — 后备箱
# ═══════════════════════════════════════════════════

class TestTrunkPatterns:
    def test_trunk_open(self):
        r = classify_structured("打开后备箱")
        assert r is not None
        assert r["data"]["object"] == "trunk"
        assert r["data"]["operate"] == "open"

    def test_trunk_close(self):
        r = classify_structured("关闭后备箱")
        assert r is not None
        assert r["data"]["operate"] == "close"

    def test_trunk_alias_tailgate(self):
        r = classify_structured("打开尾门")
        assert r is not None
        assert r["data"]["object"] == "trunk"


# ═══════════════════════════════════════════════════
# 5. 新接口 — 车门锁
# ═══════════════════════════════════════════════════

class TestDoorLockPatterns:
    def test_door_lock_open(self):
        r = classify_structured("解锁车门")
        assert r is not None
        assert r["data"]["object"] == "door_lock"
        assert r["data"]["operate"] == "open"

    def test_door_lock_close(self):
        r = classify_structured("锁上车门")
        assert r is not None
        assert r["data"]["object"] == "door_lock"
        assert r["data"]["operate"] == "close"


# ═══════════════════════════════════════════════════
# 6. 新接口 — 氛围灯
# ═══════════════════════════════════════════════════

class TestAmbientLightPatterns:
    def test_ambient_light_on(self):
        r = classify_structured("打开氛围灯")
        assert r is not None
        assert r["data"]["object"] == "ambient_light"
        assert r["data"]["operate"] == "open"

    def test_ambient_light_off(self):
        r = classify_structured("关闭氛围灯")
        assert r is not None
        assert r["data"]["operate"] == "close"

    def test_ambient_light_color(self):
        r = classify_structured("氛围灯设为蓝色")
        assert r is not None
        assert r["data"]["tag"] == "蓝色"


# ═══════════════════════════════════════════════════
# 7. 新接口 — 雨刷
# ═══════════════════════════════════════════════════

class TestWiperPatterns:
    def test_wiper_on(self):
        r = classify_structured("打开雨刷")
        assert r is not None
        assert r["data"]["object"] == "wiper"
        assert r["data"]["operate"] == "open"

    def test_wiper_off(self):
        r = classify_structured("关闭雨刷")
        assert r is not None
        assert r["data"]["operate"] == "close"


# ═══════════════════════════════════════════════════
# 8. 新接口 — 后视镜
# ═══════════════════════════════════════════════════

class TestRearViewMirrorPatterns:
    def test_mirror_fold(self):
        r = classify_structured("折叠后视镜")
        assert r is not None
        assert r["data"]["object"] == "rear_view_mirror"
        assert r["data"]["mode"] == "fold"

    def test_mirror_unfold(self):
        r = classify_structured("打开后视镜")
        assert r is not None
        assert r["data"]["mode"] == "unfold"


# ═══════════════════════════════════════════════════
# 9. 新接口 — 香氛/音量/大灯/近光灯
# ═══════════════════════════════════════════════════

class TestOtherPatterns:
    def test_fragrance_on(self):
        r = classify_structured("打开香氛")
        assert r is not None
        assert r["data"]["object"] == "fragrance"

    def test_headlight_on(self):
        r = classify_structured("打开大灯")
        assert r is not None
        assert r["data"]["object"] == "headlight"

    def test_low_beam_on(self):
        r = classify_structured("打开近光灯")
        assert r is not None
        assert r["data"]["object"] == "low_beam"

    def test_volume_set(self):
        r = classify_structured("音量调到50")
        assert r is not None
        assert r["data"]["object"] == "volume"
        assert r["data"]["value"] == "50"

    def test_volume_inc(self):
        r = classify_structured("音量调大")
        assert r is not None
        assert r["data"]["operate"] == "inc"

    def test_volume_dec(self):
        r = classify_structured("音量调小")
        assert r is not None
        assert r["data"]["operate"] == "dec"

    def test_driving_mode_sport(self):
        r = classify_structured("切换运动模式")
        assert r is not None
        assert r["data"]["object"] == "driving_mode"
        assert r["data"]["mode"] == "sport"

    def test_driving_mode_eco(self):
        r = classify_structured("节能模式")
        assert r is not None
        assert r["data"]["object"] == "driving_mode"
        assert r["data"]["mode"] == "eco"


# ═══════════════════════════════════════════════════
# 10. 结构化输出格式
# ═══════════════════════════════════════════════════

class TestStructuredOutputFormat:
    def test_has_domain_intent_data(self):
        r = classify_structured("打开空调")
        assert r is not None
        assert "domain" in r
        assert "intent" in r
        assert "data" in r
        assert "confidence" in r

    def test_data_has_operate_and_object(self):
        r = classify_structured("打开空调")
        assert r is not None
        assert "operate" in r["data"]
        assert "object" in r["data"]

    def test_domain_is_setting_for_control(self):
        r = classify_structured("打开车窗")
        assert r is not None
        assert r["domain"] == "setting"

    def test_domain_is_app_for_media(self):
        r = classify_structured("播放音乐")
        assert r is not None
        assert r["domain"] == "app"

    def test_confidence_in_range(self):
        r = classify_structured("空调调到26度")
        assert r is not None
        assert 0.0 <= r["confidence"] <= 1.0

    def test_non_local_returns_none(self):
        assert classify_structured("讲个笑话") is None


# ═══════════════════════════════════════════════════
# 11. 回归：风速「档/级」字 + 驾驶模式本地路由
# ═══════════════════════════════════════════════════

class TestWindSpeedAndDrivingModeRouting:
    def test_wind_speed_accepts_dang_and_ji(self):
        # "档"(木) / "挡"(扌) / "级" 都应抽到档位值，不只认"挡"
        for text in ("把空调风速调到3档", "把空调风速调到3挡", "风速调到3级"):
            r = classify_structured(text)
            assert r is not None, text
            assert r["data"]["mode"] == "wind_speed"
            assert r["data"]["value"] == "3", text

    def test_driving_mode_set_routes_local(self):
        # 驾驶模式应走端侧快路径，不再误上云
        name = classify("切换到运动驾驶模式")["name"]
        assert name == "driving_mode.set"
        assert is_local(name)


# ═══════════════════════════════════════════════════
# 12. 车窗开度：相对调节 + 模糊小开度
# ═══════════════════════════════════════════════════

class TestWindowDegreePatterns:
    def test_open_bigger_is_inc(self):
        r = classify_structured("把车窗开大一点")
        assert r is not None
        assert r["data"]["object"] == "window"
        assert r["data"]["operate"] == "inc"

    def test_open_smaller_is_dec(self):
        r = classify_structured("车窗开小一点")
        assert r is not None
        assert r["data"]["operate"] == "dec"

    def test_close_a_bit_is_dec_not_full_close(self):
        r = classify_structured("车窗关小一点")
        assert r is not None
        assert r["data"]["operate"] == "dec"

    def test_crack_open_is_small_percent(self):
        for text in ("车窗开条缝", "车窗开一点"):
            r = classify_structured(text)
            assert r is not None, text
            assert r["data"]["operate"] == "set"
            assert r["data"]["value"] == "15", text

    def test_half_and_percent_still_work(self):
        assert classify_structured("车窗开一半")["data"]["value"] == "50"
        assert classify_structured("把车窗开到30%")["data"]["value"] == "30"

    def test_relative_intents_are_local(self):
        assert is_local(classify("把车窗开大一点")["name"])
        assert is_local(classify("车窗开小一点")["name"])


# ═══════════════════════════════════════════════════
# 13. 电量查询：本地确定性应答，不误判为胎压
# ═══════════════════════════════════════════════════

class TestBatteryQuery:
    def test_battery_query_classified_and_local(self):
        for text in ("当前还剩多少电量", "电量还有多少", "还剩多少电"):
            r = classify_structured(text)
            assert r is not None, text
            assert r["data"]["object"] == "battery", text
            assert r["data"]["operate"] == "query"
        assert is_local(classify("当前还剩多少电量")["name"])

    def test_battery_not_misrouted_to_tire_pressure(self):
        r = classify_structured("当前还剩多少电量")
        assert r["data"]["object"] != "tire_pressure"

    def test_remaining_phrase_not_split_but_conjunction_still_splits(self):
        import fast_intent as fi
        # "还有多少"是问量短语，不能被当连接词"还有"拆开（否则碎片上云乱答）
        assert fi._SPLIT_MARKERS.split("电量还有多少") == ["电量还有多少"]
        # 真正的连接词"还有"仍能拆分多意图
        assert fi._SPLIT_MARKERS.split("开空调还有放音乐") == ["开空调", "放音乐"]


def test_climate_feeling_guard_requires_temp_wind_and_direction():
    """体感冷热推断只在【同时点名温度+风速】且【明确冷/热】时触发，否则交常规分类。"""
    from fast_intent import climate_feeling_intents
    assert climate_feeling_intents("我感觉有点冷帮我把空调温度和风速都调一下") is not None
    assert climate_feeling_intents("把空调温度调一下") is None       # 只温度
    assert climate_feeling_intents("把空调风速调一下") is None       # 只风速
    assert climate_feeling_intents("空调温度和风速调一下") is None    # 无冷热方向
    assert climate_feeling_intents("导航去公司") is None             # 与空调无关

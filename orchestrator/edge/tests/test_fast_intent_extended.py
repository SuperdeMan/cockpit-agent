"""Fast Intent 扩展测试。

覆盖：新增规则模式（座椅/天窗/后备箱/车门锁/氛围灯/雨刷/后视镜等）、结构化输出格式。
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import (classify, classify_structured, is_local,
                         split_and_classify, split_and_classify_any)


# ═══════════════════════════════════════════════════
# 0. 提醒话术让路 + 体感入口收窄（badcase c9bcf8c2）
# ═══════════════════════════════════════════════════

class TestReminderUtteranceYieldsToCloud:
    def test_badcase_reminder_with_leng_cui_not_hijacked(self):
        """「再帮我加一个…提醒我冷萃咖啡过滤」：「冷」(冷萃)+「再」(再帮我) 撞旧体感
        共现规则被端侧当 hvac.on 真开了空调——提醒话术必须整句上云归 reminder。"""
        assert classify_structured("再帮我加一个明天晚上10点提醒我冷萃咖啡过滤。") is None
        assert classify("再帮我加一个明天晚上10点提醒我冷萃咖啡过滤。") is None

    def test_reminder_verbs_go_cloud(self):
        assert classify_structured("提醒我明天带伞") is None
        assert classify_structured("明早七点设个闹钟") is None
        assert classify_structured("别忘了提醒我给妈妈打电话") is None
        assert classify_structured("到时候叫我一下") is None

    def test_adas_alert_settings_stay_local(self):
        """裸「提醒」不触发让路：「限速提醒/疲劳提醒」是 ADAS 功能开关，仍走端侧。"""
        r = classify_structured("打开限速提醒")
        assert r is not None

    def test_feeling_relative_forms_still_local(self):
        assert classify("温度再热一点")["name"] == "aircon.inc"
        assert classify("冷一点")["name"] == "aircon.dec"

    def test_feeling_with_degree_still_local(self):
        r = classify("太热了，调到24度")
        assert r is not None and r["name"] == "hvac.set" and r["slots"]["temp"] == "24"

    def test_bare_cooccurrence_no_longer_enters_aircon(self):
        """旧规则 (热|冷)×(再|一点|度) 的误伤面：含「冷/热」的无关句不再被空调分支接走
        （允许落到其它更贴切的域，如「这首歌…再来一遍」归媒体；只断言不进空调）。"""
        r1 = classify_structured("这首歌太热了再来一遍")
        assert r1 is None or r1.get("data", {}).get("object") != "aircon"
        r2 = classify_structured("再来一杯冷萃咖啡")
        assert r2 is None or r2.get("data", {}).get("object") != "aircon"


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


class TestSceneManagementGoesToCloud:
    """场景管理句（造/改/删「X模式」）一律上云——句中的车控词是**场景内容**不是当下指令。

    没有这条护栏，「创建钓鱼模式：氛围灯调到10%，空调22度」会被端侧当多意图车控当场执行：
    灯真调暗了、空调真开了，场景却没建成（2026-07-14 真栈 e2e 实测命中）。
    """

    def test_create_sentence_not_executed_locally(self):
        assert classify("帮我创建一个钓鱼模式：氛围灯调到10%，空调22度") is None
        assert classify_structured("帮我创建一个钓鱼模式：氛围灯调到10%，空调22度") is None
        assert split_and_classify("帮我创建一个钓鱼模式：氛围灯调到10%，空调22度") is None

    def test_edit_and_delete_sentences_not_executed_locally(self):
        assert classify("把钓鱼模式的温度改成24") is None      # 改场景定义 ≠ 现在开空调
        assert classify("钓鱼模式再加一个开香氛") is None
        assert classify("去掉钓鱼模式里的香氛") is None
        assert classify("删掉钓鱼模式") is None

    def test_edge_mode_words_still_local(self):
        """D8：驾驶/动力类模式词是端侧毫秒级秒回，护栏不能把它们让给云端。"""
        assert is_local(classify("打开运动模式")["name"])
        assert is_local(classify("把驾驶模式改成运动")["name"])

    def test_plain_vehicle_control_unaffected(self):
        assert classify("把空调调到26度")["name"] == "hvac.set"
        assert classify("氛围灯调暗一点") is not None

    def test_scene_activation_with_param_not_hijacked_by_aircon(self):
        """「开启午休模式，温度26」里的「温度26」是**场景参数**（激活时覆盖场景的空调设定），
        不是当下要开空调。

        护栏必须早于空调分支：否则「温度26」让整句落到 aircon，端侧把空调开了、
        **场景根本没激活**（真机实测：speech「开了」、scene_mode 仍是 off）。
        """
        assert classify("开启午休模式，温度26") is None          # 用户/未知场景 → 整句上云
        assert classify("开启午休模式") is None

        # 出厂场景词仍结构化识别（保留 VAL 知识/语料），但不是 LOCAL_INTENTS → 照样上云
        r = classify_structured("开启露营模式，温度26")
        assert r["data"]["object"] == "scene_mode" and r["data"]["mode"] == "camping"
        assert not is_local(classify("开启露营模式，温度26")["name"])

    def test_scene_deactivation_goes_to_cloud(self):
        assert classify("退出午休模式") is None
        assert not is_local(classify("退出露营模式")["name"])

    def test_scene_sentences_are_never_split(self):
        """**混合意图路径也得堵**：`split_and_classify_any` 会把拆出来的本地子句当场执行。

        「开启午休模式，温度26」若被拆成「开启午休模式」+「温度26」，后半句就成了独立的
        本地空调指令——真机实测：speech「开了」、空调真开了、场景根本没激活。
        `_split_parts` 是两个 split 函数的唯一收口，堵在那里。
        """
        for t in ("开启午休模式，温度26", "帮我创建一个钓鱼模式：氛围灯调到10%，空调22度",
                  "退出露营模式，把灯关了"):
            assert split_and_classify(t) is None, t
            assert split_and_classify_any(t) is None, t

        # 普通多意图照拆不误（零回归）
        assert split_and_classify_any("空调22度，氛围灯调暗") is not None


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

    def test_battery_topic_not_hijacked_as_battery_query(self):
        # "固态电池/电池技术/电池行业"是话题（深度调研主题），不应被端侧判成电量查询，
        # 否则"深入调研固态电池"被劫持成"电量72%"（裸"电池"过宽，已收窄）。
        for text in ("深入调研一下固态电池的现状和量产前景",
                     "固态电池技术", "研究下电池行业的发展"):
            r = classify_structured(text)
            if r is not None:
                assert r["data"].get("object") != "battery", f"{text} 误判成 battery: {r}"

    def test_battery_with_level_or_status_word_still_local(self):
        # 带电量级/状态词的"电池"查询仍判 battery（不误伤真实电量查询）。
        for text in ("电池还有多少", "电池剩多少电", "看下电池状态", "电池健康吗"):
            r = classify_structured(text)
            assert r is not None, text
            assert r["data"]["object"] == "battery", text

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


# ═══════════════════════════════════════════════════
# badcase 361f6e72：温度问句不得误触空调（车控误执行）
# ═══════════════════════════════════════════════════

class TestEnvTempQueryNotHvac:
    """「今天体感温度怎么样」曾被裸「温度」子条件劫持成 hvac 开启（3ms 本地执行）。
    天气语境/疑问式让路查询；带操作动词仍归空调。"""

    def _name(self, text):
        r = classify(text)
        return r["name"] if r else None

    def test_feels_like_question_goes_weather(self):
        assert self._name("今天体感温度怎么样") == "info.weather"

    def test_outdoor_temp_goes_weather(self):
        assert self._name("外面气温多少") == "info.weather"

    def test_bare_temp_interrogative_not_hvac(self):
        n = self._name("温度怎么样")
        assert n is None or not n.startswith(("hvac", "aircon"))

    def test_temp_adjust_still_hvac(self):
        # 空调域名字双轨（set→hvac.set / inc→aircon.inc，既有现状），前缀二选一即在域内
        for text in ("温度调到26度", "温度调高一点", "温度如何调高"):
            n = self._name(text)
            assert n and n.startswith(("hvac", "aircon")), f"{text} -> {n}"

    def test_ac_keyword_still_hvac(self):
        assert (self._name("打开空调") or "").startswith("hvac")


# ═══════════════════════════════════════════════════
# 旅程红灯 R5/R7：偏好陈述与「提醒打电话」不被端侧劫持
# ═══════════════════════════════════════════════════

class TestPreferenceAndReminderLetThrough:
    """B3-3：「记住，我最喜欢的空调温度是26度」曾被温度分支当场执行成开空调；
    「把空调调到我喜欢的温度」曾被当「开空调」秒回——参数在画像里须云端记忆召回。
    A2-4：「到之前一刻钟提醒我给张姐打电话」曾被电话分支秒回「暂不支持哦」。"""

    def _name(self, text):
        r = classify(text)
        return r["name"] if r else None

    def test_remember_preference_not_hvac(self):
        n = self._name("记住，我最喜欢的空调温度是26度")
        assert n is None or not n.startswith(("hvac", "aircon"))

    def test_favorite_temp_not_hvac(self):
        n = self._name("把空调调到我喜欢的温度")
        assert n is None or not n.startswith(("hvac", "aircon"))

    def test_plain_hvac_still_local(self):
        assert self._name("把空调调到26度") in ("hvac.set", "aircon.set")

    def test_reminder_call_not_phone(self):
        n = self._name("到之前一刻钟提醒我给张姐打电话")
        assert n is None or not n.startswith("phone")

    def test_plain_call_still_phone(self):
        assert self._name("给张姐打电话") == "phone.call"

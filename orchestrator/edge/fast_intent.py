"""Fast Intent：端侧快意图分类器（PoC 规则版）。

只判定"是否高频确定指令(车控/媒体)"并抽槽位，给置信度。命中则本地秒回，否则上云。
新增 classify_structured() 输出公版 data 格式 {domain, intent, data}，覆盖座椅/天窗/后备箱等。
保持 classify() 向后兼容——现有调用者不受影响。
"""
from __future__ import annotations
import re

LOCAL_INTENTS = {
    "hvac.set", "hvac.on", "hvac.off",
    "window.open", "window.close", "window.set",
    "media.play", "media.pause", "media.next", "media.prev",
    # 座椅（open/close 兼容 on/off）
    "seat.heating.on", "seat.heating.off", "seat.heating.open", "seat.heating.close",
    "seat.ventilation.on", "seat.ventilation.off", "seat.ventilation.open", "seat.ventilation.close",
    "seat.massage.on", "seat.massage.off", "seat.massage.open", "seat.massage.close",
    "seat.lumbar_support.on", "seat.lumbar_support.off", "seat.lumbar_support.open", "seat.lumbar_support.close",
    "seat.lumbar_support.inc", "seat.lumbar_support.dec",
    "sunroof.open", "sunroof.close", "sunroof.set",
    "sunshade.open", "sunshade.close", "sunshade.set",
    "trunk.open", "trunk.close",
    "door_lock.open", "door_lock.close",
    # 氛围灯/大灯/雨刷/香氛（open/close 兼容 on/off）
    "ambient_light.on", "ambient_light.off", "ambient_light.open", "ambient_light.close", "ambient_light.set",
    "headlight.on", "headlight.off", "headlight.open", "headlight.close",
    "wiper.on", "wiper.off", "wiper.open", "wiper.close", "wiper.speed.set", "wiper.speed.inc", "wiper.speed.dec",
    "rear_view_mirror.fold", "rear_view_mirror.unfold",
    "fragrance.on", "fragrance.off", "fragrance.open", "fragrance.close", "fragrance.set",
    "volume.set", "volume.inc", "volume.dec",
    # 油箱盖 / 充电口盖
    "fuel_tank_cover.open", "fuel_tank_cover.close",
    "charging_port.open", "charging_port.close",
    # 胎压 / 行车记录仪
    "tire_pressure.query",
    "dashcam.open", "dashcam.close",
    # 场景模式 / 电源模式
    "scene_mode.set",
    "power_mode.set",
    # 动能回收
    "energy_recovery.set", "energy_recovery.inc", "energy_recovery.dec",
    # 车道辅助
    "lane_departure_assistance.open", "lane_departure_assistance.close",
    "lane_assistance.open", "lane_assistance.close",
    # 方向盘（加热/高度）
    "steering_wheel.heating.open", "steering_wheel.heating.close",
    "steering_wheel.height.set", "steering_wheel.height.inc", "steering_wheel.height.dec",
    # 屏幕亮度
    "screen.brightness.set", "screen.brightness.inc", "screen.brightness.dec",
    # 注意：page/app/weather 为 online_only，不进 LOCAL_INTENTS，统一上云
    # 空调风速 / 温度增减
    "aircon.wind_speed.set", "aircon.wind_speed.inc", "aircon.wind_speed.dec",
    "aircon.inc", "aircon.dec",
    # ── 新增：蓝牙 ──
    "bluetooth.on", "bluetooth.off", "bluetooth.open", "bluetooth.close",
    "bluetooth.connect", "bluetooth.disconnect",
    # ── 新增：WiFi ──
    "wifi.on", "wifi.off", "wifi.open", "wifi.close",
    "wifi.connect", "wifi.disconnect",
    # ── 新增：个人热点 ──
    "hotspot.on", "hotspot.off", "hotspot.open", "hotspot.close",
    # ── 新增：自动驻车/制动 ──
    "auto_hold.on", "auto_hold.off", "auto_hold.open", "auto_hold.close",
    # ── 新增：主菜单/桌面 ──
    "launcher.return",
    # ── 新增：均衡器/音效 ──
    "equalizer.on", "equalizer.off", "equalizer.open", "equalizer.close", "equalizer.set",
    "sound_effect.set", "sound_effect.open", "sound_effect.close",
    # ── 新增：语音助手 ──
    "voice_assistant.on", "voice_assistant.off", "voice_assistant.open", "voice_assistant.close",
    "voice_assistant.set", "voice_assistant.wakeup", "voice_assistant.stop",
    # ── 新增：系统设置 ──
    "system.restore", "system.update", "system.clean",
    "factory_settings.restore",
    # ── 新增：360环视/倒车影像 ──
    "surround_view.on", "surround_view.off", "surround_view.open", "surround_view.close",
    # ── 新增：仪表设置 ──
    "dashboard.open", "dashboard.close",
    # ── 新增：电话 ──
    "phone.call", "phone.answer", "phone.hangup",
    # ── 新增：通讯录 ──
    "contacts.open", "contacts.close",
    # ── 新增：通话记录 ──
    "call_log.open", "call_log.close",
    # ── 新增：近光灯 ──
    "low_beam.on", "low_beam.off", "low_beam.open", "low_beam.close",
}


def _i(name: str, slots: dict, conf: float) -> dict:
    return {"name": name, "slots": slots, "confidence": conf}


def classify(text: str) -> dict | None:
    """旧接口：返回 {name, slots, confidence}。向后兼容。"""
    result = classify_structured(text)
    if result is None:
        return None

    # 从结构化结果映射回旧 name/slots 格式
    _on_off_map = {"open": "on", "close": "off"}
    data = result.get("data", {})
    obj = data.get("object", "")
    operate = data.get("operate", "")
    mode = data.get("mode", "")

    # 构造旧 name
    if obj == "aircon":
        if mode == "wind_speed":
            name = f"aircon.wind_speed.{operate}"
        elif operate == "close":
            name = "hvac.off"
        elif operate == "inc":
            name = "aircon.inc"
        elif operate == "dec":
            name = "aircon.dec"
        elif operate in ("set", "open") and data.get("value"):
            name = "hvac.set"
        else:
            name = "hvac.on"
    elif obj == "window":
        name = f"window.{operate}"
    elif obj in ("seat", "sunroof", "sunshade", "trunk", "door_lock",
                 "ambient_light", "headlight", "rear_view_mirror",
                 "fragrance", "volume"):
        name = f"{obj}.{operate}"
        if mode:
            name = f"{obj}.{mode}.{operate}"
    elif obj == "wiper":
        if mode == "speed":
            name = f"wiper.speed.{operate}"
        else:
            name = f"wiper.{operate}"
    elif obj == "steering_wheel":
        name = f"steering_wheel.{mode}.{operate}" if mode else f"steering_wheel.{operate}"
    elif obj == "screen":
        name = f"screen.{mode}.{operate}" if mode else f"screen.{operate}"
    elif obj == "energy_recovery":
        name = f"energy_recovery.{operate}"
    elif obj in ("lane_departure_assistance", "lane_assistance"):
        name = f"{obj}.{operate}"
    elif obj in ("scene_mode", "power_mode"):
        name = f"{obj}.set"
    elif obj == "weather":
        name = "weather.query"
    elif obj == "page":
        name = "page.open"
    elif obj == "app":
        name = f"app.{operate}"
    elif obj == "media":
        # 媒体特殊映射：switch → next/prev（从文本推断）
        if operate == "switch":
            if "上一首" in text:
                name = "media.prev"
            else:
                name = "media.next"
        else:
            media_map = {"start": "play", "pause": "pause", "stop": "pause"}
            name = f"media.{media_map.get(operate, operate)}"
    elif obj == "bluetooth":
        name = f"bluetooth.{operate}"
    elif obj == "wifi":
        name = f"wifi.{operate}"
    elif obj == "hotspot":
        name = f"hotspot.{_on_off_map.get(operate, operate)}"
    elif obj == "auto_hold":
        name = f"auto_hold.{_on_off_map.get(operate, operate)}"
    elif obj == "epb":
        name = f"epb.{_on_off_map.get(operate, operate)}"
    elif obj == "launcher":
        name = "launcher.return"
    elif obj in ("equalizer", "sound_effect"):
        name = f"{obj}.{operate}"
    elif obj == "voice_assistant":
        name = f"voice_assistant.{operate}"
    elif obj == "factory_settings":
        name = "factory_settings.restore"
    elif obj == "memory":
        name = "system.clean"
    elif obj == "language":
        name = "language.set"
    elif obj == "time_format":
        name = "time_format.set"
    elif obj == "surround_view":
        name = f"surround_view.{_on_off_map.get(operate, operate)}"
    elif obj == "dashboard":
        name = f"dashboard.{_on_off_map.get(operate, operate)}"
    elif obj == "phone":
        name = f"phone.{operate}"
    elif obj == "contacts":
        name = f"contacts.{operate}"
    elif obj == "call_log":
        name = f"call_log.{_on_off_map.get(operate, operate)}"
    elif obj in ("radio", "online_radio", "opera", "news", "audiobook"):
        name = f"{obj}.{operate}"
    elif obj in ("music", "video"):
        name = f"{obj}.{operate}"
    elif obj == "TV":
        name = f"TV.{_on_off_map.get(operate, operate)}"
    elif obj == "frunk":
        name = f"frunk.{operate}"
    elif obj == "map":
        name = f"map.{operate}"
    elif obj in ("food", "hotel", "flight", "train", "stock",
                 "temperature", "humidity", "wind_force", "air_quality"):
        name = f"{obj}.query"
    elif obj == "navi":
        name = f"navi.{operate}"
    elif obj in ("high_beam", "low_beam", "fog_light", "warning_light"):
        name = f"{obj}.{operate}"
    elif obj in ("cruise_following", "blind_spot_warning", "body_stability",
                 "hill_descent", "creep_mode", "forward_collision_warning",
                 "fatigue_detection", "speed_limit_assistance"):
        name = f"{obj}.{_on_off_map.get(operate, operate)}"
    elif obj in ("v2v_charging", "battery_preheat", "scheduled_charging"):
        name = f"{obj}.{_on_off_map.get(operate, operate)}"
    elif obj == "energy_consumption":
        name = "energy_consumption.query"
    elif obj == "fan":
        name = f"fan.{_on_off_map.get(operate, operate)}"
    elif obj == "step_heating":
        name = f"step_heating.{_on_off_map.get(operate, operate)}"
    elif obj == "camera":
        name = f"camera.{_on_off_map.get(operate, operate)}"
    elif obj == "car_link":
        name = f"car_link.{_on_off_map.get(operate, operate)}"
    elif obj == "team":
        name = f"team.{operate}"
    elif obj == "tire_temperature":
        name = f"tire_temperature.{_on_off_map.get(operate, operate)}"
    else:
        name = f"{obj}.{operate}"

    # 构造旧 slots
    slots = {}
    if obj == "aircon":
        if mode == "wind_speed" and data.get("value"):
            slots["value"] = data["value"]
        elif data.get("value"):
            slots["temp"] = data["value"]
    elif data.get("value"):
        slots["value"] = data["value"]
    if data.get("mode") and obj in ("scene_mode", "power_mode"):
        slots["mode"] = data["mode"]
    if data.get("tag"):
        slots["tag"] = data["tag"]

    return _i(name, slots, result.get("confidence", 0.9))


def classify_structured(text: str) -> dict | None:
    """新接口：返回公版 {domain, intent, data: {operate, object, ...}} 格式。"""
    t = text.strip()

    # ── 空调 ──────────────────────────────────────────────
    if ("空调" in t and "界面" not in t and "页面" not in t) or \
            ("温度" in t and "查" not in t and "几度" not in t and "多少" not in t) or \
            "风速" in t or "风量" in t or \
            (("热" in t or "冷" in t) and ("度" in t or "一点" in t or "再" in t)):
        if "关" in t:
            return _s("setting", "control", "close", "aircon", conf=0.93)
        # 风速/风量
        if "风速" in t or "风量" in t:
            if "大" in t or "高" in t:
                return _s("setting", "control", "inc", "aircon", mode="wind_speed", conf=0.9)
            if "小" in t or "低" in t:
                return _s("setting", "control", "dec", "aircon", mode="wind_speed", conf=0.9)
            m = re.search(r"(\d)\s*挡", t)
            if m:
                return _s("setting", "control", "set", "aircon", mode="wind_speed",
                          value=m.group(1), unit="level", conf=0.9)
            return _s("setting", "control", "set", "aircon", mode="wind_speed", conf=0.85)
        temperature = _extract_temperature(t)
        # 温度增减（相对，无具体度数）
        if ("调高" in t or "高一点" in t or "热一点" in t or "再热" in t) \
                and temperature is None:
            return _s("setting", "control", "inc", "aircon", conf=0.88)
        if ("调低" in t or "低一点" in t or "冷一点" in t or "再冷" in t) \
                and temperature is None:
            return _s("setting", "control", "dec", "aircon", conf=0.88)
        # 温度设定（绝对）
        if temperature is not None:
            return _s("setting", "control", "set", "aircon",
                      value=str(temperature), unit="degree", conf=0.95)
        if "热" in t or "高" in t:
            return _s("setting", "control", "set", "aircon",
                      value="26", unit="degree", conf=0.88)
        # 内/外循环
        if "内循环" in t:
            return _s("setting", "control", "set", "aircon", mode="internal", conf=0.9)
        if "外循环" in t:
            return _s("setting", "control", "set", "aircon", mode="external", conf=0.9)
        if "除雾" in t:
            return _s("setting", "control", "set", "aircon", mode="除雾", conf=0.9)
        if "除霜" in t:
            return _s("setting", "control", "set", "aircon", mode="除霜", conf=0.9)
        return _s("setting", "control", "open", "aircon", conf=0.9)

    # ── 车窗 ──────────────────────────────────────────────
    if "车窗" in t or "窗户" in t:
        pos = _extract_position(t)
        if "关" in t:
            return _s("setting", "control", "close", "window",
                      positions=pos, conf=0.92)
        # 开度（百分比或"一半"）
        pct = _extract_percentage(t)
        if pct is not None:
            return _s("setting", "control", "set", "window",
                      value=str(pct), unit="percent", positions=pos, conf=0.92)
        if "开" in t:
            return _s("setting", "control", "open", "window",
                      positions=pos, conf=0.92)

    # ── 天窗 ──────────────────────────────────────────────
    if "天窗" in t:
        if "关" in t:
            return _s("setting", "control", "close", "sunroof", conf=0.92)
        pct = _extract_percentage(t)
        if pct is not None:
            return _s("setting", "control", "set", "sunroof",
                      value=str(pct), unit="percent", conf=0.92)
        return _s("setting", "control", "open", "sunroof", conf=0.92)

    # ── 遮阳帘 ────────────────────────────────────────────
    if "遮阳帘" in t or "遮阳" in t:
        pos = _extract_position(t)
        if "关" in t:
            return _s("setting", "control", "close", "sunshade",
                      positions=pos, conf=0.9)
        pct = _extract_percentage(t)
        if pct is not None:
            return _s("setting", "control", "set", "sunshade",
                      value=str(pct), unit="percent", positions=pos, conf=0.9)
        return _s("setting", "control", "open", "sunshade",
                  positions=pos, conf=0.9)

    # ── 位置+功能简写（后排通风/前排加热/后排灯 等，省略"座椅"/"灯"）──
    _pos_func = _extract_position(t)
    if _pos_func:
        # 位置 + 加热/通风/按摩 → 座椅
        for func_word, mode in [("加热", "heating"), ("通风", "ventilation"), ("按摩", "massage")]:
            if func_word in t:
                m = re.search(r"(\d)\s*挡", t)
                if m:
                    return _s("setting", "control", "set", "seat",
                              mode=mode, value=m.group(1), unit="level", positions=_pos_func, conf=0.88)
                operate = "close" if "关" in t else "open"
                return _s("setting", "control", operate, "seat",
                          mode=mode, positions=_pos_func, conf=0.88)
        # 位置 + 灯 → 车内灯/氛围灯
        if "灯" in t:
            operate = "close" if "关" in t else "open"
            if "氛围" in t or "彩色" in t:
                return _s("setting", "control", operate, "ambient_light", positions=_pos_func, conf=0.85)
            return _s("setting", "control", operate, "ambient_light", positions=_pos_func, conf=0.85)

    # ── 座椅 ──────────────────────────────────────────────
    if "座椅" in t or "座位" in t or "腰托" in t or "腰部支撑" in t:
        pos = _extract_position(t)
        # 模式识别
        mode = None
        if "加热" in t:
            mode = "heating"
        elif "通风" in t:
            mode = "ventilation"
        elif "按摩" in t:
            mode = "massage"
        elif "腰" in t:
            mode = "lumbar_support"

        if "关" in t:
            return _s("setting", "control", "close", "seat",
                      mode=mode, positions=pos, conf=0.9)
        # 增减
        if ("调高" in t or "升高" in t) and mode:
            return _s("setting", "control", "inc", "seat",
                      mode=mode, positions=pos, conf=0.9)
        if ("调低" in t or "降低" in t) and mode:
            return _s("setting", "control", "dec", "seat",
                      mode=mode, positions=pos, conf=0.9)
        # 挡位
        m = re.search(r"(\d)\s*挡", t)
        if m and mode:
            return _s("setting", "control", "set", "seat",
                      mode=mode, value=m.group(1), unit="level",
                      positions=pos, conf=0.9)
        if mode:
            return _s("setting", "control", "open", "seat",
                      mode=mode, positions=pos, conf=0.9)
        return _s("setting", "control", "open", "seat",
                  positions=pos, conf=0.85)

    # ── 后备箱 ────────────────────────────────────────────
    if "后备箱" in t or "尾箱" in t or "尾门" in t:
        if "关" in t:
            return _s("setting", "control", "close", "trunk", conf=0.9)
        return _s("setting", "control", "open", "trunk", conf=0.9)

    # ── 车门锁 ────────────────────────────────────────────
    if "车门" in t or "门锁" in t or "解锁" in t or "上锁" in t:
        pos = _extract_position(t)
        if "锁" in t and "解" not in t:
            return _s("setting", "control", "close", "door_lock",
                      positions=pos, conf=0.9)
        return _s("setting", "control", "open", "door_lock",
                  positions=pos, conf=0.9)

    # ── 氛围灯 ────────────────────────────────────────────
    if "氛围灯" in t or "氛围" in t:
        # 亮度调节（优先于开/关）
        if "亮度" in t or "亮" in t or "暗" in t:
            if "高" in t or "亮" in t or "大" in t:
                return _s("setting", "control", "inc", "ambient_light",
                          mode="brightness", conf=0.9)
            if "低" in t or "暗" in t or "小" in t:
                return _s("setting", "control", "dec", "ambient_light",
                          mode="brightness", conf=0.9)
            m = re.search(r"(\d)\s*挡", t)
            if m:
                return _s("setting", "control", "set", "ambient_light",
                          mode="brightness", value=m.group(1), unit="level", conf=0.9)
        if "关" in t:
            return _s("setting", "control", "close", "ambient_light", conf=0.9)
        # 颜色
        color = _extract_color(t)
        if color:
            return _s("setting", "control", "set", "ambient_light",
                      tag=color, conf=0.9)
        return _s("setting", "control", "open", "ambient_light", conf=0.9)

    # ── 大灯 / 远光灯 ────────────────────────────────────
    if "大灯" in t or "远光" in t:
        # 远光灯高度调节（优先于开/关）
        if ("远光" in t or "大灯" in t) and ("高度" in t or "调高" in t or "调低" in t):
            if "高" in t or "升" in t:
                return _s("setting", "control", "inc", "high_beam",
                          mode="height", conf=0.9)
            if "低" in t or "降" in t:
                return _s("setting", "control", "dec", "high_beam",
                          mode="height", conf=0.9)
            m = re.search(r"(\d)\s*挡", t)
            if m:
                return _s("setting", "control", "set", "high_beam",
                          mode="height", value=m.group(1), unit="level", conf=0.9)
        if "关" in t:
            return _s("setting", "control", "close", "headlight", conf=0.9)
        return _s("setting", "control", "open", "headlight", conf=0.9)

    # ── 近光灯 ────────────────────────────────────────────
    if "近光灯" in t or "近光" in t:
        if "关" in t:
            return _s("setting", "control", "close", "low_beam", conf=0.9)
        return _s("setting", "control", "open", "low_beam", conf=0.9)

    # ── 雨刷 / 雨刮 ─────────────────────────────────────
    if "雨刷" in t or "雨刮" in t:
        if "关" in t:
            return _s("setting", "control", "close", "wiper", conf=0.9)
        # 灵敏度调节（优先于速度）
        if "灵敏" in t:
            if "高" in t or "大" in t:
                return _s("setting", "control", "inc", "wiper",
                          mode="sensitivity", conf=0.9)
            if "低" in t or "小" in t:
                return _s("setting", "control", "dec", "wiper",
                          mode="sensitivity", conf=0.9)
            lv = _extract_level(t)
            if lv:
                return _s("setting", "control", "set", "wiper",
                          mode="sensitivity", value=lv, unit="level", conf=0.9)
        # 速度挡位
        if "快" in t or "大" in t:
            return _s("setting", "control", "inc", "wiper", mode="speed", conf=0.9)
        if "慢" in t or "小" in t:
            return _s("setting", "control", "dec", "wiper", mode="speed", conf=0.9)
        lv = _extract_level(t)
        if lv:
            return _s("setting", "control", "set", "wiper", mode="speed",
                      value=lv, unit="level", conf=0.9)
        return _s("setting", "control", "open", "wiper", conf=0.9)

    # ── 后视镜 ────────────────────────────────────────────
    if "后视镜" in t:
        pos = _extract_position(t)
        if "折叠" in t or "收" in t:
            return _s("setting", "control", "set", "rear_view_mirror",
                      mode="fold", positions=pos, conf=0.9)
        if "展开" in t or "打开" in t:
            return _s("setting", "control", "set", "rear_view_mirror",
                      mode="unfold", positions=pos, conf=0.9)

    # ── 香氛 ──────────────────────────────────────────────
    if "香氛" in t or "香薰" in t:
        if "关" in t:
            return _s("setting", "control", "close", "fragrance", conf=0.9)
        m = re.search(r"(\d)\s*挡", t)
        if m:
            return _s("setting", "control", "set", "fragrance",
                      value=m.group(1), unit="level", conf=0.9)
        return _s("setting", "control", "open", "fragrance", conf=0.9)

    # ── 音量 ──────────────────────────────────────────────
    if "音量" in t:
        m = re.search(r"(\d+)", t)
        if "大" in t or "高" in t:
            return _s("setting", "control", "inc", "volume", conf=0.9)
        if "小" in t or "低" in t:
            return _s("setting", "control", "dec", "volume", conf=0.9)
        if m:
            return _s("setting", "control", "set", "volume",
                      value=m.group(1), unit="level", conf=0.9)

    # ── 驾驶模式 ──────────────────────────────────────────
    if "驾驶模式" in t or "运动模式" in t or "节能模式" in t or "舒适模式" in t:
        mode = None
        if "运动" in t:
            mode = "sport"
        elif "节能" in t or "经济" in t:
            mode = "eco"
        elif "舒适" in t:
            mode = "comfort"
        elif "雪地" in t:
            mode = "snow"
        elif "越野" in t:
            mode = "offroad"
        if mode:
            return _s("setting", "control", "set", "driving_mode",
                      mode=mode, conf=0.9)

    # ── 油箱盖 ────────────────────────────────────────────
    if "油箱盖" in t or "加油口" in t or "油箱口" in t:
        if "关" in t:
            return _s("setting", "control", "close", "fuel_tank_cover", conf=0.93)
        return _s("setting", "control", "open", "fuel_tank_cover", conf=0.93)

    # ── 充电口盖 ──────────────────────────────────────────
    if "充电口" in t or "充电盖" in t:
        if "关" in t:
            return _s("setting", "control", "close", "charging_port", conf=0.93)
        return _s("setting", "control", "open", "charging_port", conf=0.93)

    # ── 胎压监测 ──────────────────────────────────────────
    if "胎压" in t or "轮胎气压" in t:
        return _s("query", "query", "query", "tire_pressure", conf=0.92)

    # ── 行车记录仪 ────────────────────────────────────────
    if "行车记录仪" in t or "记录仪" in t:
        if "关" in t:
            return _s("setting", "control", "close", "dashcam", conf=0.9)
        return _s("setting", "control", "open", "dashcam", conf=0.9)

    # ── 场景模式 ──────────────────────────────────────────
    if "小憩" in t or "露营" in t or "观影" in t or "浪漫" in t or "冥想" in t:
        mode = None
        if "小憩" in t or "小睡" in t:
            mode = "nap"
        elif "露营" in t:
            mode = "camping"
        elif "观影" in t or "看电影" in t:
            mode = "movie"
        elif "浪漫" in t:
            mode = "romantic"
        elif "冥想" in t:
            mode = "meditation"
        if mode:
            return _s("setting", "control", "set", "scene_mode",
                      mode=mode, conf=0.9)

    # ── 电源模式 ──────────────────────────────────────────
    if "电源模式" in t or "动力模式" in t:
        mode = None
        if "运动" in t:
            mode = "sport"
        elif "节能" in t or "经济" in t:
            mode = "eco"
        elif "标准" in t:
            mode = "normal"
        if mode:
            return _s("setting", "control", "set", "power_mode",
                      mode=mode, conf=0.9)

    # ── 动能回收 ──────────────────────────────────────────
    if "动能回收" in t or "能量回收" in t or "回收等级" in t:
        if "高" in t or "大" in t:
            return _s("setting", "control", "inc", "energy_recovery", conf=0.9)
        if "低" in t or "小" in t:
            return _s("setting", "control", "dec", "energy_recovery", conf=0.9)
        m = re.search(r"(\d)\s*挡", t)
        if m:
            return _s("setting", "control", "set", "energy_recovery",
                      value=m.group(1), unit="level", conf=0.9)
        return _s("setting", "control", "set", "energy_recovery", conf=0.85)

    # ── 车道偏离预警 ──────────────────────────────────────
    if "车道偏离" in t or "偏离预警" in t:
        if "关" in t:
            return _s("setting", "control", "close", "lane_departure_assistance", conf=0.9)
        return _s("setting", "control", "open", "lane_departure_assistance", conf=0.9)

    # ── 车道保持/车道辅助 ─────────────────────────────────
    if "车道保持" in t or "车道辅助" in t:
        if "关" in t:
            return _s("setting", "control", "close", "lane_assistance", conf=0.9)
        return _s("setting", "control", "open", "lane_assistance", conf=0.9)

    # ── 方向盘（加热/高度）────────────────────────────────
    if "方向盘" in t:
        if "加热" in t:
            if "关" in t:
                return _s("setting", "control", "close", "steering_wheel",
                          mode="heating", conf=0.9)
            return _s("setting", "control", "open", "steering_wheel",
                      mode="heating", conf=0.9)
        if "调高" in t or "升高" in t or "高" in t:
            return _s("setting", "control", "inc", "steering_wheel",
                      mode="height", conf=0.9)
        if "调低" in t or "降低" in t or "低" in t:
            return _s("setting", "control", "dec", "steering_wheel",
                      mode="height", conf=0.9)
        m = re.search(r"(\d)\s*挡", t)
        if m:
            return _s("setting", "control", "set", "steering_wheel",
                      mode="height", value=m.group(1), unit="level", conf=0.9)

    # ── 屏幕亮度 ──────────────────────────────────────────
    if "屏幕" in t:
        if "关" in t or "息屏" in t:
            return _s("setting", "control", "close", "screen", conf=0.9)
        # 具体数值设定（优先于相对增减，因为 "亮度调到50" 含 "亮" 但意图是 set）
        m = re.search(r"(\d+)", t)
        if m and ("调到" in t or "设为" in t or "设到" in t):
            return _s("setting", "control", "set", "screen",
                      mode="brightness", value=m.group(1), unit="percent", conf=0.9)
        # 调低/低 必须在 亮 之前检查，因为 "亮度调低" 同时含 "亮" 和 "低"
        if "调低" in t or "低" in t or "暗" in t or "调暗" in t:
            return _s("setting", "control", "dec", "screen",
                      mode="brightness", conf=0.9)
        if "调高" in t or "高" in t or "亮" in t or "调亮" in t:
            return _s("setting", "control", "inc", "screen",
                      mode="brightness", conf=0.9)
        if m:
            return _s("setting", "control", "set", "screen",
                      mode="brightness", value=m.group(1), unit="percent", conf=0.9)
        return _s("setting", "control", "open", "screen", conf=0.85)

    # ══════════════════════════════════════════════════════════
    # 飞书意图表新增对象（2026-06 扩展）
    # 注意：这些必须在页面/应用泛化匹配之前，避免被 catch-all 截获
    # ══════════════════════════════════════════════════════════

    # ── 蓝牙 ──────────────────────────────────────────────
    if "蓝牙" in t:
        if "断" in t or "断开" in t:
            return _s("setting", "control", "disconnect", "bluetooth", conf=0.9)
        if "连" in t and "断" not in t:
            return _s("setting", "control", "connect", "bluetooth", conf=0.9)
        if "关" in t:
            return _s("setting", "control", "close", "bluetooth", conf=0.9)
        if "打开" in t or "开" in t or "开启" in t:
            return _s("setting", "control", "open", "bluetooth", conf=0.9)
        return _s("setting", "control", "open", "bluetooth", conf=0.85)

    # ── WiFi ─────────────────────────────────────────────
    if "wifi" in t.lower() or "wi-fi" in t.lower() or "无线网" in t:
        if "断" in t or "断开" in t:
            return _s("setting", "control", "disconnect", "wifi", conf=0.9)
        if "连" in t and "断" not in t:
            return _s("setting", "control", "connect", "wifi", conf=0.9)
        if "关" in t:
            return _s("setting", "control", "close", "wifi", conf=0.9)
        if "打开" in t or "开" in t or "开启" in t:
            return _s("setting", "control", "open", "wifi", conf=0.9)
        return _s("setting", "control", "open", "wifi", conf=0.85)

    # ── 个人热点 ──────────────────────────────────────────
    if "热点" in t and "列表" not in t:
        if "关" in t:
            return _s("setting", "control", "close", "hotspot", conf=0.9)
        return _s("setting", "control", "open", "hotspot", conf=0.9)

    # ── 自动驻车 / 制动 ──────────────────────────────────
    if "自动驻车" in t or "AHV" in t.upper():
        if "关" in t:
            return _s("setting", "control", "close", "auto_hold", conf=0.9)
        return _s("setting", "control", "open", "auto_hold", conf=0.9)

    # ── 电子手刹 ──────────────────────────────────────────
    if "电子手刹" in t or "手刹" in t:
        if "关" in t or "松" in t or "解" in t:
            return _s("setting", "control", "close", "epb", conf=0.9)
        return _s("setting", "control", "open", "epb", conf=0.9)

    # ── 主菜单 / 桌面 ─────────────────────────────────────
    if "桌面" in t or "主菜单" in t or "回主页" in t:
        if "返回" in t or "回" in t:
            return _s("setting", "control", "return", "launcher", conf=0.9)
        return _s("hmi", "navigate", "open", "page", tag="home", conf=0.88)

    # ── 均衡器 / 音效 ────────────────────────────────────
    if "音效" in t or "均衡器" in t or "EQ" in t.upper() or "DTS" in t.upper():
        if "关" in t:
            return _s("setting", "control", "close", "equalizer", conf=0.9)
        _eq_modes = {
            "摇滚": "rock", "流行": "pop", "古典": "classical",
            "爵士": "jazz", "乡村": "country", "自定义": "custom",
            "标准": "standard", "原声": "original",
        }
        for kw, mode in _eq_modes.items():
            if kw in t:
                return _s("setting", "control", "set", "sound_effect",
                          mode=mode, conf=0.9)
        if "切换" in t or "模式" in t:
            return _s("setting", "control", "switch", "sound_effect", conf=0.85)
        return _s("setting", "control", "open", "equalizer", conf=0.88)

    # ── 语音助手 ──────────────────────────────────────────
    if "语音助手" in t or "语音设置" in t or "语音唤醒" in t \
            or "一语直达" in t or "全时对话" in t or "连续对话" in t:
        if "关" in t:
            return _s("setting", "control", "close", "voice_assistant", conf=0.9)
        if "开" in t or "打开" in t:
            return _s("setting", "control", "open", "voice_assistant", conf=0.9)
        return _s("setting", "control", "open", "voice_assistant", conf=0.85)
    if "停止播报" in t or "停语音播报" in t or "停播报" in t:
        return _s("setting", "control", "stop", "voice_assistant", conf=0.9)

    # ── 系统 ─────────────────────────────────────────────
    if "恢复出厂" in t or "出厂设置" in t or "复出厂" in t:
        return _s("setting", "control", "restore", "factory_settings", conf=0.92)
    if "系统更新" in t or "系统升级" in t:
        return _s("setting", "control", "update", "system", conf=0.9)
    if "内存清理" in t or "清理内存" in t:
        return _s("setting", "control", "clean", "memory", conf=0.9)
    if "剩余流量" in t or "可用流量" in t or "查流量" in t:
        return _s("query", "query", "query", "remaining_network_data", conf=0.9)
    if "买流量" in t or "购买流量" in t:
        return _s("setting", "control", "buy", "network_data", conf=0.9)
    if "系统语言" in t or "语言切换" in t:
        return _s("setting", "control", "set", "language", conf=0.9)
    if "时间格式" in t or "时间设置" in t:
        return _s("setting", "control", "set", "time_format", conf=0.9)

    # ── 360环视 / 倒车影像 ────────────────────────────────
    if "360" in t or "全景影像" in t or "环视" in t or "倒车影像" in t:
        if "关" in t:
            return _s("app", "control", "close", "surround_view", conf=0.9)
        return _s("app", "control", "open", "surround_view", conf=0.9)

    # ── 仪表设置 ──────────────────────────────────────────
    if "仪表" in t:
        if "关" in t:
            return _s("setting", "control", "close", "dashboard", conf=0.9)
        return _s("setting", "control", "open", "dashboard", conf=0.9)

    # ── 电话 ─────────────────────────────────────────────
    if "接听" in t or "接电话" in t:
        return _s("phone", "control", "answer", "phone", conf=0.95)
    if "挂断" in t or "挂电话" in t or "挂一挂" in t:
        return _s("phone", "control", "hangup", "phone", conf=0.95)
    if "回拨" in t or "重拨" in t:
        return _s("phone", "control", "callback", "phone", conf=0.9)
    if "打电话" in t or "拨打" in t or "拨电话" in t or "拨给" in t:
        return _s("phone", "control", "call", "phone", conf=0.9)

    # ── 通讯录 ────────────────────────────────────────────
    if "通讯录" in t or "联系人" in t:
        if "关" in t or "退" in t:
            return _s("phone", "control", "close", "contacts", conf=0.9)
        if "查" in t or "找" in t or "搜" in t:
            return _s("query", "query", "query", "contacts", conf=0.9)
        return _s("phone", "control", "open", "contacts", conf=0.9)

    # ── 通话记录 ──────────────────────────────────────────
    if "通话记录" in t or "未接来电" in t or "已接电话" in t or "已拨电话" in t:
        if "关" in t:
            return _s("phone", "control", "close", "call_log", conf=0.9)
        return _s("phone", "control", "open", "call_log", conf=0.9)

    # ── 广播 / 收音机 / 电台 ─────────────────────────────
    if "广播" in t or "收音机" in t or ("电台" in t and "网络" not in t):
        if "关" in t:
            return _s("media", "control", "close", "radio", conf=0.9)
        if "打开" in t or "开" in t:
            return _s("media", "control", "open", "radio", conf=0.9)
        m = re.search(r"(\d+\.?\d*)\s*(?:MHz|兆赫|mhz)", t, re.IGNORECASE)
        if m:
            return _s("media", "control", "play", "radio",
                      tag=f"FM{m.group(1)}", conf=0.92)
        m = re.search(r"(\d+\.?\d*)\s*(?:KHz|千赫|khz)", t, re.IGNORECASE)
        if m:
            return _s("media", "control", "play", "radio",
                      tag=f"AM{m.group(1)}", conf=0.92)
        if "播" in t or "放" in t or "听" in t:
            return _s("media", "control", "play", "radio", conf=0.88)
        return _s("media", "control", "open", "radio", conf=0.85)

    # ── 网络电台 ──────────────────────────────────────────
    if "网络电台" in t:
        if "关" in t:
            return _s("media", "control", "close", "online_radio", conf=0.9)
        if "播" in t or "放" in t or "听" in t:
            return _s("media", "control", "play", "online_radio", conf=0.88)
        return _s("media", "control", "open", "online_radio", conf=0.85)

    # ── 音乐 ─────────────────────────────────────────────
    if "音乐" in t or "歌曲" in t or ("歌" in t and "鸽" not in t) \
            or "放首" in t or "来首" in t:
        if "关" in t:
            return _s("app", "control", "close", "music", conf=0.9)
        if "暂停" in t or "停" in t:
            return _s("app", "control", "pause", "music", conf=0.9)
        if "下一首" in t or "下一曲" in t or "切歌" in t:
            return _s("app", "control", "switch", "music", conf=0.9)
        if "上一首" in t or "上一曲" in t:
            return _s("app", "control", "switch", "music", conf=0.9)
        if "继续" in t:
            return _s("app", "control", "resume", "music", conf=0.9)
        if "播" in t or "放" in t or "听" in t or "推荐" in t:
            return _s("app", "control", "play", "music", conf=0.88)
        return _s("app", "control", "open", "music", conf=0.85)

    # ── 有声书 ────────────────────────────────────────────
    if "有声书" in t or "听书" in t:
        if "关" in t or "停" in t:
            return _s("media", "control", "stop", "audiobook", conf=0.9)
        if "上一章" in t or "上一集" in t:
            return _s("media", "control", "prev", "audiobook", conf=0.9)
        if "下一章" in t or "下一集" in t:
            return _s("media", "control", "next", "audiobook", conf=0.9)
        if "暂停" in t:
            return _s("media", "control", "pause", "audiobook", conf=0.9)
        if "播" in t or "放" in t or "听" in t or "打开" in t or "开" in t:
            return _s("media", "control", "play", "audiobook", conf=0.88)
        return _s("media", "control", "open", "audiobook", conf=0.85)

    # ── 曲艺（戏曲/相声/评书）────────────────────────────
    if "戏曲" in t or "相声" in t or "评书" in t or "曲艺" in t:
        if "关" in t or "停" in t:
            return _s("media", "control", "stop", "opera", conf=0.9)
        if "上一个" in t:
            return _s("media", "control", "prev", "opera", conf=0.9)
        if "下一个" in t:
            return _s("media", "control", "next", "opera", conf=0.9)
        if "播" in t or "放" in t or "听" in t:
            return _s("media", "control", "play", "opera", conf=0.88)
        return _s("media", "control", "open", "opera", conf=0.85)

    # ── 新闻 ─────────────────────────────────────────────
    if "新闻" in t or "头条" in t:
        if "播" in t or "放" in t or "听" in t:
            return _s("media", "control", "play", "news", conf=0.88)
        if "关" in t or "停" in t:
            return _s("media", "control", "stop", "news", conf=0.9)
        return _s("media", "control", "play", "news", conf=0.85)

    # ── 视频 ─────────────────────────────────────────────
    if "视频" in t or "电影" in t or "电视剧" in t or "脱口秀" in t:
        if "退出全屏" in t:
            return _s("app", "control", "close", "video", mode="full_screen", conf=0.9)
        if "全屏" in t:
            return _s("app", "control", "open", "video", mode="full_screen", conf=0.9)
        if "倍速" in t:
            return _s("app", "control", "set", "video", attr="playback_speed", conf=0.9)
        if "快进" in t:
            return _s("app", "control", "forward", "video", conf=0.9)
        if "快退" in t or "后退" in t:
            return _s("app", "control", "backward", "video", conf=0.9)
        if "关" in t:
            return _s("app", "control", "close", "video", conf=0.9)
        if "暂停" in t or "停" in t:
            return _s("app", "control", "pause", "video", conf=0.9)
        if "继续" in t:
            return _s("app", "control", "resume", "video", conf=0.9)
        if "播" in t or "放" in t or "看" in t:
            return _s("app", "control", "play", "video", conf=0.88)
        return _s("app", "control", "open", "video", conf=0.85)

    # ── 电视 ─────────────────────────────────────────────
    if "电视" in t:
        if "关" in t:
            return _s("app", "control", "close", "TV", conf=0.9)
        return _s("app", "control", "open", "TV", conf=0.9)

    # ── 前备箱 ────────────────────────────────────────────
    if "前备箱" in t or "前行李箱" in t or "前行李舱" in t:
        if "关" in t:
            return _s("setting", "control", "close", "frunk", conf=0.9)
        return _s("setting", "control", "open", "frunk", conf=0.9)

    # ── 导航（导航到/去/怎么走 等意图短语）────────────────
    if ("导航" in t or "去" in t or "到" in t) and \
            ("怎么走" in t or "多远" in t or "多久" in t or "路线" in t):
        if "多远" in t or "距离" in t:
            return _s("navi", "query", "query", "remaining_distance", conf=0.85)
        if "多久" in t or "时间" in t:
            return _s("navi", "query", "query", "remaining_time", conf=0.85)
        if "路线" in t:
            return _s("navi", "query", "query", "navigation_route", conf=0.85)
        return _s("navi", "plan", "plan", "navi", conf=0.8)
    if "导航" in t and ("到" in t or "去" in t or "目的地" in t):
        return _s("navi", "plan", "plan", "navi", conf=0.85)
    if "取消导航" in t or "退出导航" in t or "结束导航" in t:
        return _s("navi", "control", "cancel", "navi", conf=0.9)
    if "开始导航" in t or "发起导航" in t:
        return _s("navi", "control", "start", "navi", conf=0.9)
    if "继续导航" in t or "恢复导航" in t:
        return _s("navi", "control", "resume", "navi", conf=0.9)
    if "当前位置" in t or "我在哪" in t:
        return _s("navi", "query", "locate", "current_position", conf=0.9)
    if "实时路况" in t or "前方路况" in t:
        if "关" in t:
            return _s("navi", "control", "close", "road_condition", conf=0.9)
        return _s("navi", "query", "query", "road_condition", conf=0.9)

    # ── 地图（放大/缩小/视图切换）────────────────────────
    if "地图" in t:
        if "放大" in t or "大一点" in t:
            return _s("app", "control", "zoom_in", "map", conf=0.9)
        if "缩小" in t or "小一点" in t:
            return _s("app", "control", "zoom_out", "map", conf=0.9)
        if "最大" in t:
            return _s("app", "control", "zoom_in", "map", limit="max", conf=0.9)
        if "最小" in t:
            return _s("app", "control", "zoom_out", "map", limit="min", conf=0.9)
        if "3D" in t.upper() or "三维" in t:
            return _s("app", "control", "set", "map", mode="3d", conf=0.9)
        if "关" in t:
            return _s("app", "control", "close", "map", conf=0.9)
        if "收藏" in t:
            return _s("app", "control", "open", "map", tag="favorites", conf=0.88)
        return _s("app", "control", "open", "map", conf=0.85)

    # ── 天气 / 温度 / 湿度 / 风况 / 空气质量 ─────────────
    if "天气" in t:
        return _s("query", "query", "query", "weather", conf=0.9)
    if "温度" in t and ("查" in t or "多少" in t or "几度" in t):
        return _s("query", "query", "query", "temperature", conf=0.9)
    if "湿度" in t and ("查" in t or "多少" in t):
        return _s("query", "query", "query", "humidity", conf=0.9)
    if "风力" in t or "风况" in t or "几级风" in t:
        return _s("query", "query", "query", "wind_force", conf=0.9)
    if "空气质量" in t or "PM2.5" in t or "空气指数" in t:
        return _s("query", "query", "query", "air_quality", conf=0.9)

    # ── 美食 ─────────────────────────────────────────────
    if "美食" in t or "餐厅" in t or "找吃的" in t or "有什么吃的" in t:
        return _s("navi", "query", "query", "food", conf=0.85)
    _cuisines = ["川菜", "湘菜", "粤菜", "火锅", "烧烤", "日料",
                 "西餐", "意大利菜", "韩餐", "自助餐", "外卖", "奶茶"]
    for cuisine in _cuisines:
        if cuisine in t and ("附近" in t or "找" in t or "查" in t
                             or "哪有" in t or "店" in t):
            return _s("navi", "query", "query", "food", tag=cuisine, conf=0.85)

    # ── 酒店 ─────────────────────────────────────────────
    if "酒店" in t or "住宿" in t or "度假村" in t or "民宿" in t:
        if "查" in t or "找" in t or "附近" in t or "订" in t:
            return _s("navi", "query", "query", "hotel", conf=0.85)
        return _s("navi", "query", "query", "hotel", conf=0.8)

    # ── 航班 ─────────────────────────────────────────────
    if "航班" in t or "机票" in t or "飞机" in t:
        return _s("information", "query", "query", "flight", conf=0.88)

    # ── 火车票 ───────────────────────────────────────────
    if "火车票" in t or "火车" in t or "高铁" in t or "动车" in t:
        return _s("information", "query", "query", "train", conf=0.88)

    # ── 股票 ─────────────────────────────────────────────
    if "股票" in t or "股价" in t or "大盘" in t or "指数" in t:
        return _s("information", "query", "query", "stock", conf=0.88)

    # ── 车内灯（阅读灯/化妆灯/脚窝灯/动态氛围灯等）──────
    if "阅读灯" in t or "化妆灯" in t or "脚窝灯" in t or "门灯" in t:
        if "关" in t:
            return _s("setting", "control", "close", "ambient_light", conf=0.9)
        return _s("setting", "control", "open", "ambient_light", conf=0.9)
    if "动态氛围灯" in t or "律动" in t:
        if "关" in t:
            return _s("setting", "control", "close", "ambient_light",
                      mode="dynamic", conf=0.9)
        return _s("setting", "control", "open", "ambient_light",
                  mode="dynamic", conf=0.9)

    # ── 车外灯（雾灯、双闪等）────────────────────────────
    if "雾灯" in t:
        if "关" in t:
            return _s("setting", "control", "close", "fog_light", conf=0.9)
        return _s("setting", "control", "open", "fog_light", conf=0.9)
    if "双闪" in t or "警示灯" in t or "危险灯" in t:
        if "关" in t:
            return _s("setting", "control", "close", "warning_light", conf=0.9)
        return _s("setting", "control", "open", "warning_light", conf=0.9)

    # ── 轮胎（胎温监测等扩展）────────────────────────────
    if "胎温" in t:
        if "关" in t:
            return _s("setting", "control", "close", "tire_temperature", conf=0.9)
        return _s("setting", "control", "open", "tire_temperature", conf=0.9)

    # ── 辅助驾驶（扩展：ACC/盲区/车身稳定等）─────────────
    if "自适应巡航" in t or "ACC" in t.upper():
        if "关" in t:
            return _s("setting", "control", "close", "cruise_following", conf=0.9)
        return _s("setting", "control", "open", "cruise_following", conf=0.9)
    if "盲区" in t:
        if "关" in t:
            return _s("setting", "control", "close", "blind_spot_warning", conf=0.9)
        return _s("setting", "control", "open", "blind_spot_warning", conf=0.9)
    if "车身稳定" in t or "ESP" in t.upper() or "ESC" in t.upper():
        if "关" in t:
            return _s("setting", "control", "close", "body_stability", conf=0.9)
        return _s("setting", "control", "open", "body_stability", conf=0.9)
    if "陡坡缓降" in t:
        if "关" in t:
            return _s("setting", "control", "close", "hill_descent", conf=0.9)
        return _s("setting", "control", "open", "hill_descent", conf=0.9)
    if "蠕行模式" in t or "蠕行" in t:
        if "关" in t:
            return _s("setting", "control", "close", "creep_mode", conf=0.9)
        return _s("setting", "control", "open", "creep_mode", conf=0.9)
    if "前碰撞预警" in t or "前向碰撞" in t:
        if "关" in t:
            return _s("setting", "control", "close", "forward_collision_warning", conf=0.9)
        return _s("setting", "control", "open", "forward_collision_warning", conf=0.9)
    if "疲劳驾驶" in t or "疲劳检测" in t:
        if "关" in t:
            return _s("setting", "control", "close", "fatigue_detection", conf=0.9)
        return _s("setting", "control", "open", "fatigue_detection", conf=0.9)
    if "限速" in t and ("辅助" in t or "提醒" in t or "控制" in t):
        if "关" in t:
            return _s("setting", "control", "close", "speed_limit_assistance", conf=0.9)
        return _s("setting", "control", "open", "speed_limit_assistance", conf=0.9)

    # ── 能源（扩展：V2V充电/电池预热/定时充电）──────────
    if "车对车充电" in t or "V2V" in t.upper():
        if "关" in t:
            return _s("setting", "control", "close", "v2v_charging", conf=0.9)
        return _s("setting", "control", "open", "v2v_charging", conf=0.9)
    if "电池预热" in t:
        if "关" in t:
            return _s("setting", "control", "close", "battery_preheat", conf=0.9)
        return _s("setting", "control", "open", "battery_preheat", conf=0.9)
    if "定时充电" in t:
        if "关" in t:
            return _s("setting", "control", "close", "scheduled_charging", conf=0.9)
        return _s("setting", "control", "open", "scheduled_charging", conf=0.9)
    if "能耗" in t or "电量" in t or "续航" in t:
        return _s("query", "query", "query", "energy_consumption", conf=0.88)
    if "熄火" in t or "关电源" in t or "断电" in t:
        return _s("setting", "control", "power_off", "vehicle", conf=0.85)

    # ── 风扇 ─────────────────────────────────────────────
    if "风扇" in t and "空调" not in t:
        pos = _extract_position(t)
        if "关" in t:
            return _s("setting", "control", "close", "fan",
                      positions=pos, conf=0.9)
        return _s("setting", "control", "open", "fan",
                  positions=pos, conf=0.9)

    # ── 采暖（踏步取暖等）────────────────────────────────
    if "踏步取暖" in t or "踏步加热" in t:
        if "关" in t:
            return _s("setting", "control", "close", "step_heating", conf=0.9)
        return _s("setting", "control", "open", "step_heating", conf=0.9)

    # ── 摄像头 ───────────────────────────────────────────
    if "摄像头" in t:
        if "关" in t:
            return _s("setting", "control", "close", "camera", conf=0.9)
        return _s("setting", "control", "open", "camera", conf=0.9)

    # ── 车机互联 ──────────────────────────────────────────
    if "车机互联" in t or "CarPlay" in t or "CarLife" in t:
        if "关" in t:
            return _s("app", "control", "close", "car_link", conf=0.9)
        return _s("app", "control", "open", "car_link", conf=0.9)

    # ── 队列 ─────────────────────────────────────────────
    if "队列" in t:
        if "创建" in t or "建" in t:
            return _s("setting", "control", "create", "team", conf=0.9)
        if "加入" in t or "进" in t:
            return _s("setting", "control", "join", "team", conf=0.9)
        if "离开" in t or "退出" in t:
            return _s("setting", "control", "leave", "team", conf=0.9)
        if "删除" in t or "解散" in t:
            return _s("setting", "control", "delete", "team", conf=0.9)

    # ══════════════════════════════════════════════════════════
    # 泛化匹配（页面引导 / 通用应用 / 通用媒体）
    # 仅处理上面专项规则未覆盖的残余关键词
    # ══════════════════════════════════════════════════════════

    # ── 页面引导（设置页面等专项未覆盖的）────────────────
    _page_names = {
        "设置": "settings",
        "空调界面": "aircon", "空调页面": "aircon",
        "主页": "home", "首页": "home",
    }
    if "打开" in t or "进入" in t or "切换到" in t:
        for kw, pn in _page_names.items():
            if kw in t:
                return _s("hmi", "navigate", "open", "page", tag=pn, conf=0.88)

    # ── 应用（专项未覆盖的通用 app 开关）──────────────────
    _app_names = {
        # 注意：蓝牙/电话/音乐/导航/电台 已由上面专项处理，不再列于此
    }
    if "打开" in t or "关闭" in t or "退出" in t:
        for kw, an in _app_names.items():
            if kw in t:
                operate = "close" if ("关" in t or "退出" in t) else "open"
                return _s("app", "control", operate, "app", tag=an, conf=0.88)

    # ── 通用媒体播放（旧保留，兜底）──────────────────────
    if "暂停" in t or "停一下" in t:
        return _s("app", "control", "pause", "media", conf=0.93)
    if "下一首" in t or "换一首" in t or "切歌" in t:
        return _s("app", "control", "switch", "media", conf=0.92)
    if "上一首" in t:
        return _s("app", "control", "switch", "media", conf=0.92)
    if "播放" in t or "放首歌" in t or "放音乐" in t or "来点音乐" in t:
        return _s("app", "control", "start", "media", conf=0.9)

    return None


def _s(domain: str, intent: str, operate: str, obj: str, **kwargs) -> dict:
    """构造结构化结果 {domain, intent, data: {...}}。"""
    data = {"operate": operate, "object": obj}
    data.update(kwargs)
    return {"domain": domain, "intent": intent, "data": data, "confidence": kwargs.pop("conf", 0.9)}


def _extract_position(t: str) -> list[str] | None:
    """从文本中提取位置信息。"""
    position_keywords = [
        "主驾", "主驾位", "驾驶位",
        "副驾", "副驾位", "副驾驶", "副驾驶位",
        "前排", "后排",
        "左后", "右后",
        "全车",
    ]
    for kw in position_keywords:
        if kw in t:
            return [kw]
    return None


def _extract_color(t: str) -> str | None:
    """从文本中提取氛围灯颜色。"""
    colors = ["红色", "蓝色", "绿色", "白色", "紫色", "黄色", "橙色", "粉色",
              "暖白", "冷白", "冰蓝", "星空",
              "红", "蓝", "绿", "白", "紫", "黄", "橙", "粉"]
    for c in colors:
        if c in t:
            return c
    return None


def _extract_level(t: str) -> str | None:
    """从文本中提取挡位数字，支持阿拉伯数字和中文数字。"""
    _cn_digit_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                     "六": "6", "七": "7", "八": "8", "九": "9", "零": "0"}
    m = re.search(r"([一二三四五六七八九零\d])\s*挡", t)
    if m:
        d = m.group(1)
        return _cn_digit_map.get(d, d)
    return None


def _extract_temperature(t: str) -> int | None:
    """Extract a realistic cabin temperature from Arabic or Chinese numerals."""
    match = re.search(r"(\d{1,2})\s*度", t)
    if match:
        value = int(match.group(1))
        return value if 16 <= value <= 32 else None

    match = re.search(r"([零〇一二两三四五六七八九十]{1,3})\s*度", t)
    if not match:
        return None

    numeral = match.group(1)
    digits = {
        "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
        "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if "十" in numeral:
        tens, ones = numeral.split("十", 1)
        value = (digits.get(tens, 1) * 10) + (digits.get(ones, 0) if ones else 0)
    else:
        value = 0
        for char in numeral:
            value = value * 10 + digits[char]

    return value if 16 <= value <= 32 else None


def _extract_percentage(t: str) -> int | None:
    """从文本中提取百分比值。支持 '一半'、'50%' 等。"""
    if "一半" in t:
        return 50
    m = re.search(r"(\d+)\s*%", t)
    if m:
        return int(m.group(1))
    return None


def is_local(name: str) -> bool:
    return name in LOCAL_INTENTS


# ── Multi-intent splitting ─────────────────────────────────────

# Conjunctions/connectors that signal multiple intents in one sentence.
# "并且" must come before standalone "并" so the regex prefers the longer match.
# "并" uses a lookbehind/lookahead to avoid splitting inside "合并" or "并且".
# "再" is short and ambiguous, so it requires a preceding comma.
# Plain comma is the final fallback (tried last, only when no keyword follows).
_SPLIT_MARKERS = re.compile(
    r"[，,]?\s*(?:并且|同时|然后|接着|顺便|顺带|还有|另外)\s*|"
    r"(?<![合])并(?![且])\s*|"
    r"[，,]\s*再\s*|"
    r"[，,]\s*"
)


def split_and_classify(text: str) -> list[dict] | None:
    """Multi-intent splitting. Returns list of structured intents if all local, None otherwise.

    Returns:
        list[dict]: Multiple structured intents (each in classify_structured format)
            when 2+ sub-clauses are detected AND all are local.
        None: Single intent (no split needed) OR any sub-intent needs cloud.
    """
    t = text.strip()

    # Split on conjunction markers (includes plain comma as final fallback)
    parts = _SPLIT_MARKERS.split(t)

    if len(parts) < 2:
        return None  # Single intent, no split needed

    # Classify each part
    intents = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        result = classify_structured(part)
        if result is None:
            return None  # Can't classify one part -> needs cloud
        intents.append(result)

    if len(intents) < 2:
        return None

    # Check all are local
    for intent in intents:
        legacy_name = _to_legacy_name(intent)
        if legacy_name is None or not is_local(legacy_name):
            return None  # Non-local or unknown -> whole sentence to cloud

    return intents


def split_and_classify_any(text: str) -> list[dict] | None:
    """Split multi-intent and classify ALL sub-intents regardless of locality.

    Unlike split_and_classify (all-or-nothing), this returns all classified
    intents even when some are non-local or unclassifiable.  Unclassifiable
    sub-clauses are marked with `_needs_cloud=True` so callers can route
    them to the cloud planner.

    Each returned dict has an extra `_raw_text` field with the original
    sub-clause text, so callers can extract non-local sub-clauses for cloud.

    Returns:
        list[dict]: 2+ structured intents (may be mixed local/non-local/unclassified).
        None: single intent only.
    """
    t = text.strip()
    parts = _SPLIT_MARKERS.split(t)
    if len(parts) < 2:
        return None

    intents = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        result = classify_structured(part)
        if result is None:
            # Can't classify locally → mark for cloud dispatch
            result = {"_raw_text": part, "_needs_cloud": True,
                      "data": {"object": "unknown", "operate": "unknown"}}
        else:
            result["_raw_text"] = part
            result["_needs_cloud"] = False
        intents.append(result)

    return intents if len(intents) >= 2 else None


def _to_legacy_name(intent: dict) -> str | None:
    """Convert structured intent to legacy name for is_local() check."""
    data = intent.get("data", {})
    obj = data.get("object", "")
    operate = data.get("operate", "")
    mode = data.get("mode", "")

    # Per-object operate normalisation: structured intents use "open"/"close",
    # but LOCAL_INTENTS uses "on"/"off" for some objects and "open"/"close"
    # for others.
    _on_off_map = {"open": "on", "close": "off"}

    if obj == "aircon":
        if mode == "wind_speed":
            return f"aircon.wind_speed.{operate}"
        if operate in ("inc", "dec"):
            return f"aircon.{operate}"
        return f"hvac.{_on_off_map.get(operate, operate)}"
    if obj == "window":
        return f"window.{operate}"
    if obj == "media":
        media_map = {"start": "play", "pause": "pause", "stop": "pause"}
        return f"media.{media_map.get(operate, operate)}"
    # 媒体子类统一映射到 media.*
    if obj in ("music", "radio", "online_radio", "audiobook", "opera", "news", "video", "TV"):
        media_map = {"play": "play", "start": "play", "open": "play", "pause": "pause",
                     "stop": "pause", "close": "pause", "switch": "next", "resume": "play",
                     "query": "query"}
        return f"media.{media_map.get(operate, operate)}"
    if obj in ("navigation", "navi", "map", "food", "hotel", "flight", "train", "stock", "weather"):
        return None  # online_only, not local
    if obj == "interaction":
        return f"interaction.{operate}"
    if obj == "frunk":
        return f"frunk.{operate}"
    if obj == "rear_view_mirror":
        mirror_map = {"fold": "fold", "unfold": "unfold"}
        op = mirror_map.get(operate, operate)
        return f"{obj}.{op}"
    if obj in ("seat", "ambient_light", "headlight", "fragrance"):
        # These use on/off in LOCAL_INTENTS
        op = _on_off_map.get(operate, operate)
        if mode:
            return f"{obj}.{mode}.{op}"
        return f"{obj}.{op}"
    if obj == "wiper":
        if mode == "speed":
            return f"wiper.speed.{operate}"
        return f"wiper.{_on_off_map.get(operate, operate)}"
    if obj in ("sunroof", "sunshade", "trunk", "door_lock"):
        # These use open/close in LOCAL_INTENTS
        if mode:
            return f"{obj}.{mode}.{operate}"
        return f"{obj}.{operate}"
    if obj == "volume":
        return f"{obj}.{operate}"
    if obj == "steering_wheel":
        if mode:
            return f"steering_wheel.{mode}.{operate}"
        return f"steering_wheel.{operate}"
    if obj == "screen":
        if mode:
            return f"screen.{mode}.{operate}"
        return f"screen.{operate}"
    if obj == "energy_recovery":
        return f"energy_recovery.{operate}"
    if obj in ("lane_departure_assistance", "lane_assistance"):
        return f"{obj}.{operate}"
    if obj in ("fuel_tank_cover", "charging_port"):
        return f"{obj}.{operate}"
    if obj == "tire_pressure":
        return "tire_pressure.query"
    if obj == "dashcam":
        return f"dashcam.{_on_off_map.get(operate, operate)}"
    if obj in ("scene_mode", "power_mode"):
        return f"{obj}.set"
    if obj == "page":
        return "page.open"
    if obj == "app":
        return f"app.{operate}"
    if obj == "weather":
        return "weather.query"
    # ── 新增对象映射 ──
    if obj == "bluetooth":
        return f"bluetooth.{operate}"
    if obj == "wifi":
        return f"wifi.{operate}"
    if obj == "hotspot":
        return f"hotspot.{_on_off_map.get(operate, operate)}"
    if obj == "auto_hold":
        return f"auto_hold.{_on_off_map.get(operate, operate)}"
    if obj == "epb":
        return f"epb.{_on_off_map.get(operate, operate)}"
    if obj == "launcher":
        return "launcher.return"
    if obj in ("equalizer", "sound_effect"):
        return f"{obj}.{operate}"
    if obj == "voice_assistant":
        return f"voice_assistant.{operate}"
    if obj == "factory_settings":
        return "factory_settings.restore"
    if obj == "memory":
        return "system.clean"
    if obj == "language":
        return "language.set"
    if obj == "time_format":
        return "time_format.set"
    if obj == "surround_view":
        return f"surround_view.{_on_off_map.get(operate, operate)}"
    if obj == "dashboard":
        return f"dashboard.{_on_off_map.get(operate, operate)}"
    if obj == "phone":
        return f"phone.{operate}"
    if obj == "contacts":
        return f"contacts.{operate}"
    if obj == "call_log":
        return f"call_log.{_on_off_map.get(operate, operate)}"
    if obj == "low_beam":
        return f"low_beam.{_on_off_map.get(operate, operate)}"
    return None  # Unknown object -> not local


def structured_to_legacy(intent: dict) -> dict | None:
    """Convert a structured intent {domain, intent, data, confidence} to legacy {name, slots, confidence}.

    Returns None if the intent cannot be mapped to a known local action.
    """
    name = _to_legacy_name(intent)
    if name is None:
        return None
    data = intent.get("data", {})
    obj = data.get("object", "")
    mode = data.get("mode", "")
    slots = {}
    if data.get("value"):
        if obj == "aircon" and mode != "wind_speed":
            slots["temp"] = data["value"]
        else:
            slots["value"] = data["value"]
    if data.get("mode") and obj in ("scene_mode", "power_mode"):
        slots["mode"] = data["mode"]
    if data.get("tag"):
        slots["tag"] = data["tag"]
    return {"name": name, "slots": slots, "confidence": intent.get("confidence", 0.9)}

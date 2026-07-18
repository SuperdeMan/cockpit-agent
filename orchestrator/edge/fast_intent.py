"""Fast Intent：端侧快意图分类器（PoC 规则版）。

只判定"是否高频确定指令(车控/媒体)"并抽槽位，给置信度。命中则本地秒回，否则上云。
新增 classify_structured() 输出公版 data 格式 {domain, intent, data}，覆盖座椅/天窗/后备箱等。
保持 classify() 向后兼容——现有调用者不受影响。
"""
from __future__ import annotations
import re

LOCAL_INTENTS = {
    "hvac.set", "hvac.on", "hvac.off",
    "window.open", "window.close", "window.set", "window.inc", "window.dec",
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
    # 电量查询 / 胎压 / 行车记录仪
    "battery.query",
    "tire_pressure.query",
    "dashcam.open", "dashcam.close",
    # ── R4.1b P0：端侧对象化（空气净化 / 导航播报 / 按键音；open/close 命名，与 classify() 快路径口径一致）──
    "air_purifier.open", "air_purifier.close",
    "navi_broadcast.open", "navi_broadcast.close", "navi_broadcast.set",
    "key_tone.open", "key_tone.close",
    # 驾驶模式 / 电源模式
    "driving_mode.set",
    "power_mode.set",
    # 注意：scene_mode.set（露营/小憩/观影/浪漫/冥想 等命名场景）刻意不入 LOCAL_INTENTS——
    # 这些命名场景由云端 scene-orchestrator 编排（展开为多动作 + 危险确认 + 低电量门控），
    # 端侧 scene_mode 仅设状态位、无编排能力。classify_structured 仍识别 scene_mode（保留
    # VAL 知识与语料），但 is_local=False 使其路由上云交 scene.activate。
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


# ── 场景管理句判定（端云分工，见 classify_structured 顶部）────────────────────
# 造/改/删「X模式」是云端 scene-orchestrator 的活；句中的车控词是场景内容不是当下指令。
_SCENE_CREATE_RE = re.compile(
    r"(创建|新建|自定义|帮我建|建立|建一个|建个|做一个|做个|存成|存为|设一个|设个)[^。]{0,8}模式")
_SCENE_EDIT_RE = re.compile(
    r"(删掉|删除|去掉|不要)\s*[一-龥]{1,6}模式"
    r"|[一-龥]{1,6}模式(的)?[^。]{0,10}(改成|改为|改到|加上|加一个|再加|去掉|删掉)")
# D8：驾驶/动力类模式词归端侧 driving_mode/power_mode（毫秒级秒回），不让场景编排抢走
_EDGE_MODE_RE = re.compile(
    r"(驾驶|运动|舒适|经济|节能|标准|雪地|越野|性能|省电|电量|动能回收)模式")


# 场景激活/退出句（带动词的「X模式」）——句中的车控词是场景参数，不是当下指令
_SCENE_ACT_RE = re.compile(
    r"(开启|打开|进入|切换到|来个|来一个|启动|退出|关闭|取消|结束)\s*[一-龥]{1,6}模式")

_FACTORY_SCENE_MODES = (
    ("小憩", "nap"), ("小睡", "nap"), ("露营", "camping"),
    ("观影", "movie"), ("看电影", "movie"), ("浪漫", "romantic"), ("冥想", "meditation"),
)


def _factory_scene_mode(t: str) -> str | None:
    """出厂场景词 → VAL scene_mode 值（与 entities.yaml scene_modes 同源）。"""
    for kw, mode in _FACTORY_SCENE_MODES:
        if kw in t:
            return mode
    return None


def _is_scene_management(t: str) -> bool:
    if _SCENE_CREATE_RE.search(t):
        # 「创建X模式」恒判场景管理——哪怕 X 撞了端侧模式名：云端会诚实告诉用户"这名字被占了"，
        # 好过端侧把句子里的车控动作当场执行掉。
        return True
    return bool(_SCENE_EDIT_RE.search(t)) and not _EDGE_MODE_RE.search(t)


def _is_scene_utterance(t: str) -> bool:
    """整句归云端场景编排的话：造/改/删场景 **+ 开启/退出场景**。

    激活句也要算进来：「开启午休模式，温度26」里的「温度26」是**场景参数**，一旦被拆句，
    后半句就成了独立的本地车控意图、被混合意图路径当场执行——空调开了、场景没激活
    （真机实测命中，与造场景句同一个坑）。D8 的端侧模式词不在此列。
    """
    return _is_scene_management(t) or bool(
        _SCENE_ACT_RE.search(t) and not _EDGE_MODE_RE.search(t))


# 提醒类话术（→云端 reminder Agent）：代词/时点锚定的「提醒」词形 + 闹钟/待办。
# 刻意不含裸「提醒」——「打开限速提醒/超速提醒/疲劳提醒」是 ADAS 功能开关（端侧设置域）；
# 「记得/叫我」单独出现歧义大，只收「记得提醒/到时候叫我」等组合形。R7 拨号分支的
# 同类让路（提醒我给张姐打电话）由本守卫统一承接。
_REMINDER_UTTER_RE = re.compile(
    r"提醒(我|一下|咱|大家)|别忘了|别忘记|勿忘"
    r"|(记得|一定要)(提醒|叫|喊)|到时候?(叫|喊)我"
    r"|[设定加建创][个一两三]?[个条]?(闹钟|提醒|待办)|闹钟|待办")


def _is_reminder_utterance(t: str) -> bool:
    return bool(_REMINDER_UTTER_RE.search(t))


# 显式体感调节词形（空调分支入口白名单）：与分支内 inc/dec 处理一一对应的相对调节说法。
# 刻意只收旧共现规则的**合理子集**——「有点冷/好热」这类裸体感陈述历史上就走云端
# 隐式车控（planner 规则），不在此扩大端侧接管面。
_FEELING_FORMS = ("热一点", "冷一点", "再热", "再冷")


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
        name = "info.weather"
    elif obj == "forecast":
        name = "info.forecast"
    elif obj == "stock":
        name = "info.stock"
    elif obj == "search":
        name = "info.search"
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

    # ── 提醒类话术一律上云（badcase c9bcf8c2：「再帮我加一个明晚10点提醒我冷萃咖啡过滤」
    # 中「冷」（冷萃）+「再」（再帮我）撞上体感共现规则，被端侧当 hvac.on 秒回并真开了空调）。
    # 「提醒我/别忘了/设个闹钟」是建提醒诉求，归云端 reminder Agent；须早于一切车控分支。
    # 刻意不用裸「提醒」——「打开限速提醒/疲劳提醒」是 ADAS 设置（下方分支），仍走端侧。
    if _is_reminder_utterance(t):
        return None

    # ── 场景句一律上云（与本文件头部「命名场景归云端 scene-orchestrator 编排」同一分工）──
    # ① 场景**管理**句：「创建钓鱼模式：氛围灯调到10%，空调22度」里的车控词是**场景内容**，
    #    不是**当下的指令**——端侧若照单执行，用户会发现灯真被调暗了、场景却没建成。
    #    「把钓鱼模式的温度改成24」同理：改的是场景定义，不是现在开空调。
    # ② 场景**激活/退出**句：「开启午休模式，温度26」里的「温度26」是**场景参数**（激活时
    #    覆盖场景里的空调设定），不是当下要开空调。**这一条必须早于空调分支**——否则
    #    「温度26」会让整句落到 aircon，端侧把空调开了、场景根本没激活（真机实测命中）。
    #    出厂场景词（露营/小憩…）仍返回结构化 scene_mode（保留 VAL 知识与语料），但它不在
    #    LOCAL_INTENTS 里，照样上云；用户自建场景端侧无知识 → None → 整句上云。
    #    D8：驾驶/动力类模式词（运动/省电/雪地…）不在此列，继续走端侧毫秒级秒回。
    if _is_scene_management(t):
        return None
    if _SCENE_ACT_RE.search(t) and not _EDGE_MODE_RE.search(t):
        mode = _factory_scene_mode(t)
        if mode:
            return _s("setting", "control", "set", "scene_mode", mode=mode, conf=0.9)
        return None

    # ── R4.1b P0：端侧对象化（空气净化 / 导航播报 / 按键音；短语明确、早置防被后续泛化截获）──
    if "空气净化" in t:
        return _s("setting", "control", "close" if "关" in t else "open", "air_purifier", conf=0.92)
    if "导航" in t and "播报" in t:            # 导航播报开关/模式（导航+播报共现，不误吸「导航去X」）
        if "关" in t:
            return _s("setting", "control", "close", "navi_broadcast", conf=0.9)
        _bc_modes = {"简洁": "concise", "简捷": "concise", "详细": "detailed",
                     "周详": "detailed", "详尽": "detailed", "AI": "ai", "标准": "standard"}
        for kw, m in _bc_modes.items():
            if kw in t:
                return _s("setting", "control", "set", "navi_broadcast", mode=m, conf=0.9)
        if "切换" in t or "换" in t:
            return _s("setting", "control", "set", "navi_broadcast", conf=0.88)
        return _s("setting", "control", "open", "navi_broadcast", conf=0.9)
    if "按键音" in t:
        return _s("setting", "control", "close" if "关" in t else "open", "key_tone", conf=0.9)

    # ── 空调 ──────────────────────────────────────────────
    # R5 让路（旅程 B3-3）：①「记住，我最喜欢的空调温度是26度」是**偏好陈述**要进云端
    # 记忆（原被温度分支当场执行成开空调 26 度）；②「把空调调到我喜欢的温度」参数在
    # 用户画像里，须由云端记忆召回填值（原端侧当「开空调」秒回「开了」）。整句上云。
    # 体感入口收窄（badcase c9bcf8c2）：旧共现规则 (热|冷)×(度|一点|再) 宽到「再帮我…
    # 冷萃咖啡」「这歌太热了再来一遍」都命中——收成显式体感词形白名单，兜底不再误开空调。
    if any(w in t for w in ("记住", "记一下", "帮我记", "别忘了我")) \
            or ("喜欢" in t and ("温度" in t or "空调" in t)) \
            or any(w in t for w in ("常用的温度", "习惯的温度", "老样子")):
        pass                                   # 不进空调分支，落到云端兜底
    elif ("空调" in t and "界面" not in t and "页面" not in t) or \
            ("温度" in t and not _is_env_temp_query(t)) or \
            "风速" in t or "风量" in t or \
            any(w in t for w in _FEELING_FORMS) or \
            (("热" in t or "冷" in t) and _extract_temperature(t) is not None):
        if "关" in t:
            return _s("setting", "control", "close", "aircon", conf=0.93)
        # 风速/风量
        if "风速" in t or "风量" in t:
            if "大" in t or "高" in t:
                return _s("setting", "control", "inc", "aircon", mode="wind_speed", conf=0.9)
            if "小" in t or "低" in t:
                return _s("setting", "control", "dec", "aircon", mode="wind_speed", conf=0.9)
            m = re.search(r"(\d+)\s*[挡档级]", t)
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
        # 相对调节（先于绝对开/关）：开大点/多开点 → inc；开小点/关小点 → dec
        if ("大" in t or "多开" in t) and "关" not in t and "小" not in t:
            return _s("setting", "control", "inc", "window",
                      positions=pos, conf=0.9)
        if "小" in t:
            return _s("setting", "control", "dec", "window",
                      positions=pos, conf=0.9)
        # 模糊小开度：开条缝/开个缝/开一点 → 15%
        if "缝" in t or ("开" in t and ("一点" in t or "一些" in t)):
            return _s("setting", "control", "set", "window",
                      value="15", unit="percent", positions=pos, conf=0.9)
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

    # ── 场景模式（裸「露营模式」这类无动词形态；带动词的已在顶部护栏处理）──
    mode = _factory_scene_mode(t)
    if mode:
        return _s("setting", "control", "set", "scene_mode", mode=mode, conf=0.9)

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
    # "热点"在新闻上下文中（新闻/资讯/头条/今日/今天/播报/发生）不判为车载热点
    _news_ctx = any(w in t for w in ("新闻", "资讯", "头条", "今日", "今天", "播报", "发生"))
    if "热点" in t and "列表" not in t and not _news_ctx:
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
    # R7 让路（旅程 A2-4）：「到之前一刻钟**提醒我**给张姐打电话」是设提醒不是当场拨号——
    # 含提醒/别忘了词形的整句上云归 reminder（否则端侧秒回「暂不支持哦」把提醒吞了）。
    if ("打电话" in t or "拨打" in t or "拨电话" in t or "拨给" in t) \
            and not any(w in t for w in ("提醒", "别忘了", "记得", "叫我")):
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

    # ── 新闻（消歧：播/听→媒体播放；看/摘要/头条→信息摘要）──
    if "新闻" in t or "头条" in t:
        if "播" in t or "放" in t or "听" in t:
            return _s("media", "control", "play", "news", conf=0.88)
        if "关" in t or "停" in t:
            return _s("media", "control", "stop", "news", conf=0.9)
        # "看/摘要/今天发生了什么/有什么新闻" → info.news（文本摘要，online_only）
        if "看" in t or "摘要" in t or "发生" in t or "有什么" in t:
            return _s("info", "query", "query", "news", conf=0.88)
        # 默认仍走媒体播放（兼容"来段新闻"等模糊表达）
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

    # ── 天气 / 气象 / 温度 / 湿度 / 风况 / 空气质量 ─────────────
    # R4.1 P3 B1：「气象」与「天气」语义等价（气象 5.0%→并入天气类，纯命名遗漏），但排除
    #   气象局=地点（走导航）/ 含「预警」=云端 info.alerts（非端侧天气查询），防劫持。
    if "天气" in t or ("气象" in t and "气象局" not in t and "预警" not in t):
        return _s("query", "query", "query", "weather", conf=0.9)
    # 体感/气温=纯天气语义（info.weather 的 speech 自带体感温度），疑问式一并接住
    # （badcase 361f6e72：「今天体感温度怎么样」原两头不沾，被空调分支劫持）。
    if ("体感" in t or "气温" in t) and \
            ("查" in t or "多少" in t or "几度" in t or "怎么样" in t or "怎样" in t or "如何" in t):
        return _s("query", "query", "query", "weather", conf=0.9)
    if "温度" in t and ("查" in t or "多少" in t or "几度" in t
                       or "怎么样" in t or "怎样" in t or "如何" in t):
        return _s("query", "query", "query", "temperature", conf=0.9)
    if "湿度" in t and ("查" in t or "多少" in t):
        return _s("query", "query", "query", "humidity", conf=0.9)
    if "风力" in t or "风况" in t or "几级风" in t:
        return _s("query", "query", "query", "wind_force", conf=0.9)
    if "空气质量" in t or "PM2.5" in t or "空气指数" in t:
        return _s("query", "query", "query", "air_quality", conf=0.9)

    # ── 天气预报（online_only→info.forecast）──────────────────
    if ("预报" in t or "未来几天" in t or "明天天气" in t
            or "后天天气" in t or "这周天气" in t):
        return _s("info", "query", "query", "forecast", conf=0.88)

    # ── 联网搜索（online_only→info.search）────────────────────
    if "搜一下" in t or "搜一搜" in t or "帮我搜" in t or "帮我查一下" in t:
        return _s("info", "query", "query", "search", conf=0.85)

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

    # ── 股票（收敛到 info.stock，消除 information/stock 孤儿意图）──
    if "股票" in t or "股价" in t or "大盘" in t or "指数" in t:
        return _s("info", "query", "query", "stock", conf=0.88)

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
    # 电量/剩余续航查询：归 battery.query（在 LOCAL_INTENTS、端侧确定性应答）。
    # "还能跑/能跑多/还能开多/跑多少公里/续航"等剩余里程问法也归此，否则漏到云端被弱 LLM
    # 误判成闲聊（energy_consumption.query 不在 LOCAL_INTENTS，会继续上云）。
    # 注意"开车去X多远"是距离非续航，不含"还能跑/能跑多"等前缀，不会误命中。
    # "电池"单独出现太宽（"固态电池/电池技术/电池行业"是话题，"深入调研固态电池"曾被劫持成
    # 电量查询）→ 必须与电量级/状态词（多少/还有/剩/几成/百分/状态/健康/够不够/满电）同现才判电量查询。
    if ("电量" in t or "续航" in t or "还能跑" in t or "能跑多" in t
            or "还能开多" in t or "跑多少公里" in t or "开多少公里" in t
            or ("剩" in t and "电" in t)
            or ("电池" in t and any(w in t for w in
                                    ("多少", "还有", "几成", "百分", "状态", "健康",
                                     "够不够", "够用", "满电")))):
        return _s("query", "query", "query", "battery", conf=0.9)
    if "能耗" in t:
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
    # R4.1 P3 B2：设置页/界面开合族——补「关闭」方向 + 「界面/页面」通用兜底（负一屏/语音技能
    # 界面/发音人列表等无「设置」字的 UI 页）。排除「给我读/念一下」= 读内容请求（云端，非开页面），
    # 修既有「打开设置里的隐私协议给我读一下」被误接成 page/settings 的劫持。
    _page_names = {
        "设置": "settings",
        "空调界面": "aircon", "空调页面": "aircon",
        "主页": "home", "首页": "home",
    }
    _page_open = "打开" in t or "进入" in t or "切换到" in t
    _page_close = "关闭" in t or "关掉" in t
    _read_content = any(w in t for w in ("给我读", "读一下", "念一下", "读给", "念给"))
    if (_page_open or _page_close) and not _read_content:
        _op = "close" if (_page_close and not _page_open) else "open"
        for kw, pn in _page_names.items():
            if kw in t:
                return _s("hmi", "navigate", _op, "page", tag=pn, conf=0.88)
        # 「XX界面/页面」通用兜底：上面专项与 _page_names 都没接住的 UI 页开合
        if "界面" in t or "页面" in t:
            return _s("hmi", "navigate", _op, "page", conf=0.85)

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


# 「温度」出现但语义是问天气/环境温度（badcase 361f6e72：「今天体感温度怎么样」
# 被裸「温度」子条件劫持成开空调——问天气误触车控执行）。
_ENV_TEMP_CTX = ("体感", "天气", "气温", "外面", "室外", "户外")
_TEMP_INTERROGATIVES = ("怎么样", "怎样", "如何", "冷不冷", "热不热")
_AC_ADJUST_VERBS = ("调", "设", "开", "关", "升", "降", "加", "减")


def _is_env_temp_query(t: str) -> bool:
    """裸「温度」该不该让给天气/环境查询。三层：
    ① 查/几度/多少 —— 原空调分支的既有排除，无条件保留（不动 eval 基线）；
    ② 天气语境词（体感/气温/室外…）—— 无条件让路；
    ③ 疑问式（怎么样/如何…）—— 仅在没有空调操作动词时让路
      （「温度如何调高」仍归空调，「温度怎么样」归查询）。"""
    if "查" in t or "几度" in t or "多少" in t:
        return True
    if any(k in t for k in _ENV_TEMP_CTX):
        return True
    if any(v in t for v in _AC_ADJUST_VERBS):
        return False
    return any(k in t for k in _TEMP_INTERROGATIVES)


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
# "还有"作连接词才拆；"还有多少/几/没"是问量短语（如"电量还有多少"），不拆。
_SPLIT_MARKERS = re.compile(
    r"[，,]?\s*(?:并且|同时|然后|接着|顺便|顺带|还有(?!多少|几|没)|另外)\s*|"
    r"(?<![合])并(?![且])\s*|"
    r"[，,]\s*再\s*|"
    r"[，,]\s*"
)


def _resplit_on_he(part: str) -> list[str]:
    """对含“和”的片段做安全二次拆分：仅当按“和”拆开后每段都是 local 车控时才拆，
    否则原样返回。解决“座椅加热和座椅通风”“空调和氛围灯”这类并列；同时避免误拆
    人名/词组（如“周华健”“天气和路况”——任一段非 local 即不拆，整段交后续/上云）。"""
    if "和" not in part:
        return [part]
    subs = [s.strip() for s in part.split("和") if s.strip()]
    if len(subs) < 2:
        return [part]
    for s in subs:
        r = classify_structured(s)
        if r is None:
            return [part]
        name = _to_legacy_name(r)
        if name is None or not is_local(name):
            return [part]
    return subs


def _split_parts(text: str) -> list[str]:
    """按 _SPLIT_MARKERS 拆分，再对每段做“和”的安全二次拆分；返回 strip 后的非空段。

    场景句**不拆**（返回空）：「创建钓鱼模式：氛围灯调到10%，空调22度」一拆，后半句
    「空调22度」就成了独立的本地车控意图，被混合意图路径（split_and_classify_any）当场执行
    ——用户看到空调真开了、场景却没建成。「开启午休模式，温度26」同理（「温度26」是场景参数）。
    整句必须完整交云端 scene-orchestrator。
    这里是两个 split 函数的唯一收口，堵在这里即可（2026-07-14 真栈实测两次命中）。
    """
    if _is_scene_utterance(text):
        return []
    parts: list[str] = []
    for p in _SPLIT_MARKERS.split(text):
        if p and p.strip():
            parts.extend(_resplit_on_he(p.strip()))
    return parts


def climate_feeling_intents(text: str) -> list[dict] | None:
    """从"体感冷热"推断空调调节方向：用户说「感觉冷，把空调温度和风速都调一下」时，
    意图是【暖一点】= 温度调高 + 风速调小；说「热」则【凉一点】= 温度调低 + 风速调大。

    仅在同时点名了温度和风速（"都调"那种）且明确冷/热时触发，产出两条本地结构化指令
    供端侧多意图并行执行；其余空调表达交常规分类，零回归。
    """
    t = (text or "").strip()
    if "空调" not in t and "冷气" not in t and "暖风" not in t:
        return None
    if not (("温度" in t) and ("风速" in t or "风量" in t)):
        return None
    cold = any(w in t for w in ("冷", "凉"))
    hot = any(w in t for w in ("热", "闷", "燥", "烫"))
    if cold == hot:  # 既无冷热、或冷热都提了 → 方向不明，不擅自处理
        return None
    if cold:  # 冷 → 暖一点：温度↑、风速↓
        return [
            _s("setting", "control", "inc", "aircon", conf=0.9),
            _s("setting", "control", "dec", "aircon", mode="wind_speed", conf=0.9),
        ]
    # 热 → 凉一点：温度↓、风速↑
    return [
        _s("setting", "control", "dec", "aircon", conf=0.9),
        _s("setting", "control", "inc", "aircon", mode="wind_speed", conf=0.9),
    ]


def split_and_classify(text: str) -> list[dict] | None:
    """Multi-intent splitting. Returns list of structured intents if all local, None otherwise.

    Returns:
        list[dict]: Multiple structured intents (each in classify_structured format)
            when 2+ sub-clauses are detected AND all are local.
        None: Single intent (no split needed) OR any sub-intent needs cloud.
    """
    t = text.strip()

    # 按连词/逗号拆分 + “和”的安全二次拆分
    parts = _split_parts(t)

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
    parts = _split_parts(t)
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
    if obj in ("navigation", "navi", "map", "food", "hotel", "flight", "train",
               "stock", "weather", "forecast", "search"):
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
    if obj in ("air_purifier", "key_tone"):   # R4.1b P0：open/close（与 LOCAL_INTENTS/classify 快路径一致）
        return f"{obj}.{operate}"
    if obj == "navi_broadcast":               # R4.1b P0：open/close + set（播报模式，mode 不入 name）
        return "navi_broadcast.set" if operate == "set" else f"navi_broadcast.{operate}"
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
        return "info.weather"
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

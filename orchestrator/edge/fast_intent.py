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
}


def _i(name: str, slots: dict, conf: float) -> dict:
    return {"name": name, "slots": slots, "confidence": conf}


def classify(text: str) -> dict | None:
    """旧接口：返回 {name, slots, confidence}。向后兼容。"""
    result = classify_structured(text)
    if result is None:
        return None

    # 从结构化结果映射回旧 name/slots 格式
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
            "温度" in t or "风速" in t or "风量" in t or \
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
        # 温度增减（相对，无具体度数）
        if ("调高" in t or "高一点" in t or "热一点" in t or "再热" in t) \
                and not re.search(r"\d+\s*度", t):
            return _s("setting", "control", "inc", "aircon", conf=0.88)
        if ("调低" in t or "低一点" in t or "冷一点" in t or "再冷" in t) \
                and not re.search(r"\d+\s*度", t):
            return _s("setting", "control", "dec", "aircon", conf=0.88)
        # 温度设定（绝对）
        m = re.search(r"(\d{2})\s*度", t)
        if m:
            return _s("setting", "control", "set", "aircon",
                      value=m.group(1), unit="degree", conf=0.95)
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
        if "关" in t:
            return _s("setting", "control", "close", "ambient_light", conf=0.9)
        # 颜色
        color = _extract_color(t)
        if color:
            return _s("setting", "control", "set", "ambient_light",
                      tag=color, conf=0.9)
        return _s("setting", "control", "open", "ambient_light", conf=0.9)

    # ── 大灯 ──────────────────────────────────────────────
    if "大灯" in t or "远光" in t:
        if "关" in t:
            return _s("setting", "control", "close", "headlight", conf=0.9)
        return _s("setting", "control", "open", "headlight", conf=0.9)

    # ── 近光灯 ────────────────────────────────────────────
    if "近光灯" in t or "近光" in t:
        if "关" in t:
            return _s("setting", "control", "close", "low_beam", conf=0.9)
        return _s("setting", "control", "open", "low_beam", conf=0.9)

    # ── 雨刷 ──────────────────────────────────────────────
    if "雨刷" in t or "雨刮" in t:
        if "关" in t:
            return _s("setting", "control", "close", "wiper", conf=0.9)
        # 速度挡位
        if "快" in t or "大" in t:
            return _s("setting", "control", "inc", "wiper", mode="speed", conf=0.9)
        if "慢" in t or "小" in t:
            return _s("setting", "control", "dec", "wiper", mode="speed", conf=0.9)
        m = re.search(r"(\d)\s*挡", t)
        if m:
            return _s("setting", "control", "set", "wiper", mode="speed",
                      value=m.group(1), unit="level", conf=0.9)
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
        if "亮" in t or "调亮" in t:
            return _s("setting", "control", "inc", "screen",
                      mode="brightness", conf=0.9)
        if "暗" in t or "调暗" in t:
            return _s("setting", "control", "dec", "screen",
                      mode="brightness", conf=0.9)
        m = re.search(r"(\d+)", t)
        if m:
            return _s("setting", "control", "set", "screen",
                      mode="brightness", value=m.group(1), unit="percent", conf=0.9)
        return _s("setting", "control", "open", "screen", conf=0.85)

    # ── 页面引导 ──────────────────────────────────────────
    _page_names = {
        "设置": "settings", "导航页面": "navigation", "导航界面": "navigation",
        "空调界面": "aircon", "空调页面": "aircon", "主页": "home", "首页": "home",
    }
    if "打开" in t or "进入" in t or "切换到" in t:
        for kw, pn in _page_names.items():
            if kw in t:
                return _s("hmi", "navigate", "open", "page", tag=pn, conf=0.88)

    # ── 应用 ──────────────────────────────────────────────
    _app_names = {
        "音乐": "music", "电台": "radio", "电话": "phone",
        "蓝牙": "bluetooth", "导航": "navigation",
    }
    if "打开" in t or "关闭" in t or "退出" in t:
        for kw, an in _app_names.items():
            if kw in t:
                operate = "close" if ("关" in t or "退出" in t) else "open"
                return _s("app", "control", operate, "app", tag=an, conf=0.88)

    # ── 天气查询 ──────────────────────────────────────────
    if "天气" in t:
        return _s("query", "query", "query", "weather", conf=0.9)

    # ── 媒体（旧保留）─────────────────────────────────────
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

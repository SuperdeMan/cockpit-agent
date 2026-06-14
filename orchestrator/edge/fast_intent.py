"""Fast Intent：端侧快意图分类器（PoC 规则版）。

只判定"是否高频确定指令(车控/媒体)"并抽槽位，给置信度。命中则本地秒回，否则上云。
新增 classify_structured() 输出公版 data 格式 {domain, intent, data}，覆盖座椅/天窗/后备箱等。
保持 classify() 向后兼容——现有调用者不受影响。
"""
from __future__ import annotations
import re

LOCAL_INTENTS = {
    "hvac.set", "hvac.on", "hvac.off",
    "window.open", "window.close",
    "media.play", "media.pause", "media.next", "media.prev",
    # 新增结构化意图（归一化后对应的旧名称仍保留在这里做兼容）
    "seat.heating.on", "seat.heating.off", "seat.ventilation.on", "seat.ventilation.off",
    "seat.massage.on", "seat.massage.off",
    "sunroof.open", "sunroof.close",
    "sunshade.open", "sunshade.close",
    "trunk.open", "trunk.close",
    "door_lock.open", "door_lock.close",
    "ambient_light.on", "ambient_light.off",
    "headlight.on", "headlight.off",
    "wiper.on", "wiper.off",
    "rear_view_mirror.fold", "rear_view_mirror.unfold",
    "fragrance.on", "fragrance.off",
    "volume.set", "volume.inc", "volume.dec",
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
        if operate == "close":
            name = "hvac.off"
        elif operate in ("set", "open") and data.get("value"):
            name = "hvac.set"
        else:
            name = "hvac.on"
    elif obj == "window":
        name = f"window.{operate}"
    elif obj in ("seat", "sunroof", "sunshade", "trunk", "door_lock",
                 "ambient_light", "headlight", "wiper", "rear_view_mirror",
                 "fragrance", "volume"):
        name = f"{obj}.{operate}"
        if mode:
            name = f"{obj}.{mode}.{operate}"
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
    if obj == "aircon" and data.get("value"):
        slots["temp"] = data["value"]
    if data.get("value") and obj != "aircon":
        slots["value"] = data["value"]

    return _i(name, slots, result.get("confidence", 0.9))


def classify_structured(text: str) -> dict | None:
    """新接口：返回公版 {domain, intent, data: {operate, object, ...}} 格式。"""
    t = text.strip()

    # ── 空调 ──────────────────────────────────────────────
    if "空调" in t or "温度" in t or (("热" in t or "冷" in t) and "度" in t):
        if "关" in t:
            return _s("setting", "control", "close", "aircon", conf=0.93)
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
        if "开" in t:
            return _s("setting", "control", "open", "window",
                      positions=pos, conf=0.92)

    # ── 天窗 ──────────────────────────────────────────────
    if "天窗" in t:
        if "关" in t:
            return _s("setting", "control", "close", "sunroof", conf=0.92)
        return _s("setting", "control", "open", "sunroof", conf=0.92)

    # ── 遮阳帘 ────────────────────────────────────────────
    if "遮阳帘" in t or "遮阳" in t:
        pos = _extract_position(t)
        if "关" in t:
            return _s("setting", "control", "close", "sunshade",
                      positions=pos, conf=0.9)
        return _s("setting", "control", "open", "sunshade",
                  positions=pos, conf=0.9)

    # ── 座椅 ──────────────────────────────────────────────
    if "座椅" in t or "座位" in t:
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


def is_local(name: str) -> bool:
    return name in LOCAL_INTENTS

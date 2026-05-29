"""Fast Intent：端侧快意图分类器（PoC 规则版）。

只判定"是否高频确定指令(车控/媒体)"并抽槽位，给置信度。命中则本地秒回，否则上云。
TODO(Phase1): 规则 + 端侧轻量分类模型；意图白名单由端侧 Agent 注册声明；阈值可 OTA。
"""
from __future__ import annotations
import re

LOCAL_INTENTS = {
    "hvac.set", "hvac.on", "hvac.off",
    "window.open", "window.close",
    "media.play", "media.pause", "media.next", "media.prev",
}


def _i(name: str, slots: dict, conf: float) -> dict:
    return {"name": name, "slots": slots, "confidence": conf}


def classify(text: str) -> dict | None:
    t = text.strip()

    # 空调
    if "空调" in t or "温度" in t or (("热" in t or "冷" in t) and "度" in t):
        if "关" in t:
            return _i("hvac.off", {}, 0.93)
        m = re.search(r"(\d{2})\s*度", t)
        if m:
            return _i("hvac.set", {"temp": m.group(1)}, 0.95)
        if "热" in t or "高" in t:
            return _i("hvac.set", {"temp": "26"}, 0.88)
        return _i("hvac.on", {}, 0.9)

    # 车窗
    if "车窗" in t or "窗户" in t:
        if "关" in t:
            return _i("window.close", {}, 0.92)
        if "开" in t:
            return _i("window.open", {}, 0.92)

    # 媒体
    if "暂停" in t or "停一下" in t:
        return _i("media.pause", {}, 0.93)
    if "下一首" in t or "换一首" in t or "切歌" in t:
        return _i("media.next", {}, 0.92)
    if "上一首" in t:
        return _i("media.prev", {}, 0.92)
    if "播放" in t or "放首歌" in t or "放音乐" in t or "来点音乐" in t:
        return _i("media.play", {}, 0.9)

    return None


def is_local(name: str) -> bool:
    return name in LOCAL_INTENTS

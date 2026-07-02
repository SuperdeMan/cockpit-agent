"""从原始话术抽取行程规划槽位（目的地/天数/偏好）。

R2.1：这段「多日出行目的地抽取」原在编排核心 `planning._extract_trip`——它既是 trip.plan 兜底
注入的判据（有效目的地 + 天数/偏好/日游/trigger，通勤/固定点 BLOCK 剔除），也产出 dest/days/prefs。
现搬回 trip-planner Agent：manifest.route_hints 的 trip.plan 触发只做「是否注入」的粗门控（等价的
DEST+SIGNAL 正则 + 反例守卫），真正的槽位抽取由本函数在 Agent 侧从 raw_text 完成——编排核心不再
持任何行程领域知识（恢复「新增 Agent 不改编排核心」铁律）。逻辑与原 _extract_trip 逐字一致。
"""
from __future__ import annotations
import re

# 目的地：取『去/到/赴X』中的 X（懒匹配到 玩/住/游/标点/N天 前），通勤/固定点不算出行
_TRIP_DEST_RE = re.compile(
    r"(?:去|到|赴|游)\s*([一-鿿]{2,6}?)"
    r"(?=玩|住|待|游|逛|的|附近|边|，|,|。|！|!|、|\s|[一二两三四五六七八九十0-9]+\s*[天日]|$)")
# 退路：『杭州三日游』这类无『去』前缀、地名直接接 N日游
_TRIP_DEST_BEFORE_DAYS_RE = re.compile(
    r"([一-鿿]{2,6}?)(?=[一二两三四五六七八九十0-9]+\s*[天日]游)")
_TRIP_DAYS_RE = re.compile(r"([一二两三四五六七八九十0-9]+)\s*[天日]")
_TRIP_PREF_WORDS = ("带老人", "带娃", "带孩子", "带小孩", "不要太累", "不累",
                    "轻松", "悠闲", "慢一点", "慢点", "休闲")
_TRIP_PREF_RE = re.compile("|".join(_TRIP_PREF_WORDS))
# 强出行信号：与目的地同现即判为行程规划（即便没说天数）
_TRIP_TRIGGER_RE = re.compile("行程|自驾游|度假")
# 通勤/固定地点：是导航日常目的地，不是多日出行
_TRIP_DEST_BLOCK = {"公司", "家", "单位", "学校", "上班", "这里", "那里", "机场", "车站"}
_CN_NUM = {"一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def extract_trip(text: str) -> tuple[str, str, str]:
    """从话术解析 (destination, days, preferences)；非出行/无目的地返回空。"""
    text = text or ""
    m_dest = _TRIP_DEST_RE.search(text) or _TRIP_DEST_BEFORE_DAYS_RE.search(text)
    dest = (m_dest.group(1) if m_dest else "").strip()
    # 通勤/固定点用前缀判定（"公司开"仍算公司；"张家界"不会被单字"家"误杀）
    if not dest or any(dest.startswith(b) for b in _TRIP_DEST_BLOCK):
        return "", "", ""
    m_days = _TRIP_DAYS_RE.search(text)
    # 出行判定：有目的地，且（N天/N日 或 出行偏好词 或 N日游 或 行程/自驾游/度假）
    if not (m_days or _TRIP_PREF_RE.search(text) or "日游" in text
            or _TRIP_TRIGGER_RE.search(text)):
        return "", "", ""
    days = ""
    if m_days:
        d = m_days.group(1)
        days = _CN_NUM.get(d, d)
    prefs = "、".join(w for w in _TRIP_PREF_WORDS if w in text)
    return dest, days, prefs

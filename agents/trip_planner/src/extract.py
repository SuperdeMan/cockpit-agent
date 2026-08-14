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
# G4 主题行程：《X》书名号 / 「跟着X游/玩」/「X同款/取景地/打卡」三族。
# 主题是**内容知识**不是行政/天数信号，单列函数供 pipeline 主题检索步消费；
# extract_trip 的出行判定同时认它作 trigger（「跟着《太平年》游杭州」此前
# 四个信号一个不中，落域进不去）。
_THEME_TITLE_RE = re.compile(r"《([^》]{1,20})》")
_THEME_FOLLOW_RE = re.compile(r"跟着\s*([^，。,、\s]{2,16}?)\s*(?:游|玩|逛)")
_THEME_TAG_RE = re.compile(r"([^，。,、\s]{2,16}?)\s*(?:同款|取景地|打卡地)")
# 通勤/固定地点：是导航日常目的地，不是多日出行
_TRIP_DEST_BLOCK = {"公司", "家", "单位", "学校", "上班", "这里", "那里", "机场", "车站"}
_CN_NUM = {"一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def extract_theme(text: str) -> str:
    """G4：主题串（影视/文化 IP）确定性解析；无主题返回空串。

    书名号优先（边界最硬）；「跟着X游」「X同款/取景地」两族剥掉动词/后缀取主体。
    只做识别不做映射——主题→具体地点的知识在 pipeline 主题检索步经 LLM 提议 +
    高德接地验证，这里绝不产地名。"""
    t = text or ""
    m = _THEME_TITLE_RE.search(t)
    if m:
        return m.group(1).strip()
    for pattern in (_THEME_FOLLOW_RE, _THEME_TAG_RE):
        m = pattern.search(t)
        if m:
            theme = m.group(1).strip("的 ")
            # lazy 捕获会吞句首动词（「去打卡繁花同款」→「去打卡繁花」）——剥掉。
            changed = True
            while changed and len(theme) > 2:
                changed = False
                for prefix in ("打卡", "看看", "去", "看", "找", "玩", "逛"):
                    if theme.startswith(prefix) and len(theme) - len(prefix) >= 2:
                        theme = theme[len(prefix):]
                        changed = True
            if theme:
                return theme
    return ""


def extract_trip(text: str) -> tuple[str, str, str]:
    """从话术解析 (destination, days, preferences)；非出行/无目的地返回空。"""
    text = text or ""
    m_dest = _TRIP_DEST_RE.search(text) or _TRIP_DEST_BEFORE_DAYS_RE.search(text)
    dest = (m_dest.group(1) if m_dest else "").strip()
    # 通勤/固定点用前缀判定（"公司开"仍算公司；"张家界"不会被单字"家"误杀）
    if not dest or any(dest.startswith(b) for b in _TRIP_DEST_BLOCK):
        return "", "", ""
    m_days = _TRIP_DAYS_RE.search(text)
    # 出行判定：有目的地，且（N天/N日 或 出行偏好词 或 N日游 或 行程/自驾游/度假
    # 或 主题标记——G4：「跟着《太平年》游杭州」此前四个信号一个不中）
    if not (m_days or _TRIP_PREF_RE.search(text) or "日游" in text
            or _TRIP_TRIGGER_RE.search(text) or extract_theme(text)):
        return "", "", ""
    days = ""
    if m_days:
        d = m_days.group(1)
        days = _CN_NUM.get(d, d)
    prefs = "、".join(w for w in _TRIP_PREF_WORDS if w in text)
    return dest, days, prefs

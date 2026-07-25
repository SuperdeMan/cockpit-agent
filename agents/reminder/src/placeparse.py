"""位置提醒的中文表达解析（M3 P1，纯函数，与 `timeparse.py` 同层）。

「到公司提醒我拿文件」「到家了提醒我收快递」「离开机场提醒我打车」。

**刻意不碰 ETA 型词形**（`到X之前` / `快到` / `到达前`）：那一族已由 P1c 的
`REMINDABLE_ACTIVE` 交接链走通（导航产 ETA → 提醒消费，旅程 B5-1 绿）。
本模块只认「到达即触发」的地理围栏语义，`之前/前/快到` 一律让路——
两条链路抢同一句话是 badcase 制造机（体感共现规则劫持提醒话术的老教训）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ARRIVE, LEAVE = "arrive", "leave"

# ETA 族词形：命中即让路（不是本模块的活）
_ETA_GUARD_RE = re.compile(r"之前|快到|到达前|抵达前|前一?刻钟|提前")

# 「到/到了/回到 <地点> [了]? [就]? 提醒我 …」/「离开/出了 <地点> …」
_ARRIVE_RE = re.compile(
    r"(?:^|[，,、。\s])?(?:等?)(?:到达|回到|到了|到)\s*"
    r"(?P<place>[^，。,、\s]{1,12}?)\s*(?:了)?\s*(?:以后|之后|时|的?时候)?\s*"
    r"(?:就)?\s*(?:再)?\s*(?:提醒我|叫我|记得|别忘了|提醒)")
_LEAVE_RE = re.compile(
    r"(?:^|[，,、。\s])?(?:等?)(?:离开|出了|开出)\s*"
    r"(?P<place>[^，。,、\s]{1,12}?)\s*(?:了)?\s*(?:以后|之后|时|的?时候)?\s*"
    r"(?:就)?\s*(?:再)?\s*(?:提醒我|叫我|记得|别忘了|提醒)")

# 地点名尾巴清理：「公司的时候」「家里」这类口语后缀
_PLACE_TAIL_RE = re.compile(r"(的?时候|以后|之后|那边|那儿|那里|附近)$")
# 不是地点的词（时间词误入）——命中即判为非位置提醒
_NOT_PLACE = {"时间", "点", "点钟", "明天", "今天", "后天", "早上", "晚上", "中午",
              "下午", "上午", "周末", "以后", "之后", "时候", "站", "会儿", "一会"}


@dataclass
class ParsedPlace:
    ok: bool = False
    place: str = ""
    trigger_on: str = ARRIVE
    title: str = ""          # 剥掉位置从句后的事项


def _clean_place(p: str) -> str:
    p = _PLACE_TAIL_RE.sub("", (p or "").strip(" 了的"))
    return p.strip()


def parse_place_text(raw: str) -> ParsedPlace:
    """解析位置提醒。不是位置提醒 → `ok=False`（调用方走原有时间链路，零影响）。"""
    text = (raw or "").strip()
    if not text or _ETA_GUARD_RE.search(text):
        return ParsedPlace()
    for regex, trigger in ((_ARRIVE_RE, ARRIVE), (_LEAVE_RE, LEAVE)):
        m = regex.search(text)
        if not m:
            continue
        place = _clean_place(m.group("place"))
        if not place or place in _NOT_PLACE or place.isdigit():
            continue
        # 事项 = 触发从句之后的部分（「提醒我」之后）
        title = text[m.end():].strip(" ，。,、的了")
        return ParsedPlace(ok=True, place=place, trigger_on=trigger, title=title)
    return ParsedPlace()


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """两点球面距离（米）。任一坐标缺失 → inf（判不出距离就是没到，绝不当成到了）。"""
    import math
    try:
        lat1, lon1, lat2, lon2 = (float(lat1), float(lon1), float(lat2), float(lon2))
    except (TypeError, ValueError):
        return float("inf")
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def name_hit(place: str, location) -> bool:
    """名称匹配兜底（PoC 下限）：车况 location 的 name/district/city 命中地点名即算到达。

    坐标缺失时才用它——**匹配必须双向包含**，防「北京」命中「北京西站提醒」这种泛匹配。
    """
    place = (place or "").strip()
    if not place or not isinstance(location, dict):
        return False
    for k in ("name", "district", "city", "address"):
        v = str(location.get(k) or "").strip()
        if v and (place in v or v in place):
            return True
    return False


def arrived(place_extra: dict, location, *, default_radius_m: int = 300) -> bool:
    """是否满足触发条件（到达/离开由调用方按 trigger_on 判读，此处只答「在不在里面」）。"""
    if not isinstance(location, dict):
        return False
    lat, lon = place_extra.get("lat"), place_extra.get("lon")
    vlat = location.get("lat")
    vlon = location.get("lng") if location.get("lng") is not None else location.get("lon")
    if lat is not None and lon is not None and vlat is not None and vlon is not None:
        radius = place_extra.get("radius_m") or default_radius_m
        return haversine_m(lat, lon, vlat, vlon) <= float(radius)
    return name_hit(str(place_extra.get("place") or ""), location)

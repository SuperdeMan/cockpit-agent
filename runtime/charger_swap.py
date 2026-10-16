"""「换一个充电站」的说法判据（CA2-19 换站）——导航（执行换站）与充电（识别后改派给导航）共用这一份。

只认充电这一类：测出来的旅程是导航途经充电站之后说「换一个充电站」。加油站等其它停靠类目要另立类目词表、
和导航既有的停靠类目映射对账，登记另做。
"""
from __future__ import annotations

import re

from runtime.route_stops import plain_name

#: 说法里指充电站的词。裸「桩」不算：「换个桩」多半是同一个站里换一根充电桩，不是换站。
_STATION = r"(?:充电站|充电桩|充电点|充电的地方|地方充电|快充站|超充站|快充|超充)"
#: 「换」后面的量词 / 指代，可连用两个（「换个别的」）：「换一个」「换家」「换别的」；不含「一条」（「换一条路」是换路线）
_WHICH = r"(?:一个|一家|一座|一下|个|家|座|别的|其他的?|另一个|另一家|另外一个|另外一家|下一个)"
#: 两种语序：「换一个充电站」/「这个充电站不行，换一家」；后者「换」之后不能接路线词（「…充电站，换个路线」是换路线）
_SWAP_RE = re.compile(
    rf"换{_WHICH}{{0,2}}(?:这个|那个)?{_STATION}"
    rf"|{_STATION}[^。？?！!]{{0,6}}?换{_WHICH}(?!路|条|道|线)")

#: 充电站名字里会出现的词：认路线上哪个途经点是充电站（高德充电站的名字基本都带其中之一）
CHARGER_NAME_WORDS = ("充电", "快充", "超充", "闪充", "换电")


#: 「再加一个充电站」：要的是**另一个**站（2026-10-10 真栈 swap2）。动词必须有，「再换一个充电站」是换站、不在这里
_ANOTHER_RE = re.compile(
    rf"(?:再|还|另外?|多)(?:加|找|来|要|添|安排|搜)(?:{_WHICH}){{0,2}}{_STATION}"
    rf"|(?:加|找|来|要|添)(?:另一个|另一家|另外一个|另外一家|别的|其他的?){_STATION}")


def asks_to_swap_charger(text: str) -> bool:
    """原话是不是在要「换一个充电站」。"""
    return bool(_SWAP_RE.search(text or ""))


def asks_for_another_charger(text: str) -> bool:
    """原话是不是在要「再加一个充电站」——路线上已有的站不算数。"""
    return bool(_ANOTHER_RE.search(text or ""))


def is_charger_name(name: str) -> bool:
    """按名字看一个停靠点是不是充电站。"""
    return any(word in (name or "") for word in CHARGER_NAME_WORDS)


#: 站名里不算「点名」的通用部分：括号注记（分店 / 位置）、类目词与常见后缀——用户说「换一个充电站」没点名任何一个站。
_GENERIC_STOP_PARTS_RE = re.compile(
    r"\([^)]*\)|电动汽车|新能源|汽车|超级充电站|充电站|充电桩|充电点|快充站|超充站|闪充站|换电站|充电|快充|超充|闪充|换电|站")


def names_stop(text: str, stop_name: str) -> bool:
    """原话有没有点名这个停靠点（200×5 后续批 §18.4）：站名去掉括号注记与类目通用词后，特征部分有连续两个字出现在原话里。

    「路特斯那个充电站换一家」点名了「路特斯汽车充电站(北站闪充站)」；「换一个充电站」一个站都没点名。导航换站据此分清
    「用户说的哪个站」与「规划器替用户挑的站」（核心旅程 EN07 r5：原话没点名，槽里却写了某个站名，换完才问换哪一个）。"""
    words = str(text or "")
    for part in _GENERIC_STOP_PARTS_RE.sub(" ", plain_name(stop_name)).split():
        if any(part[i:i + 2] in words for i in range(len(part) - 1)):
            return True
    return False

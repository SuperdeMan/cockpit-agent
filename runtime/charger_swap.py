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


#: 「（顺路）加个充电站」：在这趟路线上加一个充电停靠（核心旅程 EN09 第 2 句「那顺路加个充电站」）。动词必须是加 / 添 / 插——
#: 「找个充电站」是找站、不在这里；「再加一个充电站」也算（要另一个站，由导航按路线上已有的排除）。
_ADD_RE = re.compile(rf"(?:加|添|插)上?(?:{_WHICH}){{0,2}}{_STATION}")


def asks_to_add_charger(text: str) -> bool:
    """原话是不是在要「（顺路）加一个充电站」——正在导航时，这是改这趟路线。"""
    return bool(_ADD_RE.search(text or ""))


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


#: 不点名的修饰：「候选充电站」「一个充电站」「附近的充电桩」「顺路的快充」——指的是充电这一类，不是哪一个站。
# 「可用 / 能用 / 空闲 / 有空位」说的是状态不是站名（高德没有实时空闲数，也就按不了它搜）：`292bb8f0` 复测 EN07 r5，规划器把要加的站写成
# 「附近可用快充站」，导航按字面搜不到，第二个站没加上。「下一个 / 再一个 / 又一个 / 多一个」是「再来一个」，也不点名：`d23623a8` 复测
# EN07 r2 把要加的站写成「下一个充电站」，去掉「一个」剩个「下」，同样按字面搜不到。「免费 / 公共 / 直流 / 交流 / 大功率」同属属性、
# 不是站名（2026-10-18，§21.3：点名站名的试搜不拿它们当名字）。「普通 / 便宜 / 快速 / 人少 / 其他 / 电动车」与「上次 / 刚才」这类
# 指代同样不是站名（§22.1：修前「找个普通充电站」被当成点名「普通」去试搜，答「附近没找到普通的充电站」）。
_GENERIC_MODIFIERS_RE = re.compile(r"候选|随便|任意|另一个|下一个|再一个|又一个|多一个|一个|一家|一座|个|家|座|附近的?|最近的?|"
                                   r"顺路的?|沿途的?|路上的?|可用的?|能用的?|空闲的?|有空位的?|免费的?|公共的?|直流的?|交流的?|"
                                   r"大功率的?|普通的?|一般的?|便宜的?|实惠的?|快速的?|人少的?|不排队的?|其他的?|其它的?|别的|"
                                   r"电动车|电车|上次的?|上回的?|之前的?|刚才的?|刚刚的?|常去的?|的")


def is_generic_charger_word(text: str) -> bool:
    """停靠点说法只是泛指充电这一类（去掉类目词与不点名的修饰后什么都不剩）⇒ True；带品牌 / 站名特征的（「特斯拉充电站」）⇒ False。

    2026-10-17 EN07（`3d2f9a28` r3）：「途经再加一个充电站」被规划成 `add_waypoint=候选充电站`，导航按这四个字去搜、什么也没找到，
    第二个站没加上。导航据此把泛指的说法换成类目检索词；带品牌的照旧按字面搜（换成类目会把用户点名的品牌丢掉）。"""
    if not is_charger_name(text) and not _STATION_WORD_RE.search(str(text or "")):
        return False
    rest = _GENERIC_STOP_PARTS_RE.sub("", plain_name(str(text or "")))
    return not _GENERIC_MODIFIERS_RE.sub("", rest).strip()


_STATION_WORD_RE = re.compile(_STATION)

#: 「X 的充电站 / X 充电桩 / X 超充」里站名前面那个词：可能是品牌 / 运营商（「特斯拉」「国家电网」「特来电」），也可能是属性（「免费」）——
#: 字面上分不出，交给检索结果裁决（结果名字里真带着它才算，同周边的名字试探）。这里只把那个词取出来、挡掉明显不是名字的。
#: 2026-10-18（200×5 后续批 §21.3）：「附近有特斯拉的充电站吗」规划给 `charging.find`，充电 Agent 只按「充电站」搜、回 8 个普通站，
#: 用户点名的「特斯拉」被静默丢掉。
#: 「超级」算站名后缀的一部分（「理想超级充电站」点名的是「理想」）。
_BEFORE_STATION_RE = re.compile(rf"([^\s，,。？?！!、；;：:]*?)的?(?:超级)?{_STATION}")
#: 站名前面的方位 / 存在 / 请求 / 量词（零领域词）：名字在**最后一个**这种词之后——「在深圳北站附近找个特斯拉充电桩」「深圳北站附近
#: 有特斯拉充电站吗」。§22.1：修前只从分句开头剥框架，一句里先说了地点（带目的地的找站）就取不出名字，点名的品牌被静默丢掉。
#: 「来」「家」不在内：「特来电」「国家电网」。
_PROBE_CUT_RE = re.compile(
    r"附近|周边|周围|旁边|边上|一带|那边|这边|有没有|哪里有|哪儿有|哪有|还有|有|找一下|找一个|找一家|找几个|找个|找|"
    r"搜一下|搜|查一下|查|看看|看一下|看下|推荐|要|去|到|在|的|一个|一家|几个|几家")
#: 分句开头的请求 / 方位 / 存在框架（零领域词）：剥掉之后剩下的才可能是名字；可连用（「帮我找一下附近…」）
_PROBE_FRAME_RE = re.compile(
    r"^(?:请|麻烦|帮我|给我|你|我想|我要|想|要|在|去)?"
    r"(?:附近|周边|周围|这附近|这边|离我最近|最近)?的?"
    r"(?:有没有|哪里有|哪儿有|还有|有)?(?:找|搜|查|看看|看一下|看下)?(?:一个|一家|几个|一下|个|家|下)?")
#: 名字里不会有的字：虚词、方位、指代、疑问、数字与量词（「家」不在内：「国家电网」）
_PROBE_NON_NAME_CHARS = set("的了吗呢吧啊呀么里有没在哪谁什怎样个那这0123456789一二三四五六七八九十百千万元块")
_PROBE_MAX = 8


def charger_name_probe(text: str, place: str = "") -> str:
    """原话点名的充电站名字（「附近有特斯拉的充电站吗」⇒「特斯拉」）；只泛指充电这一类（「附近有充电站吗」「能用的充电桩」）、
    或那个词明显不是名字（带虚词 / 方位 / 数字，超长）⇒ 空串。

    place：这次找站围绕的地点（目的地槽）。先从原话里去掉它——「深圳北站特斯拉充电站」点名的是「特斯拉」，不是「深圳北站特斯拉」。"""
    text = str(text or "")
    if place and place.strip():
        text = text.replace(place.strip(), " ")
    for match in _BEFORE_STATION_RE.finditer(text):
        word = match.group(1)
        cut = 0
        for boundary in _PROBE_CUT_RE.finditer(word):
            cut = boundary.end()
        word, previous = word[cut:], None
        while word and word != previous:
            previous, word = word, _PROBE_FRAME_RE.sub("", word, count=1)
        if (2 <= len(word) <= _PROBE_MAX and not set(word) & _PROBE_NON_NAME_CHARS
                and not is_generic_charger_word(word + "充电站")):
            return word
    return ""


def carries_charger_name(name: str, probe: str) -> bool:
    """这个站名里带着原话点名的那个词（不分大小写、忽略空白），且按名字确实是充电站（特斯拉的展厅、服务中心不算）。"""
    def norm(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()
    return bool(probe) and norm(probe) in norm(name) and is_charger_name(name)


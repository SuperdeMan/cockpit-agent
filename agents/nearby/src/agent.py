"""周边发现 Agent —— 基于高德 POI 2.0 的富数据周边搜索 + 详情增强。

发现归本 Agent、出行归 navigation（见 docs/design/2026-07-05-nearby-discovery-redesign.md）：
本 Agent 只做「找 + 看详情」，导航由 HMI 卡片按钮 handoff 给 navigate 链路；nearby.order 为诚实预留桩。
Provider 适配层（mock/amap 经 env 切换）；真实源运行期失败**诚实降级**说拿不到，
不改供 mock 假 POI（治理 P0，conventions §9.4——假餐厅可能被导航过去，代价不对称）。
"""
from __future__ import annotations
import logging
import os
import re
import time

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from agents._sdk.timewindow import (
    clock_minutes, dining_window, fmt_clock, parse_event_time)
from .providers import build_place_provider
from .providers.base import GeoPoint, Place, is_open_now

logger = logging.getLogger("agent.nearby")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 室内组哨兵：类目归一为「室内」时不做单关键词检索，走 _search_indoor 多类目扇出——
# 高德按名称/类目匹配，「室内景点」这种抽象词只会退化成子串命中（badcase 4799fb1：
# planner 明确要室内，搜出去的却是户外公园）。
_INDOOR_SENTINEL = "室内"
# 扇出类目（宝安实测：壹方城4.9 / CGV影城4.7 / 宝安博物馆4.2）。串行检索——高德免费档
# QPS 紧，并发扇出会 CUQPS_HAS_EXCEEDED_THE_LIMIT。
_INDOOR_FANOUT = ("商场", "电影院", "博物馆")
# 户外类目：weather_context 在场时话术按天气承接（好天气鼓励、坏天气提示）
_OUTDOOR_CATS = {"景点", "景区", "旅游", "公园"}

# 类目 → 高德主检索词（关键词优先、稳健；types 精确化留 P1）。
# ⚠ 类目扫描按**插入序**取首个命中的子串（_resolve_category），顺序即优先级：
#   餐饮/住宿/影院/设施（停车/充电/加油）在前——「商场停车场」要归停车不归商场；
#   室内组必须在「景点」之前——「室内景点」含「景点」子串，后置会被抢走。
_CATEGORY_KEYWORD = {
    "餐饮": "美食", "美食": "美食", "吃饭": "美食", "餐厅": "美食", "吃的": "美食",
    # 时段词不是检索词：真栈实测「今晚带爸妈出去吃饭」→ planner 填 keyword=「晚饭」
    # → 拿它问高德，播成「为您找到 10 家**晚饭**」、结果是一串赛百味。
    "晚饭": "美食", "晚餐": "美食", "午饭": "美食", "午餐": "美食",
    "夜宵": "美食", "宵夜": "美食", "早饭": "早餐店", "早餐": "早餐店",
    "酒店": "酒店", "住宿": "酒店", "宾馆": "酒店", "民宿": "民宿",
    "影院": "电影院", "电影院": "电影院", "电影": "电影院",
    "停车": "停车场", "停车场": "停车场", "车位": "停车场",
    "充电": "充电站", "充电站": "充电站", "充电桩": "充电站",
    "加油": "加油站", "加油站": "加油站",
    "公共厕所": "公共厕所", "洗手间": "公共厕所", "卫生间": "公共厕所",
    "厕所": "公共厕所",
    "室内": _INDOOR_SENTINEL,
    "商场": "商场", "购物中心": "购物中心", "商城": "商场",
    "博物馆": "博物馆", "美术馆": "美术馆", "科技馆": "科技馆", "展览": "展览馆",
    "图书馆": "图书馆", "游乐": "游乐场", "KTV": "KTV", "唱歌": "KTV",
    "温泉": "温泉", "水族馆": "水族馆", "海洋馆": "水族馆",
    # G5（EVA 二轮）语义类目扩展：「带孩子看看动物」此前零覆盖、兜底还会错搜「美食」。
    # 动物园在动物之前（更具体先命中）；均在「景点」之前（插入序即优先级）。
    "动物园": "动物园", "动物": "动物园", "植物园": "植物园",
    "亲子": "亲子乐园", "遛娃": "亲子乐园", "书店": "书店",
    "景点": "景点", "景区": "景点", "旅游": "景点", "公园": "公园",
    "超市": "超市", "便利店": "便利店", "咖啡": "咖啡厅", "奶茶": "奶茶饮品",
    "药店": "药店", "银行": "银行", "医院": "医院",
}
# 餐饮类目（口味画像仅此类生效）
_FOOD_CATS = {"餐饮", "美食", "吃饭", "餐厅", "吃的",
              "晚饭", "晚餐", "午饭", "午餐", "夜宵", "宵夜"}
# P5（EVA 遗留卡）：口味记忆的**消费面**类目——比 _FOOD_CATS 宽。此前口味召回
# 门禁只认正餐类目，咖啡/奶茶搜索整个被排除 → 「这家咖啡太酸」的店铺级差评
# **结构性永不降权**（2026-08-15 真栈实测：带店名条目已入库、结果集纹丝不动）。
# 菜系偏置（like_cuisine 改检索词）仍只限 _FOOD_CATS——拿粤菜偏好去偏置咖啡
# 检索是错的；降权/忌口消费按本集合。
_TASTE_CATS = _FOOD_CATS | {"咖啡", "咖啡厅", "奶茶", "甜品", "面包", "烘焙", "饮品"}
# G5：原话里的饮食信号——只有它在场时才允许落「餐饮」默认类目。
# 「看看动物的地方」落「美食」是给出错误结果，比失败更糟。
_FOOD_HINT_RE = re.compile(
    r"吃|喝|饭|餐|饿|菜|夜宵|早茶|外卖|美食|小吃|火锅|烧烤|甜品|日料|自助")
# G5：氛围属性词（「安静点的地方喝咖啡」）。高德没有安静度字段——能做的是
# 环境类标签+评分的软重排，并在话术里如实说数据边界（不假装有安静度）。
_AMBIENCE_RE = re.compile(r"安静|清静|清净|环境好|氛围好|不吵|幽静")
_AMBIENCE_TAGS = ("环境", "安静", "书", "清吧", "花园", "庭院", "湖景", "江景")
# ── E2（G5 余项）无障碍 / 停车便利 / 不排队三族 ────────────────────────
# 原话显式触发。「无台阶/少走路」高德没有任何字段——能做的**近似**只有一个：
# 目的地半径内的停车场（缺口分析 §2-G5 给的方向）。近似要说明是近似。
_ACCESS_RE = re.compile(
    r"腿脚不便|腿脚不好|腿脚不太?方便|不方便走路|走路不方便|走不动|走太多路|少走路|"
    r"轮椅|无障碍|台阶|拄拐|推车|婴儿车|"
    # 停车说法允许中间插词：真栈实测「停车最好方便一点」不匹配连写的「停车方便」，
    # 整条属性维就没触发（正则按连写写死＝只认一种语序）。
    r"停车[^，。！？]{0,4}(?:方便|好停|近|好找)|(?:方便|好)停车|停车位好找")
# 记忆驱动触发：话里点名家人/老人时才去读画像（不是每次搜索都翻记忆）。
_ELDER_RE = re.compile(r"爸妈|父母|老人|长辈|老年|奶奶|爷爷|外公|外婆")
_MOBILITY_MEM_RE = re.compile(
    r"腿脚|(?:行动|走路|走动)不(?:太)?(?:方便|便)|不方便走|走不动|轮椅|拄拐")
# 「不排队」：**没有实时排队数据就说没有**，不拿评分/人气冒充（六#3 语料的另半边）。
# 原话与**记忆文本**共用一条：记忆里是「老婆不喜欢排队」，原话里是「不排队」，
# 两处各写一条正则迟早只改一处（P5 那个门禁漏洞就是同一形态）。
_NO_QUEUE_RE = re.compile(
    r"(?:不喜欢|不爱|讨厌|嫌|怕|不愿意?|不想|别|不用|不)\s*(?:排队|等位)|排队少")
_PARKING_PROBE_K = 4          # 停车探测的候选上限：高德免费档 QPS 紧，串行且有界
_PARKING_RADIUS_KM = 0.3      # 「停车近」的近似半径


def _weather_word(weather: str) -> str:
    """weather_context 槽值 → 话术用的天气词（「中雨」「下雨」→「雨天」）。
    识别不出恶劣天气返回空串（好天气/未知不套坏天气话术）。"""
    w = (weather or "").strip()
    if not w:
        return ""
    for zi, label in (("雷", "雷雨天"), ("雨", "雨天"), ("雪", "雪天"),
                      ("雹", "冰雹天"), ("台风", "台风天"), ("高温", "高温天"),
                      ("酷暑", "高温天"), ("炎热", "高温天"), ("闷热", "闷热天"),
                      ("沙尘", "沙尘天"), ("霾", "雾霾天"), ("风", "大风天")):
        if zi in w:
            return label
    return ""


def _to_float(v) -> float:
    try:
        s = str(v).replace("元", "").replace("¥", "").replace("￥", "").strip()
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cost_display(cost: str) -> str:
    c = (cost or "").strip()
    return f"{c}元" if c.isdigit() else c


# 详情说法剥壳：把「看第2个详情 / 蜀香源怎么样 / 这家电话多少」还原成核心店名。
# route_hints 用 $text 把整句灌进 name 槽，必须剥掉发现/详情措辞才能进高德检索（类比导航剥「导航去」前缀）。
_DETAIL_PREFIX_RE = re.compile(
    r'^(看看|看|查查|查看|查|了解|想看)?\s*(第\s*[一二两三四五六七八九十\d]+\s*[个家]?)?\s*(这家|那家|这个|这间|它家?)?\s*')
_DETAIL_SUFFIX_RE = re.compile(
    r'\s*的?(详情|详细信息|怎么样|好不好|好吗|评分|人均|多少钱|电话|营业时间|几点[关开]门?|地址|信息)\s*$')


def _clean_name(raw: str) -> str:
    """剥离发现/详情措辞，取核心店名；剥空则回退原文（由上层反问）。"""
    s = (raw or "").strip()
    for _ in range(3):
        s2 = _DETAIL_SUFFIX_RE.sub("", _DETAIL_PREFIX_RE.sub("", s)).strip(" 的，。、")
        if s2 == s:
            break
        s = s2
    return s or (raw or "").strip()


# ── 发现说法解析（不依赖弱 LLM 填槽，从原话/关键词兜底）──
_CN_NUM = {"十": 10, "二十": 20, "三十": 30, "四十": 40, "五十": 50, "六十": 60,
           "七十": 70, "八十": 80, "九十": 90, "一百": 100, "两百": 200, "二百": 200,
           "三百": 300, "四百": 400, "五百": 500, "百": 100}


def _cn_to_int(s: str) -> int:
    return int(s) if s.isdigit() else _CN_NUM.get(s, 0)


# 价位（区间）：『一百以内』→(0,100)、『二百以上』→(200,0)、『一百左右』→(60,140，约±40%）
_PRICE_RE = re.compile(
    r"(?:人均|均价|价位|预算|不超过|不高于)?\s*([0-9]{2,4}|[一二两三四五六七八九十百]+)\s*"
    r"(?:元|块钱|块)?\s*(以内|以下|之内|封顶|左右|上下|以上|起)")
_SORT_RATING_RE = re.compile(r"评分高|高分|口碑好?|好评|人气高?|评价高|最好的")
_OPEN_NOW_RE = re.compile(r"营业中|还(在)?营业|正在营业|现在营业|营业吗|还开(着|门)?|没(有)?关门|开着(吗|的)?")
# 查询动词 + 发现措辞：route_hint 把整句灌进 keyword 时，须剥掉才能得干净类目/菜系词
_PROXIMITY_RE = re.compile(
    r"帮(我|忙)一?下?|给我|替我|我想|我要|想要|请问?|麻烦|帮忙|"
    r"查一?查|查一下|查询|查找|查看|搜一?搜|搜一下|搜索|找一?找|看一?看|看下|瞧瞧|了解一?下?|"
    r"附近的?|周边的?|就近的?|最近的?|旁边的?|这边的?|一带的?|附近有|"
    r"哪儿?有|哪里有|有没有|有什么|有啥|找个?|找家|找点|来个?|来点|"
    r"推荐个?|推荐家|推荐点|想吃个?|想去个?|什么|好吃的?|好玩的?")


def _strip_proximity(text: str) -> str:
    return _PROXIMITY_RE.sub("", text or "").strip(" 的，。、")


_BRAND_PREFIX_MAX = 5          # 品牌名上限：瑞幸/星巴克/特斯拉/肯德基/蜜雪冰城
_NON_BRAND_CHARS = set("的了吗呢吧啊呀吧 ，。、！？0123456789一二三四五六七八九十百千万元块")


def _brand_qualified(cleaned: str) -> str:
    """『品牌 + 类目别名』→ 原样返回；否则空串（调用方退回干净类目词）。

    只认「短且不含虚词/数量词的前缀 + 已知类目别名」这一种形状：
    『瑞幸咖啡』『特斯拉充电桩』认，『人均百元的停车场』『充电桩』不认。
    宁可漏认（退回类目词＝今天的行为）也不能错认——错认会把整句灌给高德。
    """
    for alias in sorted(_CATEGORY_KEYWORD, key=len, reverse=True):
        if not cleaned.endswith(alias) or cleaned == alias:
            continue
        prefix = cleaned[:-len(alias)]
        if len(prefix) <= _BRAND_PREFIX_MAX and not (set(prefix) & _NON_BRAND_CHARS):
            return cleaned
        return ""              # 命中最长别名即定案，不再拿更短别名重试
    return ""


def _strip_qualifiers(text: str) -> str:
    """从关键词里剥掉价位/评分/营业/查询措辞，留核心类目/菜系词（『人均一百左右的火锅』→火锅）。"""
    s = _PRICE_RE.sub("", text or "")
    s = _SORT_RATING_RE.sub("", s)
    s = _OPEN_NOW_RE.sub("", s)
    return _strip_proximity(s)


def _parse_price(text: str) -> tuple[float, float]:
    """从原话解析人均区间 (下限, 上限)，0=该端不限。解析不出→(0,0)。"""
    m = _PRICE_RE.search(text or "")
    if not m:
        return (0.0, 0.0)
    n = _cn_to_int(m.group(1))
    if n <= 0:
        return (0.0, 0.0)
    q = m.group(2)
    if q in ("左右", "上下"):
        return (float(round(n * 0.6)), float(round(n * 1.4)))
    if q in ("以上", "起"):
        return (float(n), 0.0)
    return (0.0, float(n))                          # 以内/以下/之内/封顶


def _parse_sort(text: str) -> str:
    return "rating" if _SORT_RATING_RE.search(text or "") else ""


_NAMED_POI_SUFFIXES = ("店", "餐厅", "门店", "分店")


def _named_poi_query(keyword: str) -> bool:
    """检索词是否指名了**具体门店/场所**（「瑞幸咖啡(深铁金融科技大厦店)」「麦当劳 国贸店」）。

    指名场所按名检索不依赖位置——那是名字查找；而「咖啡店/停车场」这类品类词没有
    位置就没有「附近」可言。判据刻意收紧：含括号（高德 POI 命名习惯），或以门店后缀
    结尾且去掉后缀后仍 ≥4 字（「咖啡店」「便利店」这类纯品类词不算指名）。"""
    kw = str(keyword or "").strip()
    if not kw:
        return False
    if "(" in kw or "（" in kw:
        return True
    return any(kw.endswith(suffix) and len(kw) - len(suffix) >= 4
               for suffix in _NAMED_POI_SUFFIXES)


def _parse_open_now(text: str) -> bool:
    return bool(_OPEN_NOW_RE.search(text or ""))


# 餐饮检索词的正面白名单：菜系/菜品/食材字眼。认不出的一律退回干净类目词
# （「不辣」「适合带老人」这类**约束词**当店名去搜，比少一个检索维度糟得多）。
_DISH_MARKERS = (
    "菜", "馆", "锅", "料理", "日料", "韩料", "烧烤", "小吃", "面", "粉", "饭",
    "餐", "咖啡", "茶", "甜品", "烘焙", "披萨", "汉堡", "寿司", "烤肉", "海鲜",
    "粥", "串", "饺", "包子", "牛排", "自助", "素食", "火锅", "简餐", "轻食",
    "肉", "鱼", "虾", "蟹", "鸡", "鸭", "豆腐", "清真", "brunch", "buffet",
)


def _looks_like_dish(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(t) and any(m in t for m in _DISH_MARKERS)


class NearbyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.place = build_place_provider()

    @staticmethod
    def _now_ts() -> int:
        """时间锚（实例可替换）。用餐窗判定依赖「现在几点」——不留注入口，
        用例就只能写成「在某些真实时刻会红」的样子（那是假红的制造机）。"""
        return int(time.time())

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "nearby.search": self._search,
            "nearby.detail": self._detail,
            "nearby.order": self._order,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="周边助手暂不支持该请求。")

    # ── 位置 / 类目 / 关键词 ──
    async def _resolve_center(self, intent, meta) -> GeoPoint | None:
        """搜索中心解析（R3 残余根因，旅程 B1-3）。

        location 槽是**地名**时（焦点指代：「那附近有停车场」→ LLM 从焦点填
        location=万象天地），不能直接交给无城市偏置的 geocode——「万象天地」全国
        歧义，真栈解析到**呼和浩特**分店。先用富 provider 按当前坐标偏置搜该名，
        top1 名字包含校验通过才取其坐标；搜不到/校验不过回退地址 geocode（原行为）。
        """
        near = self._near(intent, meta)
        if near is None or near.lat or not near.address:
            return near                      # 无位置 / 已是坐标 → 原样
        cur = current_location_from_meta(meta)
        bias = GeoPoint(lat=cur.lat, lng=cur.lng) if cur else None
        try:
            hits = await self.place.search(near.address, near=bias, meta=meta)
        except ProviderError as e:
            logger.debug("center resolve search failed: %s", e)
            hits = []
        if hits:
            top = hits[0]
            a, b = near.address, (top.name or "")
            if a and b and (a in b or b in a) and top.lat and top.lng:
                logger.info("center %r resolved to %r (%.4f,%.4f)",
                            near.address, top.name, top.lat, top.lng)
                return GeoPoint(lat=top.lat, lng=top.lng)
        return near

    # 地点指代词（旅程 B1-3「那附近有停车场」）：与 info 侧 `_DESTINATION_DEICTIC_RE`
    # 同族（那边/那儿/那里/目的地/终点），nearby 再收「那附近」。只有话里带指代时才
    # 消费焦点坐标——普通「附近有什么好吃的」必须仍按当前 GPS，不许被上次导航劫持。
    _DEST_DEICTIC_RE = re.compile(r"那附近|那边|那儿|那里|目的地|终点")

    @classmethod
    def _near(cls, intent, meta) -> GeoPoint | None:
        """搜索中心：显式 location 槽位（坐标或地名）> 地点指代+焦点目的地坐标 > 本轮
        已授权 GPS。无任何位置 → None（provider 走关键字检索，不拿任意城市冒充「附近」）。

        第二级（B1-3 确定性化，2026-08-14）：「导航去万象天地」→「那附近有停车场吗」
        此前依赖 planner LLM 看焦点 prompt 填 location 槽——软路径方差（7/25 journeys
        绿、8/14 两跑皆红），而同一枚焦点坐标 weather 侧早已确定性消费（B1-2 稳定绿）。
        engine 按 manifest `context_scopes: [location]` 把 `focus_destination_*` 注进
        meta，与 info `_deictic_destination` 同款：LLM 与客户端都写不到这三个键。"""
        loc = (intent.slots.get("location") or "").strip()
        if loc:
            parts = loc.split(",")
            if len(parts) == 2:
                try:
                    return GeoPoint(lng=float(parts[0]), lat=float(parts[1]))
                except ValueError:
                    pass
            return GeoPoint(address=loc)
        if cls._DEST_DEICTIC_RE.search(intent.raw_text or ""):
            try:
                lat = float((meta or {}).get("focus_destination_lat", ""))
                lng = float((meta or {}).get("focus_destination_lng", ""))
            except (TypeError, ValueError):
                lat = lng = None
            if lat is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
                logger.info("nearby center from focus destination %r",
                            (meta or {}).get("focus_destination", ""))
                return GeoPoint(lat=lat, lng=lng)
        cur = current_location_from_meta(meta)
        if cur:
            return GeoPoint(lat=cur.lat, lng=cur.lng)
        return None

    @staticmethod
    def _resolve_category(intent) -> str:
        """类目：category 槽位优先；否则从原话+keyword 槽扫类目词（route_hint 把整句灌进 keyword）。"""
        raw = (intent.slots.get("category") or "").strip()
        # A recognized explicit category wins.  A coarse/unknown category
        # ("公共设施"/"生活服务") must still let the user's actual words refine
        # it; otherwise an empty keyword falls through to the historic food
        # default even for an explicit restroom request.
        for hay in (raw, (intent.raw_text or "") + " "
                    + (intent.slots.get("keyword") or "")):
            for key in _CATEGORY_KEYWORD:
                if key in hay:
                    return key
        if raw:
            return raw
        # G5（EVA 二轮）：全无类目命中且没有任何饮食信号 → 不再默认餐饮
        # （「看看动物的地方」落「美食」是错误结果不是兜底）。返回空串，
        # _search 对「类目/菜系/品牌/关键词全空」的情形诚实追问。
        # 饮食信号面含 cuisine 槽（「川菜」这类菜系词不在类目表里，但它就是餐饮）。
        hay_all = (f"{intent.raw_text or ''} {intent.slots.get('keyword') or ''} "
                   f"{intent.slots.get('cuisine') or ''}")
        return "餐饮" if _FOOD_HINT_RE.search(hay_all) else ""

    @staticmethod
    def _build_keyword(category, cuisine, brand, kw_slot) -> str:
        """高德检索词：品牌/菜系优先；设施/类型类目（停车场/充电站/酒店…）直接用干净类目词，
        避免把带动词/价位的整句（『帮我查一查人均百元的停车场』）当关键词；仅餐饮用剥壳后的具体词。"""
        if brand:
            return brand                       # 品牌是专名（瑞幸/星巴克），不做菜品判定
        if cuisine:
            # cuisine 槽同样会被填进约束词——真栈实测 planner 把「不辣」「适合带老人」
            # 填在这里，而这条分支是**早退**，下面餐饮分支的守卫根本够不着
            # （首版只补了那一处，复验时原样复现：「为您找到 10 家不辣」）。
            if _looks_like_dish(cuisine):
                return cuisine
            cuisine = ""                       # 不是菜系 → 丢掉，继续按类目走
        # 指名门店（「瑞幸咖啡 深铁金融科技大厦店」/「麦当劳(科苑南路餐厅)」）原样保留：
        # 它比任何类目/品牌词都具体，被改写成「咖啡厅」是静默的信息丢失——
        # 候选卡按钮/用户点名的选店句正是这种形状（demo-mkemhn 选店死路的一环）。
        named = _strip_qualifiers(kw_slot)
        if _named_poi_query(named):
            return named
        cat_kw = _CATEGORY_KEYWORD.get(category)
        if cat_kw and category not in _FOOD_CATS:      # 设施/非餐饮类目 → 干净类目词
            # 但『瑞幸咖啡』『特斯拉充电桩』这种**品牌 + 类目别名**是用户明确点名的东西，
            # 比类目更具体——用类目词覆盖它是静默的信息丢失（2026-08-12 实测：
            # 「附近的瑞幸咖啡店」被改写成「咖啡厅」，返回一半非瑞幸门店，而同 plan 的
            # luckin.order 直接取 items.0 当下单门店）。
            # 判据刻意收得很紧（见 _brand_qualified）：**不能靠 _strip_qualifiers 兜底**
            # ——它只剥价位/评分/营业/查询措辞，『人均百元的停车场』剥完还是整句，
            # 正是本分支当初存在的理由。剥不成品牌形状的一律退回类目词。
            return _brand_qualified(_strip_qualifiers(kw_slot)) or cat_kw
        cleaned = _strip_qualifiers(kw_slot)           # 餐饮：剥掉价位/评分/动词后的具体词（火锅/川菜）
        if cleaned and cleaned not in ("地点", "的"):
            # 剥完若正好是**类目别名**（「吃饭」「晚饭」），它不是检索词：拿它问高德
            # 会搜名字里带「晚饭」的店，话术还念成「为您找到 10 家晚饭」（真栈实测）。
            if cleaned in _CATEGORY_KEYWORD:
                return _CATEGORY_KEYWORD[cleaned]
            # **约束词不是检索词**（2026-08-15 双档真栈实测的两个恶例）：
            # planner 填 keyword=「不辣」→ 搜出一串「辣可可·现炒黄牛肉」，
            # 填 keyword=「适合带老人」→ 搜出家政公司。餐饮分支此前对剥壳结果
            # 无条件信任，而「剥不掉的那部分」既可能是菜系，也可能是**约束**。
            # 判据取正面白名单（菜系词/餐饮字眼）——认不出就退回干净类目词，
            # 与上面「整句：漏认，退回类目」同一条纪律：**宁可少检索维度，
            # 也不能拿约束词当店名去搜**。
            if _looks_like_dish(cleaned):
                return cleaned
            return cat_kw or "美食"
        # Preserve an explicit unknown/coarse category as the provider query;
        # silently rewriting it to food is a semantic corruption.  Only a
        # genuinely absent category keeps the legacy nearby-food default.
        return cat_kw or _strip_qualifiers(category) or "美食"

    @staticmethod
    def _item(p: Place) -> dict:
        # lat/lng 供 HMI「导航去第N个」handoff（同 navigation poi_list 形状）；
        # city 供商户官方检索（麦当劳 searchType=2 按位置搜时城市必填）
        # `open_week` 2026-08-19 加入（Q2 残余）：`open_today` 厂商常缺，而
        # 「周一至周日 10:00-22:00」这种一周概述照样能判出收盘时刻。云侧
        # `candidate_query.dimension_value` 按 today→week 的权威序取值——
        # 今日实况优于一周概述。加它之前 `_CANDIDATE_ITEM_KEYS` 里的 `open_week`
        # 是个**死键**（本批修死键时差点又制造一个）。
        return {"id": p.id, "name": p.name, "category": p.category,
                "rating": p.rating, "cost": p.cost, "distance_km": p.distance_km,
                "address": p.address, "city": p.city, "tags": p.tags,
                "open_today": p.open_today, "open_week": p.open_week,
                "lat": p.lat, "lng": p.lng}

    @staticmethod
    def _known_attrs(p: Place) -> str:
        bits = []
        if p.rating:
            bits.append(f"评分{p.rating}")
        if p.cost:
            bits.append(f"人均{_cost_display(p.cost)}")
        return "、".join(bits)

    async def _search(self, intent, ctx, meta) -> AgentResult:
        category = self._resolve_category(intent)
        # G5：类目/菜系/品牌/关键词全空且无饮食信号 → 诚实追问，不猜「美食」
        if not category and not any(
                (intent.slots.get(k) or "").strip()
                for k in ("cuisine", "brand", "keyword")):
            return AgentResult(
                status=NEED_SLOT,
                speech="想找哪一类地方？比如餐厅、动物园、商场或公园。",
                follow_up="说个类目我就近帮你找", missing_slots=["category"])
        weather = (intent.slots.get("weather_context") or "").strip()
        # 室内组（「(下雨天)去哪玩」→ planner 填 category=室内 + weather_context）：
        # 单一关键词表达不了「适合室内玩的地方」，走多类目扇出组合推荐
        if _CATEGORY_KEYWORD.get(category) == _INDOOR_SENTINEL:
            return await self._search_indoor(intent, ctx, meta, weather)
        cuisine = (intent.slots.get("cuisine") or "").strip()
        brand = (intent.slots.get("brand") or "").strip()
        kw_slot = (intent.slots.get("keyword") or "").strip()
        keyword = self._build_keyword(category, cuisine, brand, kw_slot)
        raw = intent.raw_text or ""
        # G6：口味偏好在检索**前**生效。泛餐饮发现（用户没点菜系/品牌/关键词）时，
        # 记忆里的喜好菜系直接偏置检索词；用户点名的东西永远优先于记忆。
        taste = None
        taste_notes: list[str] = []
        # E1（G1 余项）：事件时刻 → 用餐窗反推。「晚上7点的电影，先吃个饭」此前只能
        # 诚实澄清（「想让我怎么安排？」）——`arrive_by` 是「几点到」，这是它的镜像
        # 半边「几点该吃完」。路上预留是**明说的假设**，话术必须念出来。
        window = None
        if category in _TASTE_CATS:
            now_ts = self._now_ts()
            ev = parse_event_time(raw, now_ts=now_ts)
            if ev:
                window = dict(dining_window(ev[0], now_ts=now_ts), event_word=ev[1])
            taste = await self._taste_profile(ctx, raw)
            # **当轮明说的忌口压过记忆偏好**（真栈实测：「不要太辣」时两个 provider
            # 都推了川菜——记忆说爱吃川菜、当轮说不要辣，系统选了记忆）。
            # 记忆是背景，用户这句话是前景；前景与背景冲突时前景赢，并且要说出来。
            turn_no_spicy = bool(self._NO_SPICY_RE.search(raw))
            if turn_no_spicy:
                # 没有任何口味记忆时也要生效——「不要太辣」本身就是一条约束，
                # 不该因为画像是空的就被丢掉。
                taste = dict(taste or {"like_cuisine": "", "dislikes": [],
                                       "no_queue": False}, no_spicy=True)
            # 菜系偏置只限正餐类目（拿粤菜偏好偏置咖啡检索是错的）；
            # 忌口/店铺级降权按 _TASTE_CATS 全集生效（P5）。
            if (category in _FOOD_CATS and taste and taste["like_cuisine"]
                    and not (cuisine or brand or kw_slot.strip())):
                liked = taste["like_cuisine"]
                if turn_no_spicy and any(w in liked for w in self._SPICY_MARKS):
                    taste_notes.append(f"您说了不要辣，这次就不按平时爱吃的{liked}找了")
                else:
                    keyword = liked
                    taste_notes.append(f"按您的口味优先{liked}")
        rating_min = _to_float(intent.slots.get("rating_min"))
        # 价位/排序/营业中：原话解析优先（『一百左右』的区间语义只在原话里，LLM 填的 price_max
        # 槽位会丢下限 → 之前『左右』返回太便宜的）；原话无价位再退回 LLM 槽位。
        price_min, price_max = _parse_price(raw + " " + kw_slot)
        if not (price_min or price_max):
            price_max = _to_float(intent.slots.get("price_max"))
        sort = (intent.slots.get("sort") or "").strip() or _parse_sort(raw)
        open_now = str(intent.slots.get("open_now") or "").lower() in ("1", "true", "yes") \
            or _parse_open_now(raw)
        near = await self._resolve_center(intent, meta)
        # 话术标签用**净化后**的检索词：cuisine 槽里的约束词（「不辣」）被 _build_keyword
        # 丢掉后，label 若还读原槽，就会播成「为您找到 10 家不辣」（真栈原样复现过）。
        label = brand or keyword

        # 位置缺席的诚实降级（demo-mkemhn 59b34983/cffc84fd/44943f00）：没有任何
        # 搜索中心时，provider 会走**全国关键字检索**，高德默认把北京热门 POI 排前面
        # ——把它播成「附近/最近」是在冒充，用户纠正（「不是北京哦」）也无从生效，
        # 因为系统根本不知道自己少了什么。品牌/品类发现类检索没有位置就没有「附近」；
        # **指名门店**（_named_poi_query）仍放行——那是名字查找，不依赖位置。
        if near is None and not _named_poi_query(keyword):
            logger.warning(
                "nearby.search without center（诚实降级，不拿全国检索冒充附近）: %s",
                keyword)
            return AgentResult(
                speech=f"我现在拿不到车辆的位置，没法确定哪家{label}算「附近」。"
                       f"可以检查一下定位开关，或者告诉我一个参照位置，"
                       f"比如「在科技园附近找{label}」。",
                follow_up="说一个参照位置我就能继续找。",
                data={"items": [], "center": "none"})

        skw = dict(category=category, near=near, rating_min=rating_min,
                   price_min=price_min, price_max=price_max, brand=brand,
                   sort=sort, open_now=open_now, meta=meta)
        try:
            results = await self.place.search(keyword, **skw)
        except ProviderError as e:
            # 真实源失败不再改供 mock 假 POI（假餐厅可能被用户导航过去）——诚实说拿不到。
            # M0a 对齐 R9 契约：OK 话术（单步 FAILED 的 speech 被聚合器吞成裸「处理失败」）。
            logger.warning("place search failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(speech="周边搜索服务暂时不可用，稍后再试一次？")

        if not results:
            if near is None:
                return AgentResult(
                    speech=f"没找到叫「{label}」的门店，换个说法再试试？",
                    follow_up="报出完整店名（如『瑞幸咖啡(科技园店)』）更容易找到")
            return AgentResult(
                speech=f"附近暂时没找到{label}，换个说法或扩大范围再试试？",
                follow_up="可以说『附近的火锅』或『评分高的川菜馆』")

        # E1：按**入座时刻**筛营业中（这一条不是近似，是真实数据：business.opentime_today）。
        # 「明确闭店才剔，未知保留」与既有 open_now 同纪律；全被剔时不硬凑——保留原列表
        # 并如实说那个点大多不营业。
        window_notes: list[str] = []
        if window and not window["tight"]:
            seat_min = clock_minutes(window["seat_ts"])
            open_at_seat = [p for p in results
                            if is_open_now(p.open_today, seat_min) is not False]
            if open_at_seat:
                results = open_at_seat
            else:
                window_notes.append("不过那个点附近这些店大多不营业，可能得换个时间或换个地方")

        # G5：氛围属性软重排（「安静点的地方」）。高德没有安静度字段——按环境类
        # 标签 + 评分排前（稳定序），话术如实说数据边界，不假装有安静度。
        if _AMBIENCE_RE.search(raw) and results:
            results = sorted(results, key=lambda p: (
                0 if any(t in (p.tags or "") for t in _AMBIENCE_TAGS) else 1,
                -(p.rating or 0)))
            taste_notes.append("您要安静些的——地图没有安静度数据，已按环境类标签和评分优先")
        # E2：行动不便 → 停车便利近似重排。**先于口味降权**跑：用户当轮点名的负偏好
        # 是更强的信号，得由它说最后一句话（否则停车排序会把降权项拉回前面）。
        access: dict = {}
        reason = await self._mobility_reason(ctx, raw)
        if reason and results:
            results, parking = await self._parking_rerank(results, meta)
            access = {"reason": reason, "parking": parking}
            if parking and any(s["count"] for s in parking):
                taste_notes.append(
                    f"{reason}——地图没有无障碍/台阶数据，已按周边停车便利度排序")
            else:
                taste_notes.append(f"{reason}——地图没有无障碍数据，这条我按不上")
        # G6：负偏好软降权（忌辣/店名级差评 → 结果后移），话术只报**真实生效**的项
        # ——此前这里是搜完才召回、只拼「已参考您口味」进话术，结果集一条没变（假个性化）。
        if taste:
            results, moved = self._taste_rerank(results, taste)
            if moved:
                taste_notes.append("不合口味的已排后")
            caution = self._taste_caution(taste, cuisine or keyword)
            if caution:
                taste_notes.append(caution)
        # E2：「不排队」原话或记忆在场 → 如实说没有这维数据（不拿评分/人气冒充）。
        if _NO_QUEUE_RE.search(raw) or (taste and taste.get("no_queue")):
            taste_notes.append("地图没有实时排队数据，这条我按不上")
        pref_note = f"（{'；'.join(taste_notes)}）" if taste_notes else ""

        items = [self._item(p) for p in results]
        names = "、".join(p.name for p in results[:3])
        extra = self._known_attrs(results[0])
        extra_s = f"，{results[0].name}{extra}" if extra else ""
        # 户外类目 + weather_context 在场：话术承接天气（planner 按 guide 只在天气已知时填）
        lead = ""
        if weather and category in _OUTDOOR_CATS:
            word = _weather_word(weather)
            lead = f"{word}户外体验会打折扣，" if word else "天气不错，适合出去走走，"
        if window:
            lead = self._window_lead(window, window_notes) + lead
        # 只进 data（编排 slot_refs + obs 排查有真实消费方），**不进卡片**——
        # HMI 没有渲染这两块，落进卡片就是无消费方的死字段（B4 那条判据）。
        extra_data: dict = {}
        if window:
            extra_data["dining_window"] = window
        if access:
            extra_data["access"] = access
        # Q2/N5 保留键 `_fallback`：这一份候选是**我猜的那一类**还是**用户点名的那一份**。
        # 判据：**用户给了一个具体词，而我们最终没拿它去搜**——`keyword`/`cuisine` 槽
        # 非空，检索词却退回了干净类目词。
        # 出处：I-011 的真根因不是「失败的重搜清空了候选」——那次重搜**根本没失败**：
        # 泛化兜底搜出 10 家「美食」，于是它**合法地**覆盖了上一份川菜候选，
        # 第三轮「刚才列表里的第二家」拿到的是兜底那份的第二家。
        # **由产生方声明**（同 `_route_session`/`_safety_alert`）：只有这里知道
        # 「搜的和他说的是不是一回事」，编排看不出来。
        # ⚠ 判据首版写的是「检索词等于干净类目词」，实测把**用户点名的类目**
        # （「附近的停车场」——干净类目词正是对的检索词）一起标成了兜底。
        # 标反方向比漏标贵：真候选会被当成兜底忽略。所以判据必须落在
        # 「**有没有一个用户说了、我们却丢掉的词**」上，不落在检索词长什么样上。
        # ⚠ 判据是**两个信号取或**，因为单一信号各有够不着的一半（真栈 3 轮实测）：
        #   ① 用户给了具体词、我们丢掉了（keyword/cuisine 槽非空却退回类目词）；
        #   ② 类目本身是**猜**出来的——`_resolve_category` 的末行
        #      `return "餐饮" if _FOOD_HINT_RE...` 那条兜底分支，没有任何类目键
        #      在用户话里出现过。
        # 只有 ① 时漏判：planner 对「附近有没有卖锟斤拷的店」有时**一个具体词都不填**
        # （只给 category=餐饮），nearby 就没有可比对的东西 ⇒ 兜底没被标出来，
        # 序数当场绑到它上面（CD2 三次取样里的那一次）。
        # 只有 ② 时误判：「附近有什么好吃的」——`吃的` 是类目键、类目是**照他说的**
        # 取的，搜「美食」不是猜。
        # ⚠ haystack **刻意不含 category 槽**：那是 planner 填的，不是用户说的话。
        # 把它算进去，「附近有没有卖锟斤拷的店」（planner 填 category=餐饮）就永远
        # 判不成「猜的」——而这恰恰是本信号存在的那一类。问的是「用户自己说了哪个类目」。
        hay = f"{raw} {kw_slot}"
        category_guessed = not any(key in hay for key in _CATEGORY_KEYWORD)
        term_discarded = bool(kw_slot or (intent.slots.get("cuisine") or "").strip())
        if ((term_discarded or category_guessed)
                and keyword == _CATEGORY_KEYWORD.get(category)
                and not brand):
            extra_data["_fallback"] = True
        card = attach({"type": "place_list", "category": category, "keyword": label,
                       "items": items, "display_priority": 1}, self.place)
        # center 来源随数据落盘（观测/下游可辨）：slot=用户指定位置 / vehicle=车辆
        # 位置 / none=指名门店按名检索。none 时话术不得出现「附近/为您找到」的
        # 就近暗示——按名找到就说按名找到。
        center_src = ("slot" if (intent.slots.get("location") or "").strip()
                      else "vehicle" if near is not None else "none")
        if center_src == "none":
            speech = f"按名称找到 {len(results)} 家{label}，最匹配的是：{names}{extra_s}。"
        else:
            speech = (f"{lead}为您找到 {len(results)} 家{label}{pref_note}，"
                      f"推荐：{names}{extra_s}。")
        return AgentResult(
            speech=speech,
            ui_card=card,
            # items 供编排 slot_refs + HMI「第N个」handoff；center 供下游判断就近语义
            data={"items": items, "center": center_src, **extra_data},
            follow_up="说『看第 1 个详情』或『导航去第 2 个』",
        )

    @staticmethod
    def _window_lead(window: dict, notes: list[str]) -> str:
        """用餐窗话术（E1）。`buffer_min` 是**明说的假设**，必须念出来——
        我们没有事件地点、算不出真实路程，把假设说清楚才不算编数据。"""
        ev = fmt_clock(window["event_ts"])
        word = window.get("event_word") or "安排"
        if window["tight"]:
            return (f"{ev}的{word}——现在再吃饭时间不太够了"
                    f"（还得留{window['buffer_min']}分钟路上时间），"
                    "要不先过去，路上找点快的？")
        tail = ("；" + "；".join(notes)) if notes else ""
        return (f"{ev}的{word}——建议{fmt_clock(window['seat_ts'])}入座、"
                f"{fmt_clock(window['leave_ts'])}前吃完，"
                f"预留{window['buffer_min']}分钟路上时间{tail}。")

    async def _search_indoor(self, intent, ctx, meta, weather: str) -> AgentResult:
        """室内组合推荐（雨雪/高温等恶劣天气的「去哪玩」）：按室内组类目逐个检索再交错
        合并，商场/电影院/博物馆都露脸——比单类目更接近人对「室内去处」的期待。
        话术必须承接天气前提：badcase 三连的根源之一是回答与「下雨」这个语境完全脱节。"""
        near = await self._resolve_center(intent, meta)
        if near is None:
            # 同主路径的诚实降级：没有位置，「附近的室内去处」无从谈起。
            return AgentResult(
                speech="我现在拿不到车辆的位置，没法推荐附近的室内去处。"
                       "可以检查一下定位开关，或者告诉我一个参照位置。",
                follow_up="说一个参照位置我就能继续找。",
                data={"items": [], "center": "none"})
        rating_min = _to_float(intent.slots.get("rating_min"))
        groups: list[list[Place]] = []
        for kw in _INDOOR_FANOUT:      # 串行：高德免费档 QPS 紧，并发会 CUQPS 超限
            try:
                rs = await self.place.search(kw, category=_INDOOR_SENTINEL, near=near,
                                             rating_min=rating_min, sort="rating",
                                             limit=4, meta=meta)
            except ProviderError as e:
                logger.warning("indoor fanout %s failed（继续其余类目）: %s", kw, e)
                rs = []
            if rs:
                groups.append(rs)
        if not groups:
            # 与主路径同款诚实降级：真实源全挂不改供假 POI
            return AgentResult(speech="周边搜索服务暂时不可用，稍后再试一次？")
        # 交错合并 + 去重：每类先出评分最高的，保证类型多样性
        seen: set[str] = set()
        merged: list[Place] = []
        for tier in range(max(len(g) for g in groups)):
            for g in groups:
                if tier < len(g) and g[tier].id not in seen:
                    seen.add(g[tier].id)
                    merged.append(g[tier])
        merged = merged[:9]
        items = [self._item(p) for p in merged]
        names = "、".join(p.name for p in merged[:3])
        top = max(merged, key=lambda p: p.rating or 0)
        extra = f"，{top.name}评分{top.rating}" if top.rating else ""
        word = _weather_word(weather)
        lead = (f"{word}不太适合户外，" if word
                else "这种天气更适合室内活动，" if weather else "")
        card = attach({"type": "place_list", "category": _INDOOR_SENTINEL,
                       "keyword": "室内好去处", "items": items,
                       "display_priority": 1}, self.place)
        return AgentResult(
            speech=f"{lead}推荐附近这些室内去处：{names}{extra}。",
            ui_card=card,
            data={"items": items},
            follow_up="说『看第 1 个详情』或『导航去第 2 个』",
        )

    # ── G6（EVA 二轮）口味偏好的确定性消费面 ─────────────────────────────
    # 修「假个性化」：此前偏好在 place.search **之后**才召回、只拼进话术
    # 「（已参考您口味：…）」，结果集一条没变。现在检索前生效：泛餐饮查询按喜好
    # 菜系偏置检索词、负偏好对结果软降权，话术只报**真实生效**的项。
    _CUISINE_WORDS = ("粤菜", "川菜", "湘菜", "火锅", "日料", "日本菜", "韩国料理",
                      "西餐", "江浙菜", "本帮菜", "东北菜", "新疆菜", "烧烤", "素食",
                      "面馆", "早茶", "茶餐厅")
    _SPICY_MARKS = ("川菜", "湘菜", "火锅", "串串", "麻辣烫", "冒菜", "麻辣")
    # 忌辣说法：**原话与记忆文本共用**。首版只认「不…吃/沾辣」，真栈实测
    # 「不要太辣」根本不匹配——于是用户当轮明说的忌口连识别都没识别到，
    # 记忆里的川菜偏好照样把检索词改成「川菜」（假个性化的第三种形态）。
    # ⚠「特**别辣**」会被裸「别」吃掉（写完立刻被负例抓住）——加 lookbehind。
    _NO_SPICY_RE = re.compile(
        r"(?:不|(?<!特)别|少|忌|怕)(?:能|要|想|太|吃|沾|放|加|了)*辣|清淡")
    _NEG_TASTE_RE = re.compile(r"不喜欢|不吃|不爱|讨厌|难吃|太[酸咸甜油腻辣]")
    # 话里点名家人 → 并取该家人的口味记忆（subject 维度）。词表与 memory/relation.py
    # 的亲属同义表同源——那边是权威登记，这里只做消费侧识别（跨服务不共享代码）。
    _KIN_SUBJECTS = {
        "老婆": ("老婆", "妻子", "太太", "媳妇"), "老公": ("老公", "丈夫"),
        "爸爸": ("爸爸", "老爸", "父亲"), "妈妈": ("妈妈", "老妈", "母亲"),
        "女儿": ("女儿", "闺女"), "儿子": ("儿子",), "孩子": ("孩子", "小孩", "娃"),
    }
    _KIN_PAIR = {"爸妈": ("爸爸", "妈妈"), "父母": ("爸爸", "妈妈")}

    @classmethod
    def _person_subjects(cls, raw: str) -> list[str]:
        """话里点名的家人 → canonical subject 列表（「和老婆吃饭」→ ["老婆"]）。"""
        subs: list[str] = []
        t = raw or ""
        for word, pair in cls._KIN_PAIR.items():
            if word in t:
                subs.extend(p for p in pair if p not in subs)
        for canon, syns in cls._KIN_SUBJECTS.items():
            if canon not in subs and any(s in t for s in syns):
                subs.append(canon)
        return subs[:2]

    async def _recall_taste(self, ctx, subject: str = "") -> list[dict]:
        """口味记忆召回：**scope 一路 + 谓词前缀一路的并集**（E5）。

        此前只有「scope=profile.taste **且** predicate 前缀 taste.」这一路，而
        `pg_store._score` 里两者是 AND——真栈库里 `place.avoid`「以后不要推荐三立方」、
        `poi.dislike`、`restaurant.no_queue`「老婆不喜欢排队」scope 全是 profile.taste，
        谓词却不带前缀，于是**永远召不回来**（P5 降权能兑现只因恰好另有一条
        taste.coffee 也带了店名）。谓词与 scope 都由 LLM 写、都会漂移，
        任何一路单独当门禁都会重演这一幕——与 P4「三类并集」同款判据。
        top_k=5（P5 从 3 放宽）：店铺级差评条目会被更早的泛化条目挤出 top-3。
        """
        out: list[dict] = []
        for kw in ({"scopes": ["profile.taste"]}, {"predicate_prefix": "taste."}):
            try:
                out += await ctx.recall("口味偏好", top_k=5, subject=subject, **kw)
            except Exception:
                continue
        return out

    async def _taste_profile(self, ctx, raw: str) -> dict | None:
        """召回口味记忆（本人 + 话里点名家人的 subject 分区）→ 结构化信号。失败不挡主流程。"""
        mems: list[dict] = await self._recall_taste(ctx)
        for subj in self._person_subjects(raw):
            mems += await self._recall_taste(ctx, subject=subj)
        if not mems:
            return None
        like, dislikes, no_spicy, no_queue = "", [], False, False
        seen: set[str] = set()
        for m in mems:
            text = str(m.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            negative = (str(m.get("polarity") or "") == "dislike"
                        or bool(self._NEG_TASTE_RE.search(text)))
            if self._NO_SPICY_RE.search(text):
                no_spicy = True
            # E2：「不排队」记下来但**不假装能筛**——地图没有实时排队数据。
            if _NO_QUEUE_RE.search(text):
                no_queue = True
            hit = next((c for c in self._CUISINE_WORDS if c in text), "")
            if hit and not negative and not like:
                like = hit
            if negative:
                dislikes.append(text)
        return {"like_cuisine": like, "dislikes": dislikes, "no_spicy": no_spicy,
                "no_queue": no_queue}

    def _taste_rerank(self, results: list, taste: dict) -> tuple[list, bool]:
        """负偏好软降权：忌辣命中重辣菜系 / 店名级差评（「这家太酸」）→ 整体后移。
        不删除、组内稳定序——负偏好是排序信号不是过滤器（记错了也只是排后）。"""
        def demoted(p) -> bool:
            hay = f"{p.name or ''} {p.category or ''} {p.tags or ''}"
            if taste["no_spicy"] and any(w in hay for w in self._SPICY_MARKS):
                return True
            head = (p.name or "").split("(")[0].split("（")[0].strip()
            return bool(head and len(head) >= 2
                        and any(head in d for d in taste["dislikes"]))
        front = [p for p in results if not demoted(p)]
        back = [p for p in results if demoted(p)]
        return (front + back), bool(front and back)

    # ── E2（G5 余项）无障碍/停车便利的确定性消费面 ───────────────────────
    @classmethod
    def _kin_words(cls, raw: str) -> set[str]:
        """话里点名的人 → 用来**比对记忆文本**的词集合（含同义词）。

        真栈抓到的坑：只判断「话里有没有提到人」，「和老婆吃饭」就会命中
        「父母腿脚不便」那条记忆——系统声称考虑了一件根本不适用的事，
        与 §5① 那条「假个性化」是同一种错，只是换了个维度。
        """
        words: set[str] = set()
        t = raw or ""
        for word, pair in cls._KIN_PAIR.items():          # 爸妈 / 父母
            if word in t:
                words.add(word)
                for canon in pair:
                    words.update(cls._KIN_SUBJECTS.get(canon, ()))
        for syns in cls._KIN_SUBJECTS.values():
            if any(s in t for s in syns):
                words.update(syns)
        if _ELDER_RE.search(t):        # 泛指长辈：父母/祖辈都算同一类
            words |= {"老人", "长辈", "老年", "父母", "爸妈",
                      "爷爷", "奶奶", "外公", "外婆"}
        return words

    async def _mobility_reason(self, ctx, raw: str) -> str:
        """是否要按「行动不便」重排 → 返回话术里的**理由**（空=不触发）。

        两路：① 原话显式；② 话里点名家人/老人时读画像（`profile.person` ∪
        `person.` 谓词，同 E5 的并集判据——scope 与谓词都会漂），且记忆必须
        **确实关于话里那个人**（subject 命中，或文本提到该称谓）。
        不是每次搜索都翻记忆：没提到人就不问。
        """
        if _ACCESS_RE.search(raw or ""):
            return "您提到出行不太方便"
        subjects = self._person_subjects(raw)
        words = self._kin_words(raw)
        if not words:
            return ""
        mems: list[dict] = []
        for kw in ({"scopes": ["profile.person"]}, {"predicate_prefix": "person."}):
            try:
                mems += await ctx.recall("家人 行动 不便", top_k=5, **kw)
            except Exception:
                continue
        for m in mems:
            text = str(m.get("text") or "")
            if not _MOBILITY_MEM_RE.search(text):
                continue
            subj = str(m.get("subject") or "").strip()
            if subj:
                if subj in subjects:
                    return "记得您提到过家人行动不太方便"
                continue                      # 关于别人的，不能拿来说这一位
            if any(w in text for w in words):
                return "记得您提到过家人行动不太方便"
        return ""

    async def _parking_rerank(self, results: list, meta) -> tuple[list, list[dict]]:
        """停车便利度**近似**：前 K 家各查一次周边停车场 → 按 300m 内计数软重排。

        串行（高德免费档 QPS 紧，并发扇出会 CUQPS 超限）；只重排被探测的前 K 家，
        尾部原样保留——探测面有界，重排面就不能超出它。任一次探测失败不挡主流程。
        """
        probe = [p for p in results[:_PARKING_PROBE_K] if p.lat and p.lng]
        stats: list[dict] = []
        for p in probe:
            try:
                lots = await self.place.search(
                    "停车场", near=GeoPoint(lat=p.lat, lng=p.lng), limit=5, meta=meta)
            except ProviderError as e:
                logger.debug("parking probe failed for %s: %s", p.name, e)
                continue
            near_lots = [x for x in lots
                         if 0 < (x.distance_km or 0) <= _PARKING_RADIUS_KM]
            stats.append({"name": p.name, "count": len(near_lots),
                          "nearest_km": round(min((x.distance_km for x in near_lots),
                                                  default=0.0), 2)})
        if not stats:
            return results, []
        rank = {s["name"]: (-s["count"], s["nearest_km"] or 9.9) for s in stats}
        head = sorted(results[:_PARKING_PROBE_K],
                      key=lambda p: rank.get(p.name, (0, 9.9)))
        return head + results[_PARKING_PROBE_K:], stats

    def _taste_caution(self, taste: dict, asked: str) -> str:
        """用户点名要的正是记忆里忌口的（记得不吃辣却找川菜）→ 诚实提一句，不改结果。"""
        if taste["no_spicy"] and any(w in (asked or "") for w in self._SPICY_MARKS):
            return "记得您说过不吃辣，需要的话我可以换清淡些的"
        return ""

    async def _detail(self, intent, ctx, meta) -> AgentResult:
        # HMI「第N个详情」handoff 透传所选项的高德 POI id（meta.nearby_poi_id）→ 精确取详情，
        # 不再按店名重搜（高德对店名可能返回别的分店 → 之前「详情不在列表中」）。
        place_id = ((meta or {}).get("nearby_poi_id") or intent.slots.get("poi_id")
                    or intent.slots.get("id") or "").strip()
        name = (intent.slots.get("name") or intent.slots.get("restaurant_name") or "").strip()
        if name:
            name = _clean_name(name)
        if not place_id and not name:
            return AgentResult(
                status=NEED_SLOT, speech="您想看哪一家的详情？",
                follow_up="说店名，或先搜周边再说『看第 1 个详情』",
                missing_slots=["name"])
        near = self._near(intent, meta)
        try:
            p = await self.place.detail(place_id, name=name, near=near, meta=meta)
        except ProviderError as e:
            logger.warning("place detail failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(  # R9 契约：OK 话术防聚合器吞
                speech=f"暂时拿不到「{name or '该地点'}」的详情，稍后再试一次？")
        return AgentResult(
            speech=self._detail_speech(p),
            ui_card=attach(self._detail_card(p), self.place),
            # 详情不自动导航；lat/lng/tel 供 HMI 卡片「导航」「拨打」按钮 handoff
            data={"place": {"name": p.name, "lat": p.lat, "lng": p.lng, "tel": p.tel}},
        )

    @staticmethod
    def _detail_card(p: Place) -> dict:
        return {"type": "place_detail", "id": p.id, "name": p.name, "category": p.category,
                "address": p.address, "lat": p.lat, "lng": p.lng, "rating": p.rating,
                "cost": p.cost, "tel": p.tel, "open_today": p.open_today,
                "open_week": p.open_week, "tags": p.tags, "photos": p.photos,
                "display_priority": 1}

    @staticmethod
    def _detail_speech(p: Place) -> str:
        parts = [p.name]
        if p.rating:
            parts.append(f"评分{p.rating}")
        if p.cost:
            parts.append(f"人均{_cost_display(p.cost)}")
        if p.open_today:
            parts.append(f"今日营业{p.open_today}")
        elif p.open_week:
            parts.append(f"营业时间{p.open_week}")
        s = "，".join(parts) + "。"
        if p.tel:
            s += f"电话 {p.tel}。"
        if p.tags:
            s += f"特色：{p.tags}。"
        if p.address:
            s += f"地址：{p.address}。"
        return s

    async def _order(self, intent, ctx, meta) -> AgentResult:
        name = (intent.slots.get("name") or intent.slots.get("restaurant_name")
                or intent.slots.get("poi_id") or "").strip()
        if not name:
            return AgentResult(
                status=NEED_SLOT, speech="您想在哪一家点单或订位？",
                follow_up="先搜周边选一家，再说『在这家点单』",
                missing_slots=["name"])
        # 已二次确认：诚实——在线点单/订位尚未接入真实商户，不假装下单，给电话+导航兜底。
        if meta.get("confirmed") == "true":
            card = None
            try:
                p = await self.place.detail("", name=name, near=self._near(intent, meta), meta=meta)
                card = self._detail_card(p)
            except Exception as e:  # best-effort 调详情，失败仍诚实回应
                logger.debug("order detail lookup failed: %s", e)
            return AgentResult(
                speech=f"「{name}」的在线点单/订位还在接入中（目前仅麦当劳、瑞幸等少数连锁支持）；"
                       f"已为您调出商家信息，可直接拨打电话或导航前往。",
                ui_card=card, follow_up="说『导航过去』", data={"name": name})
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"确认为您在「{name}」发起点单/订位吗？",
            follow_up="说『确认』即可",
        ).action("nearby.order", {"name": name}, require_confirm=True)

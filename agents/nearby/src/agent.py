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

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from .providers import build_place_provider
from .providers.base import GeoPoint, Place

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
_FOOD_CATS = {"餐饮", "美食", "吃饭", "餐厅", "吃的"}
# G5：原话里的饮食信号——只有它在场时才允许落「餐饮」默认类目。
# 「看看动物的地方」落「美食」是给出错误结果，比失败更糟。
_FOOD_HINT_RE = re.compile(
    r"吃|喝|饭|餐|饿|菜|夜宵|早茶|外卖|美食|小吃|火锅|烧烤|甜品|日料|自助")
# G5：氛围属性词（「安静点的地方喝咖啡」）。高德没有安静度字段——能做的是
# 环境类标签+评分的软重排，并在话术里如实说数据边界（不假装有安静度）。
_AMBIENCE_RE = re.compile(r"安静|清静|清净|环境好|氛围好|不吵|幽静")
_AMBIENCE_TAGS = ("环境", "安静", "书", "清吧", "花园", "庭院", "湖景", "江景")


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


class NearbyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.place = build_place_provider()

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

    @staticmethod
    def _near(intent, meta) -> GeoPoint | None:
        """搜索中心：显式 location 槽位（坐标或地名）优先，否则本轮已授权 GPS。
        无任何位置 → None（provider 走关键字检索，不拿任意城市冒充「附近」）。"""
        loc = (intent.slots.get("location") or "").strip()
        if loc:
            parts = loc.split(",")
            if len(parts) == 2:
                try:
                    return GeoPoint(lng=float(parts[0]), lat=float(parts[1]))
                except ValueError:
                    pass
            return GeoPoint(address=loc)
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
            return brand
        if cuisine:
            return cuisine
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
            return cleaned
        # Preserve an explicit unknown/coarse category as the provider query;
        # silently rewriting it to food is a semantic corruption.  Only a
        # genuinely absent category keeps the legacy nearby-food default.
        return cat_kw or _strip_qualifiers(category) or "美食"

    @staticmethod
    def _item(p: Place) -> dict:
        # lat/lng 供 HMI「导航去第N个」handoff（同 navigation poi_list 形状）；
        # city 供商户官方检索（麦当劳 searchType=2 按位置搜时城市必填）
        return {"id": p.id, "name": p.name, "category": p.category,
                "rating": p.rating, "cost": p.cost, "distance_km": p.distance_km,
                "address": p.address, "city": p.city, "tags": p.tags,
                "open_today": p.open_today, "lat": p.lat, "lng": p.lng}

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
        if category in _FOOD_CATS:
            taste = await self._taste_profile(ctx, raw)
            if (taste and taste["like_cuisine"]
                    and not (cuisine or brand or kw_slot.strip())):
                keyword = taste["like_cuisine"]
                taste_notes.append(f"按您的口味优先{taste['like_cuisine']}")
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
        label = cuisine or brand or keyword

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

        # G5：氛围属性软重排（「安静点的地方」）。高德没有安静度字段——按环境类
        # 标签 + 评分排前（稳定序），话术如实说数据边界，不假装有安静度。
        if _AMBIENCE_RE.search(raw) and results:
            results = sorted(results, key=lambda p: (
                0 if any(t in (p.tags or "") for t in _AMBIENCE_TAGS) else 1,
                -(p.rating or 0)))
            taste_notes.append("您要安静些的——地图没有安静度数据，已按环境类标签和评分优先")
        # G6：负偏好软降权（忌辣/店名级差评 → 结果后移），话术只报**真实生效**的项
        # ——此前这里是搜完才召回、只拼「已参考您口味」进话术，结果集一条没变（假个性化）。
        if taste:
            results, moved = self._taste_rerank(results, taste)
            if moved:
                taste_notes.append("不合口味的已排后")
            caution = self._taste_caution(taste, cuisine or keyword)
            if caution:
                taste_notes.append(caution)
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
            data={"items": items, "center": center_src},
            follow_up="说『看第 1 个详情』或『导航去第 2 个』",
        )

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
    _NO_SPICY_RE = re.compile(r"不(?:能|要|太)?(?:吃|沾)辣|怕辣|忌辣|别太辣|清淡")
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

    async def _taste_profile(self, ctx, raw: str) -> dict | None:
        """召回口味记忆（本人 + 话里点名家人的 subject 分区）→ 结构化信号。失败不挡主流程。"""
        mems: list[dict] = []
        try:
            mems += await ctx.recall("口味偏好", scopes=["profile.taste"],
                                     predicate_prefix="taste.", top_k=3)
            for subj in self._person_subjects(raw):
                mems += await ctx.recall("口味偏好", scopes=["profile.taste"],
                                         predicate_prefix="taste.", top_k=3,
                                         subject=subj)
        except Exception:
            pass
        if not mems:
            return None
        like, dislikes, no_spicy = "", [], False
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
            hit = next((c for c in self._CUISINE_WORDS if c in text), "")
            if hit and not negative and not like:
                like = hit
            if negative:
                dislikes.append(text)
        return {"like_cuisine": like, "dislikes": dislikes, "no_spicy": no_spicy}

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

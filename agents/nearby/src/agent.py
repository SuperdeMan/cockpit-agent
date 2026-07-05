"""周边发现 Agent —— 基于高德 POI 2.0 的富数据周边搜索 + 详情增强。

发现归本 Agent、出行归 navigation（见 docs/design/2026-07-05-nearby-discovery-redesign.md）：
本 Agent 只做「找 + 看详情」，导航由 HMI 卡片按钮 handoff 给 navigate 链路；nearby.order 为诚实预留桩。
Provider 适配层（mock/amap 经 env 切换）；真实失败降级 mock，不击穿主链。
"""
from __future__ import annotations
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from .providers import build_place_provider
from .providers.base import GeoPoint, Place
from .providers.mock import MockPlaceProvider

logger = logging.getLogger("agent.nearby")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 类目 → 高德主检索词（关键词优先、稳健；types 精确化留 P1）
_CATEGORY_KEYWORD = {
    "餐饮": "美食", "美食": "美食", "吃饭": "美食", "餐厅": "美食", "吃的": "美食",
    "酒店": "酒店", "住宿": "酒店", "宾馆": "酒店", "民宿": "民宿",
    "景点": "景点", "景区": "景点", "旅游": "景点",
    "影院": "电影院", "电影院": "电影院", "电影": "电影院",
    "停车": "停车场", "停车场": "停车场", "车位": "停车场",
    "充电": "充电站", "充电站": "充电站", "充电桩": "充电站",
    "加油": "加油站", "加油站": "加油站",
    "超市": "超市", "便利店": "便利店", "咖啡": "咖啡厅", "奶茶": "奶茶饮品",
    "药店": "药店", "银行": "银行", "医院": "医院",
}
# 餐饮类目（口味画像仅此类生效）
_FOOD_CATS = {"餐饮", "美食", "吃饭", "餐厅", "吃的"}


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
_PROXIMITY_RE = re.compile(
    r"附近的?|周边的?|就近的?|最近的?|旁边的?|这边的?|一带的?|附近有|"
    r"哪儿?有|哪里有|有没有|有什么|有啥|找个?|找家|找点|来个?|来点|"
    r"推荐个?|推荐家|推荐点|想吃个?|想去个?|什么|好吃的?|好玩的?")


def _strip_proximity(text: str) -> str:
    """剥掉「附近/周边/哪有/找个/有什么好吃的」等发现措辞，留核心类目词。"""
    return _PROXIMITY_RE.sub("", text or "").strip(" 的，。、")


_CN_NUM = {"十": 10, "二十": 20, "三十": 30, "四十": 40, "五十": 50, "六十": 60,
           "七十": 70, "八十": 80, "九十": 90, "一百": 100, "两百": 200, "二百": 200,
           "三百": 300, "四百": 400, "五百": 500, "百": 100}


def _cn_to_int(s: str) -> int:
    return int(s) if s.isdigit() else _CN_NUM.get(s, 0)


_PRICE_RE = re.compile(
    r"(?:人均|均价|价位|预算|不超过|不过)?\s*([0-9]{2,4}|[一二两三四五六七八九十百]+)\s*"
    r"(?:元|块钱|块)?\s*(以内|以下|之内|左右|上下|封顶)")


def _parse_price(text: str) -> float:
    """从原话解析人均上限：『一百以内』→100、『80块以下』→80、『150左右』→约195。解析不出返回 0。"""
    m = _PRICE_RE.search(text or "")
    if not m:
        return 0.0
    n = _cn_to_int(m.group(1))
    if n <= 0:
        return 0.0
    return float(round(n * 1.3)) if m.group(2) in ("左右", "上下") else float(n)


_SORT_RATING_RE = re.compile(r"评分高|高分|口碑好?|好评|人气高?|评价高|最好的")


def _parse_sort(text: str) -> str:
    return "rating" if _SORT_RATING_RE.search(text or "") else ""


class NearbyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.place = build_place_provider()
        self._fallback = MockPlaceProvider()  # 真实 provider 抖动时降级兜底

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
        hay = raw or ((intent.raw_text or "") + " " + (intent.slots.get("keyword") or ""))
        for key in _CATEGORY_KEYWORD:
            if key in hay:
                return key
        return raw or "餐饮"

    @staticmethod
    def _build_keyword(category, cuisine, brand, kw_slot) -> str:
        """高德检索词：品牌/菜系优先；否则剥掉发现措辞后的关键词（『附近的火锅』→火锅）；再退类目词。"""
        if brand:
            return brand
        if cuisine:
            return cuisine
        cleaned = _strip_proximity(kw_slot)
        if cleaned and cleaned not in ("地点", "的"):
            return cleaned
        return _CATEGORY_KEYWORD.get(category, category or "美食")

    @staticmethod
    def _item(p: Place) -> dict:
        # lat/lng 供 HMI「导航去第N个」handoff（同 navigation poi_list 形状）
        return {"id": p.id, "name": p.name, "category": p.category,
                "rating": p.rating, "cost": p.cost, "distance_km": p.distance_km,
                "address": p.address, "tags": p.tags, "open_today": p.open_today,
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
        cuisine = (intent.slots.get("cuisine") or "").strip()
        brand = (intent.slots.get("brand") or "").strip()
        kw_slot = (intent.slots.get("keyword") or "").strip()
        keyword = self._build_keyword(category, cuisine, brand, kw_slot)
        raw = intent.raw_text or ""
        rating_min = _to_float(intent.slots.get("rating_min"))
        # 价位/排序：弱 LLM 常漏填槽位 → 从原话兜底解析（『一百以内』『评分高』）
        price_max = _to_float(intent.slots.get("price_max")) or _parse_price(raw + " " + kw_slot)
        sort = (intent.slots.get("sort") or "").strip() or _parse_sort(raw)
        near = self._near(intent, meta)

        try:
            results = await self.place.search(
                keyword, category=category, near=near, rating_min=rating_min,
                price_max=price_max, brand=brand, sort=sort, meta=meta)
        except ProviderError as e:
            logger.warning("place search failed, fallback to mock: %s", e)
            results = await self._fallback.search(
                keyword, category=category, near=near, rating_min=rating_min,
                price_max=price_max, brand=brand, sort=sort, meta=meta)

        label = cuisine or brand or keyword
        if not results:
            return AgentResult(
                speech=f"附近暂时没找到{label}，换个说法或扩大范围再试试？",
                follow_up="可以说『附近的火锅』或『评分高的川菜馆』")

        # 口味画像（仅餐饮）：学到的偏好（如「不吃辣」）体现在话术
        pref_note = ""
        if category in _FOOD_CATS:
            taste = await self._taste_prefs(ctx)
            if taste:
                pref_note = f"（已参考您口味：{taste}）"

        items = [self._item(p) for p in results]
        names = "、".join(p.name for p in results[:3])
        extra = self._known_attrs(results[0])
        extra_s = f"，{results[0].name}{extra}" if extra else ""
        card = {"type": "place_list", "category": category, "keyword": label,
                "items": items, "display_priority": 1}
        return AgentResult(
            speech=f"为您找到 {len(results)} 家{label}{pref_note}，推荐：{names}{extra_s}。",
            ui_card=card,
            data={"items": items},   # 供编排 slot_refs + HMI「第N个」handoff
            follow_up="说『看第 1 个详情』或『导航去第 2 个』",
        )

    async def _taste_prefs(self, ctx) -> str:
        """口味偏好：语义记忆召回（学到的，如「不吃辣」）。精确读取走 predicate_prefix；失败不挡主流程。"""
        try:
            mems = await ctx.recall("口味偏好", scopes=["profile.taste"],
                                    predicate_prefix="taste.", top_k=3)
        except Exception:
            mems = []
        return "、".join(m.get("text", "") for m in mems if m.get("text"))[:60]

    async def _detail(self, intent, ctx, meta) -> AgentResult:
        place_id = (intent.slots.get("poi_id") or intent.slots.get("id") or "").strip()
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
            logger.warning("place detail failed, fallback to mock: %s", e)
            p = await self._fallback.detail(place_id, name=name, near=near, meta=meta)
        return AgentResult(
            speech=self._detail_speech(p),
            ui_card=self._detail_card(p),
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

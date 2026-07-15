"""导航 Agent —— 所有 Agent 的参考范本。

演示：意图分发、缺槽位追问(NEED_SLOT)、按引用取上下文(ctx.fetch)、
产出动作(action) 与 HMI 卡片(ui_card)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import json
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.landmark import (
    is_landmark_description, landmark_candidates, name_matches)
from .providers import build_poi_provider
from .providers.base import GeoPoint, POI
from .providers.mock import MockPOIProvider

logger = logging.getLogger("agent.navigation")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 常用地点别名 → (画像 key, 中文标签)。精确匹配整段目的地，避免误伤含字地名。
_PLACE_ALIASES: dict[str, tuple[list[str], str]] = {
    "home": (["家", "我家", "回家", "家里"], "家"),
    "company": (["公司", "单位", "我公司", "我单位"], "公司"),
    "school": (["学校", "我的学校"], "学校"),
}


def _match_place_alias(text: str) -> tuple[str | None, str]:
    """目的地是否是常用地点别名。精确匹配 → (key, 标签)，否则 (None, '')。"""
    t = (text or "").strip().rstrip("。，,. ")
    for key, (aliases, label) in _PLACE_ALIASES.items():
        if t in aliases:
            return key, label
    return None, ""


# "最近的/附近的X" 这类就近查询依赖当前位置；无定位时不应拿任意城市冒充"最近"。
_PROXIMITY_RE = re.compile(r"最近|附近|周边|就近|离我")
# 剥掉就近前缀，留类目关键词（"附近的粤菜馆"→"粤菜馆"）。否则高德按整句"附近的粤菜馆"
# 找同名 POI 必然落空（"暂时无法确定"），或匹配到远处无关结果。
_PROXIMITY_PREFIX_RE = re.compile(r"^(离我)?\s*(最近|附近|周边|就近)的?\s*")


def _strip_proximity(dest: str) -> str:
    stripped = _PROXIMITY_PREFIX_RE.sub("", dest or "").strip()
    return stripped or dest


class NavigationAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.poi = build_poi_provider()
        self._fallback = MockPOIProvider()  # 真实 provider 抖动时的降级兜底

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "navigation.search_poi": self._search_poi,
            "navigation.navigate_to": self._navigate_to,
            "navigation.reverse_geocode": self._reverse_geocode,
            "navigation.poi_detail": self._poi_detail,
            "navigation.set_place": self._set_place,
            "navigation.locate": self._locate,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个导航请求我还不会处理。")

    async def _current_position(self, ctx, meta) -> GeoPoint | None:
        """当前位置统一只取本轮已授权的浏览器 GPS——与天气、「我在哪」一致，避免三处定位打架。
        PoC 没有真实车机 GPS（memory 的 vehicle.location 是 mock 上海），回退它会给出误导结果
        且与天气不一致；故不再回退，无授权返回 None，由调用方诚实提示开启定位。"""
        current = current_location_from_meta(meta)
        if current:
            return GeoPoint(lat=current.lat, lng=current.lng)
        return None

    @staticmethod
    def _navigate_payload(destination: str, lat: float, lng: float, meta: dict | None) -> dict:
        """构建导航动作；仅携带本轮已授权的精确起点。"""
        payload = {"destination": destination, "lat": lat, "lng": lng}
        current = current_location_from_meta(meta)
        if current:
            payload.update({"origin_lat": current.lat, "origin_lng": current.lng})
        return payload

    async def _search_poi(self, intent, ctx, meta) -> AgentResult:
        keyword = intent.slots.get("keyword") or intent.slots.get("category")
        if not keyword:
            return AgentResult(status=NEED_SLOT, speech="您想找什么类型的地点呢？",
                               follow_up="请提供搜索关键词，如『充电站』『川菜馆』")

        # 按引用取车辆当前位置（隐私最小化：只取需要的 scope）
        near = await self._current_position(ctx, meta)

        rating_min = float(intent.slots.get("rating_min", 0) or 0)
        # 真实 provider 失败（超时/熔断/厂商错误）降级到 mock，保证链路不阻断；
        # 失败本身已由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
        try:
            results = await self.poi.search(keyword, near=near, rating_min=rating_min, meta=meta)
        except ProviderError as e:
            logger.warning("poi search failed, fallback to mock: %s", e)
            results = await self._fallback.search(keyword, near=near, rating_min=rating_min, meta=meta)
        resolved_keyword = keyword

        # 设施类目搜索（充电站/加油站/停车场…）按本步关键词如实搜附近，不得被整句多意图
        # 原文的地标解析劫持，也不自动导航到首个结果——否则多意图“导航去X + 找充电桩”里
        # 找充电桩的子步会被整句改写成导航到 X（双 navigate、卡片串味）。
        raw_text = (intent.raw_text or "").strip()
        is_category = self._is_category_search(keyword, intent.slots.get("category") or "")

        # Planner 有时会把“去深圳笋一样的建筑物”误抽成“笋岗”这类普通关键词。
        # 视觉地标描述即使碰巧命中一个同名普通 POI，也要优先由地图验证语义候选；
        # 候选名已含城市/正式名称，不能受车辆当前城市的周边检索范围限制。
        is_visual_landmark = (not is_category) and self._is_visual_landmark_description(raw_text)
        if raw_text and not is_category and (not results or is_visual_landmark):
            for candidate in await self._landmark_candidates(raw_text):
                try:
                    candidate_results = await self.poi.search(
                        candidate,
                        near=None if is_visual_landmark else near,
                        rating_min=rating_min,
                        meta=meta,
                    )
                except ProviderError as e:
                    logger.warning("semantic POI candidate search failed: %s", e)
                    continue
                # 同 _find_destination：拒绝高德对非官方名返回的邻近无关 POI
                if candidate_results and name_matches(candidate, candidate_results[0].name):
                    resolved_keyword, results = candidate, candidate_results
                    break

        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address,
                  "lat": r.lat, "lng": r.lng} for r in results]
        card = {"type": "poi_list", "keyword": resolved_keyword, "items": items}

        if results and not is_category and self._is_navigation_phrase(raw_text):
            first = results[0]
            return AgentResult(
                speech=f"识别到您说的是{first.name}（{first.address}）。已为您规划路线。",
                ui_card=card, data={"items": items},
            ).action("navigate", self._navigate_payload(first.name, first.lat, first.lng, meta))

        names = "、".join(r.name for r in results[:3])
        return AgentResult(
            speech=f"为您找到 {len(results)} 个{resolved_keyword}，推荐前三个：{names}。需要导航过去吗？",
            ui_card=card,
            data={"items": items},  # F3：结构化结果供编排 slot_refs 取值（如 s1.data.items.0.id）
            follow_up="可以说『导航去第一个』",
        )

    @staticmethod
    def _is_navigation_phrase(text: str) -> bool:
        return (text or "").strip().startswith(("导航", "去", "到", "带我去"))

    # 设施类目关键词：这类搜索按本步关键词如实搜附近，不走整句地标解析、不自动导航
    _CATEGORY_MARKERS = (
        "充电", "快充", "慢充", "超充",
        "加油", "加气", "加氢",
        "停车", "车位",
        "超市", "便利店",
        "厕所", "卫生间", "洗手间", "公厕",
        "服务区", "药店", "医院", "银行", "atm",
    )

    @classmethod
    def _is_visual_landmark_description(cls, text: str) -> bool:
        """带明显视觉/地标描述的导航请求（含动词前缀）→ 语义候选优先。"""
        normalized = (text or "").strip()
        if not cls._is_navigation_phrase(normalized):
            return False
        return is_landmark_description(normalized)

    @classmethod
    def _is_category_search(cls, keyword: str, category_slot: str = "") -> bool:
        """本步是否为设施类目搜索（充电站/加油站/停车场…）。"""
        if (category_slot or "").strip():
            return True
        k = (keyword or "").strip().lower()
        return any(marker in k for marker in cls._CATEGORY_MARKERS)

    # 顺路停靠类目（吃饭/咖啡…）→ 高德搜索关键词
    _STOP_CATEGORY_KEYWORDS = {
        "吃饭": "餐厅", "餐厅": "餐厅", "饭店": "餐厅", "美食": "餐厅", "吃的": "餐厅",
        "咖啡": "咖啡", "奶茶": "奶茶饮品", "加油": "加油站",
        "厕所": "公共厕所", "卫生间": "公共厕所", "超市": "超市", "便利店": "便利店",
    }
    # raw_text 里的途经点兜底解析（planner 未填 waypoint 槽位时）
    _WAYPOINT_RE = re.compile(r"(?:途经|途径|经过|顺路去|顺道去|路过)\s*([^，。,、\s]+)")
    # raw_text 里的"顺路停靠"兜底识别（planner 未填 stop_category，或误拆出 food 步时）
    _STOP_RAW_RE = re.compile(
        r"(?:附近|周边|顺路|顺道|沿途|中途|途中|路上|那边|那儿|路过)[^，。,、]{0,8}?"
        r"(餐厅|饭店|吃饭|吃的|美食|川菜|火锅|咖啡|奶茶|小吃|加油|充电)")

    @classmethod
    def _stop_keyword(cls, category: str) -> str:
        c = (category or "").strip()
        for k, v in cls._STOP_CATEGORY_KEYWORDS.items():
            if k in c:
                return v
        return c

    async def _navigate_to(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "").strip()
        raw_text = (intent.raw_text or "").strip()
        if not dest:
            # 槽位为空时，尝试用 raw_text 做模糊搜索（处理"导航到上海那个像船一样的建筑"）
            raw = raw_text
            for prefix in ("导航到", "导航去", "导航", "带我去", "去", "到"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):].strip()
                    break
            raw = self._WAYPOINT_RE.sub("", raw).strip("，。, 、")  # 去掉"途经X"尾巴，不污染目的地
            if raw:
                dest = raw
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您要去哪里？", follow_up="请告诉我目的地")

        # planner 臆断修正：见 _correct_planner_landmark（把 planner 错猜的具体楼名换回真地标官方名）。
        dest = await self._correct_planner_landmark(dest, raw_text, meta)

        # 常用地点（家/公司/学校）：命中别名先走画像，未设置则二次交互让用户设置。
        place_key, place_label = _match_place_alias(dest)
        if place_key:
            place_address = (intent.slots.get("place_address") or "").strip()
            if place_address:
                # 二次交互续接：用户给了地址 → 设为该常用地点并直接导航过去。
                return await self._set_place_and_go(
                    place_key, place_label, place_address, ctx, meta, navigate=True)
            stored = await self._get_place(ctx, place_key)
            if stored:
                # "导航回家，途中找个咖啡店"：常用地点同样支持途经点/顺路停靠，别丢这层意图。
                stored_poi = POI(
                    id=f"place_{place_key}", name=stored.get("name") or place_label,
                    address=stored.get("address") or "",
                    lat=stored.get("lat"), lng=stored.get("lng"))
                items = [stored]
                waypoint = (intent.slots.get("waypoint") or "").strip()
                if not waypoint:
                    m = self._WAYPOINT_RE.search(raw_text)
                    if m:
                        waypoint = m.group(1).strip()
                if waypoint:
                    return await self._navigate_via_waypoint(
                        stored_poi, place_label, waypoint, items, meta)
                stop_category = (intent.slots.get("stop_category") or "").strip()
                if not stop_category:
                    m = self._STOP_RAW_RE.search(raw_text)
                    if m:
                        stop_category = m.group(1)
                if stop_category:
                    return await self._navigate_with_stop_choice(
                        stored_poi, place_label, stop_category, items, meta)
                return await self._navigate_to_stored(place_label, stored, meta)
            example = "深圳科技园" if place_key == "company" else "上海长宁区某某小区"
            return AgentResult(
                status=NEED_SLOT,
                speech=f"您还没有设置「{place_label}」的位置，请告诉我{place_label}的地址。",
                follow_up=f"比如说『{example}』，我记住后直接带您过去。",
                missing_slots=["place_address"],
            )

        # 带当前位置就近解析目的地（"最近的/附近的粤菜馆"按距离排序）；无定位则 near=None。
        near = await self._current_position(ctx, meta)
        # 就近查询("最近的/附近的X")：X 是【目的地类目】——无定位→诚实提示开启定位（不拿任意城市
        # 冒充"最近"）；有定位→剥掉就近前缀按当前位置周边搜。只看 dest——「东方之门，附近找吃饭」
        # 里的"附近"指目的地周边停靠(顺路用餐流程)，dest 是"东方之门"，不该误触。
        is_proximity = bool(_PROXIMITY_RE.search(dest))
        if is_proximity:
            if near is None:
                return AgentResult(
                    speech="找最近的地点要先知道您在哪。请在设置里开启定位授权，我就按当前位置帮您就近找。",
                    follow_up="开启定位后再说一次『最近的…』")
            dest = _strip_proximity(dest)  # "附近的粤菜馆" → "粤菜馆"，按当前位置周边搜
        # "换一批"翻页：HMI 在续问时带上 meta.poi_page，取下一页不同候选。
        try:
            page = max(1, int((meta or {}).get("poi_page", 1)))
        except (TypeError, ValueError):
            page = 1
        # strict=False：就近类目（「最近的粤菜馆」）的结果店名天然不含类目词，
        # 不做 R1 强校验（否则每次类目导航都白跑一轮去偏置重搜 + 地标 LLM）。
        resolved_name, results = await self._find_destination(
            dest, meta, near=near, limit=5 if is_proximity else 3, page=page,
            strict=not is_proximity)
        if not results:
            if is_proximity:
                if page > 1:  # "换一批"翻到底了
                    return AgentResult(
                        speech=f"附近没有更多{dest}了，从前面给您的几家里挑一个吧。")
                return AgentResult(speech=f"附近暂时没找到{dest}，换个类型试试？")
            return AgentResult(
                status=NEED_SLOT,
                speech=f"暂时无法确定「{dest}」对应的具体地点。",
                follow_up="请补充城市、所在区域，或附近的地标，我再为您定位。",
                missing_slots=["destination"],
            )

        first = results[0]
        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address,
                  "lat": r.lat, "lng": r.lng} for r in results]

        # 类目目的地（"最近的/附近的粤菜馆"）：附近这几家是【可选目的地】，不是顺路途经点。
        # 列出来让用户选哪家作目的地（plain poi_list，无 purpose → HMI「第N个」改写成
        # 「导航去{名称}」直接设为目的地），不走顺路停靠/途经点流程（那是"导航去X，途中找Y"语义）。
        if is_proximity:
            names = "、".join(r.name for r in results[:3])
            more = f" 等{len(results)}家" if len(results) > 3 else ""
            return AgentResult(
                speech=f"附近为您找到这些{dest}：{names}{more}。想去哪一家？说『第几个』即可。",
                ui_card={"type": "poi_list", "keyword": dest, "items": items},
                data={"items": items},
                follow_up="说『第一个』『第二个』选择目的地",
            )

        # 轮2：已选途经点（slot 或 raw_text 的"途经X"）→ 解析坐标并入 navigate
        waypoint = (intent.slots.get("waypoint") or "").strip()
        if not waypoint:
            m = self._WAYPOINT_RE.search(raw_text)
            if m:
                waypoint = m.group(1).strip()
        if waypoint:
            return await self._navigate_via_waypoint(first, resolved_name, waypoint, items, meta)

        # 轮1：顺路停靠类目（吃饭/咖啡…）→ 导航到目的地 + 给候选让用户二次选择。
        # planner 未填 stop_category 槽位时，从 raw_text"附近/顺路…餐厅/吃饭"兜底识别——
        # 即便 planner 误把找餐厅拆成 nearby.search，导航侧也能自己产出真实餐厅
        # 途经点候选（聚合器优先 waypoint_choice 卡）。
        stop_category = (intent.slots.get("stop_category") or "").strip()
        if not stop_category:
            m = self._STOP_RAW_RE.search(raw_text)
            if m:
                stop_category = m.group(1)
        if stop_category:
            return await self._navigate_with_stop_choice(
                first, resolved_name, stop_category, items, meta)

        # 普通导航：出路线规划卡（当前位置 → 目的地，起终点 + best-effort 距离/时长）
        prefix = (f"识别到您说的是{first.name}。" if resolved_name != dest else "")
        return await self._route_plan_to(
            first.name, first.address, first.lat, first.lng, meta, resolved_prefix=prefix)

    # ── 常用地点（家/公司/学校）──────────────────────────────
    async def _get_places(self, ctx) -> dict:
        """从用户画像读常用地点 map。失败/未设置返回空 dict。"""
        try:
            vals = await ctx.fetch("profile.places")
        except Exception as e:  # 画像不可用不应阻断导航
            logger.warning("fetch profile.places failed: %s", e)
            return {}
        raw = vals.get("profile.places")
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _get_place(self, ctx, place_key: str) -> dict | None:
        place = (await self._get_places(ctx)).get(place_key)
        return place if isinstance(place, dict) and place.get("lat") is not None else None

    async def _navigate_to_stored(self, label: str, stored: dict, meta) -> AgentResult:
        """已设置的常用地点 → 直接导航（出路线规划卡 起点→终点）。"""
        name = stored.get("name") or label
        addr = stored.get("address") or name
        return await self._route_plan_to(
            name, addr, stored.get("lat"), stored.get("lng"), meta,
            resolved_prefix=f"正在前往{label}：")

    async def _set_place_and_go(self, place_key: str, label: str, address: str,
                                ctx, meta, navigate: bool) -> AgentResult:
        """地理编码地址 → 存为常用地点（best-effort）→ 按需导航。"""
        near = await self._current_position(ctx, meta)
        _resolved, results = await self._find_destination(address, meta, near=near)
        if not results:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"没找到「{address}」，请补充城市/区域或换个说法。",
                follow_up=f"再说一次{label}的地址即可",
                missing_slots=["place_address" if navigate else "address"],
            )
        first = results[0]
        record = {"name": first.name, "address": first.address,
                  "lat": first.lat, "lng": first.lng}
        places = await self._get_places(ctx)
        places[place_key] = record
        try:
            await ctx.save_profile("places", places)  # 存画像失败不挡导航
        except Exception as e:
            logger.warning("save_profile places failed: %s", e)
        if navigate:
            return AgentResult(
                speech=f"已把{label}设为{first.name}，正在为您导航过去。",
                ui_card={"type": "poi_list", "keyword": label, "items": [record]},
                data={"place": label, "item": record},
            ).action("navigate", self._navigate_payload(
                first.name, first.lat, first.lng, meta))
        return AgentResult(
            speech=f"已把{label}设为{first.name}（{first.address}）。"
                   f"以后说『导航去{label}』就能直接出发。",
            data={"place": label, "item": record},
        )

    async def _set_place(self, intent, ctx, meta) -> AgentResult:
        """显式设置常用地点：『把家设成XX』『我家在XX』『设置公司地址为XX』。不导航。"""
        place_key, label = _match_place_alias(intent.slots.get("place", ""))
        address = (intent.slots.get("address") or "").strip()
        if not place_key or not address:
            pk, lb, addr = self._parse_set_place(getattr(intent, "raw_text", "") or "")
            place_key, label = (place_key or pk), (label or lb)
            address = address or addr
        if not place_key:
            return AgentResult(
                status=NEED_SLOT, speech="您想设置哪个常用地点？比如家或公司。",
                follow_up="可以说『把家设成XX地址』")
        if not address:
            return AgentResult(
                status=NEED_SLOT, speech=f"请告诉我{label}的具体地址。",
                missing_slots=["address"])
        return await self._set_place_and_go(
            place_key, label, address, ctx, meta, navigate=False)

    @staticmethod
    def _parse_set_place(raw: str) -> tuple[str | None, str, str]:
        """从原话兜底解析『把X设成Y/X在Y/X地址是Y』。返回 (key, 标签, 地址)。"""
        t = (raw or "").strip()
        m = re.search(
            r"(?:把|将)?\s*(家|我家|公司|单位|学校)(?:的)?(?:位置|地址)?\s*"
            r"(?:设成|设为|设置成|设置为|改成|改为|定为|定在)\s*(.+)", t)
        if not m:
            m = re.search(
                r"(我家|家|公司|单位|学校)(?:的)?(?:位置|地址)?\s*(?:在|是|为)\s*(.+)", t)
        if m:
            pk, lb = _match_place_alias(m.group(1))
            return pk, lb, m.group(2).strip(" 。，,.")
        return None, "", ""

    async def _navigate_with_stop_choice(self, dest_poi, resolved_name, stop_category,
                                         items, meta) -> AgentResult:
        """轮1：导航到目的地，并给"顺路停靠"类目候选让用户二次选择途经点（不自动选）。"""
        keyword = self._stop_keyword(stop_category)
        near = GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng)
        try:
            stops = await self.poi.search(keyword, near=near, limit=5, meta=meta)
        except ProviderError as e:
            logger.warning("stop category search failed: %s", e)
            stops = []
        payload = self._navigate_payload(dest_poi.name, dest_poi.lat, dest_poi.lng, meta)
        if not stops:
            return AgentResult(
                speech=f"已为您导航到{dest_poi.name}；附近暂未找到{keyword}。",
                ui_card={"type": "poi_list", "keyword": resolved_name, "items": items},
                data={"items": items},
            ).action("navigate", payload)
        names = "、".join(s.name for s in stops[:3])
        choice_items = [{"id": s.id, "name": s.name, "rating": s.rating,
                         "distance_km": s.distance_km, "address": s.address,
                         "lat": s.lat, "lng": s.lng} for s in stops]
        # purpose=waypoint_choice 让 HMI 把"第N个"回填为途经点（派发"导航去X途经Y"），而非发起新导航
        return AgentResult(
            speech=f"已为您规划到{dest_poi.name}的路线。顺路的{keyword}有：{names}，"
                   f"想顺道去哪家？说『第几个』即可，不去也可以直接出发。",
            ui_card={"type": "poi_list", "purpose": "waypoint_choice",
                     "display_priority": 1,
                     "title": f"顺路{keyword} · 选择途经点",
                     "destination": dest_poi.name, "items": choice_items},
            data={"destination": dest_poi.name, "stops": choice_items},
        ).action("navigate", payload)

    @staticmethod
    def _fmt_dur(minutes) -> str:
        m = int(minutes or 0)
        if m <= 0:
            return ""
        h, mm = divmod(m, 60)
        return (f"{h}小时" if h else "") + (f"{mm}分钟" if mm else "")

    async def _navigate_via_waypoint(self, dest_poi, resolved_name, waypoint,
                                     items, meta) -> AgentResult:
        """轮2：所选停靠点 near 目的地解析坐标→并入途经点，并出路线规划卡（出发地→途经点→目的地）。"""
        near = GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng)
        try:
            wp_results = await self.poi.search(waypoint, near=near, limit=1, meta=meta)
        except ProviderError as e:
            logger.warning("waypoint resolve failed: %s", e)
            wp_results = []
        payload = self._navigate_payload(dest_poi.name, dest_poi.lat, dest_poi.lng, meta)
        if not wp_results:
            return AgentResult(
                speech=f"没找到「{waypoint}」，已先为您导航到{dest_poi.name}。",
                ui_card={"type": "poi_list", "keyword": resolved_name, "items": items},
                data={"items": items},
            ).action("navigate", payload)

        wp = wp_results[0]
        payload["waypoints"] = [{"name": wp.name, "address": wp.address,
                                 "lat": wp.lat, "lng": wp.lng}]
        # 全程距离/时长（best-effort）：出发地→途经点→目的地
        distance_km = duration_min = 0
        current = current_location_from_meta(meta)
        if current:
            try:
                route = await self.poi.get_route(
                    GeoPoint(lat=current.lat, lng=current.lng),
                    GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng),
                    meta=meta, waypoints=[GeoPoint(lat=wp.lat, lng=wp.lng)])
                distance_km = route.get("distance_km") or 0
                duration_min = route.get("duration_min") or 0
            except Exception as e:                       # best-effort：算不出就只给时间线
                logger.debug("route plan distance unavailable: %s", e)
        head = (f"已把{wp.name}设为途经点，为您规划好路线："
                f"当前位置 → {wp.name} → {dest_poi.name}")
        if distance_km:
            dur = self._fmt_dur(duration_min)
            head += f"，全程约{distance_km}公里" + (f"、约{dur}" if dur else "")
        card = {"type": "route_plan", "origin": "当前位置", "destination": dest_poi.name,
                "waypoints": [{"name": wp.name, "address": wp.address}],
                "distance_km": distance_km, "duration_min": duration_min}
        return AgentResult(
            speech=head + "。", ui_card=card,
            data={"waypoints": payload["waypoints"]},
        ).action("navigate", payload)

    async def _route_plan_to(self, name: str, address: str, lat, lng, meta,
                             *, resolved_prefix: str = "") -> AgentResult:
        """导航到具体目的地：出路线规划卡（当前位置 → 目的地，best-effort 距离/时长）+ navigate。
        与顺路途经点的 route_plan 卡同一范式，让用户直观看到"已规划好路线（起点→终点）"。"""
        payload = self._navigate_payload(name, lat, lng, meta)
        distance_km = duration_min = 0
        current = current_location_from_meta(meta)
        if current:
            try:
                route = await self.poi.get_route(
                    GeoPoint(lat=current.lat, lng=current.lng),
                    GeoPoint(lat=lat, lng=lng), meta=meta)
                distance_km = route.get("distance_km") or 0
                duration_min = route.get("duration_min") or 0
            except Exception as e:                       # best-effort：算不出就只给起终点
                logger.debug("route plan distance unavailable: %s", e)
        speech = f"{resolved_prefix}为您导航到{name}（{address}）。"
        if distance_km:
            dur = self._fmt_dur(duration_min)
            speech += f"全程约{distance_km}公里" + (f"、约{dur}" if dur else "") + "，已规划好路线。"
        else:
            speech += "已规划好路线。"
        # 车辆接地 advisory（旅程 B3-2）：续航覆盖不了本程（含 15% 保留余量，与 charging
        # 同款判定）→ 主动提示补能。只加话术不加动作（advisory 不发车控/不改路线），
        # 用户接一句「沿途帮我找充电站」即进 charging 流程。电量经端侧 meta 注入
        # （server.py 把 VAL 真实电量写 vehicle_battery），拿不到就不提示（fail-open）。
        speech += self._range_advisory(distance_km, meta)
        card = {"type": "route_plan", "origin": "当前位置", "destination": name,
                "waypoints": [], "distance_km": distance_km, "duration_min": duration_min}
        return AgentResult(
            speech=speech, ui_card=card,
            data={"destination": name, "lat": lat, "lng": lng},
        ).action("navigate", payload)

    @staticmethod
    def _range_advisory(distance_km, meta) -> str:
        """里程 vs 电量续航的补能提示；不适用/数据缺失返回空串。"""
        try:
            pct = float(str((meta or {}).get("vehicle_battery", "")).replace("%", ""))
            dist = float(distance_km or 0)
        except (TypeError, ValueError):
            return ""
        if not (0 < pct <= 100) or dist <= 0:
            return ""
        full_range = float(os.getenv("CHARGING_FULL_RANGE_KM", "500") or 500)
        usable = pct / 100.0 * full_range
        if dist <= usable * 0.85:
            return ""
        return (f"提醒一下：当前电量约{round(pct)}%（续航约{round(usable)}公里），"
                f"本程约{round(dist)}公里，建议途中补能，可以说「沿途帮我找充电站」。")

    @staticmethod
    def _dest_matches(query: str, poi_name: str) -> bool:
        """目的地名与 POI 名强校验（R1，包含式）。

        `landmark.name_matches` 的「2 字公共子串」对**用户直报的目的地名**太松——
        「广州塔」和「广州仄仄科技有限公司」共享「广州」也算匹配，带 near 偏置的
        关键词搜索会让就近弱匹配顶掉真地标（旅程 B3-2/A2-4/B1-2 三例同族）。
        归一（去括号注记/空白/连接符）后任一方向包含才算。"""
        def norm(s: str) -> str:
            s = re.sub(r"[（(].*?[)）]", "", s or "")
            return re.sub(r"[\s·,，\-—]", "", s)
        a, b = norm(query), norm(poi_name)
        return bool(a) and bool(b) and (a in b or b in a)

    async def _find_destination(self, description: str, meta, near=None,
                                limit: int = 3, page: int = 1,
                                strict: bool = True) -> tuple[str, list]:
        """解析目的地 POI。

        视觉地标描述（“像笋的建筑”）：高德直接搜常返回勉强的模糊匹配，必须先经 LLM
        解析正式名称再由地图验证，避免被垃圾匹配抢占（否则导航到错误 POI）。
        普通目的地：原话直搜优先（带当前位置 near，使“最近的/附近的粤菜馆”按距离就近），
        top1 名字过 `_dest_matches` 强校验——不匹配先去偏置全国重搜（知名地标全国序第一），
        再走地标解析；都验证不出保留原结果兜底（话术会报出实际名，用户可纠正，不无中生有）。
        行政级目的地（「导航去惠州」）经 geocode level 判定，直接导航到行政中心，
        不给就近弱匹配（0.3km 的「惠州出口」）机会。
        limit：类目就近查询给更多候选（5）供用户选目的地；具体地点解析用默认（3）。
        """
        async def _direct(bias) -> list:
            try:
                return await self.poi.search(description, near=bias, limit=limit,
                                             page=page, meta=meta)
            except ProviderError as e:
                logger.warning("destination POI search failed: %s", e)
                return []

        async def _via_landmark() -> tuple[str, list]:
            for candidate in await self._landmark_candidates(description):
                try:
                    results = await self.poi.search(candidate, limit=limit, meta=meta)
                except ProviderError as e:
                    logger.warning("landmark candidate POI search failed: %s", e)
                    continue
                # 高德对非官方名会返回同位置的邻近无关 POI（搜“华润春笋大厦”→V东滨店）：
                # 只接受 top 结果名与候选实质匹配的，否则换下一个候选（如官方名“中国华润大厦”）。
                if results and name_matches(candidate, results[0].name):
                    return candidate, results
            return "", []

        if is_landmark_description(description):
            name, results = await _via_landmark()
            if results:
                return name, results
            results = await _direct(near)    # 地标候选验证不出来 → 退回原话直搜
            return (description, results) if results else ("", [])

        # R1：短名先过行政级判定（「惠州」「珠海」这类裸城市名不带 市/省 后缀，
        # 关键词搜索会顶出就近弱匹配）。仅 strict 且 ≤4 字触发，控制额外 geocode 调用面。
        geocode_level = getattr(self.poi, "geocode_level", None)
        if strict and geocode_level and 2 <= len(description) <= 4:
            try:
                level, loc = await geocode_level(description, meta=meta)
            except Exception as e:
                logger.debug("geocode level probe failed: %s", e)
                level, loc = "", ""
            if level in ("国家", "省", "市", "区县") and loc and "," in loc:
                try:
                    lng_s, lat_s = loc.split(",")[:2]
                    admin_poi = POI(id=f"admin_{description}", name=description,
                                    address=f"{description}（市区中心）",
                                    lat=float(lat_s), lng=float(lng_s))
                    return description, [admin_poi]
                except ValueError:
                    pass

        results = await _direct(near)
        if results:
            if not strict or self._dest_matches(description, results[0].name):
                return description, results
            # R1：就近弱匹配顶上了 top1 → 去偏置全国重搜（真地标全国序靠前）
            if near is not None:
                wide = await _direct(None)
                if wide and self._dest_matches(description, wide[0].name):
                    return description, wide
            name, lm = await _via_landmark()
            if lm:
                return name, lm
            return description, results     # 兜底：报出实际名让用户纠正
        return await _via_landmark()

    async def _landmark_candidates(self, description: str) -> list[str]:
        """把视觉化地标描述转换为少量地图可检索的正式 POI 候选（共享解析器，导航/充电共用）。"""
        return await landmark_candidates(self.llm, description, logger=logger)

    async def _correct_planner_landmark(self, dest: str, raw_text: str, meta) -> str:
        """修正云端 Planner 对视觉地标的错误臆断。

        Planner 的 LLM 有时会自作主张把视觉地标描述（"像笋的建筑"）直接解析成一个**具体楼名**
        （实测把"深圳笋状地标"错猜成"京基100"）写进 destination 槽位，绕过本 Agent 带 name_matches
        地图校验的专用地标解析器（它对整段凌乱原话仍能精准→中国华润大厦）。判据：原话是地标描述、
        而 dest 已被解析成**不含造型词**的具体名。命中则用原话重解析 + 高德校验，用**官方名**覆盖臆断；
        非该情形（普通导航/dest 本就是地标描述）零额外调用直接返回原 dest。"""
        if not (raw_text and is_landmark_description(raw_text) and not is_landmark_description(dest)):
            return dest
        for cand in await self._landmark_candidates(raw_text):
            try:
                hits = await self.poi.search(cand, limit=1, meta=meta)
            except ProviderError as e:
                logger.warning("planner-landmark correction search failed: %s", e)
                continue
            if hits and name_matches(cand, hits[0].name):
                logger.info("corrected planner dest %r -> landmark %r", dest, cand)
                return cand
        return dest

    async def _reverse_geocode(self, intent, ctx, meta) -> AgentResult:
        """逆地理编码：坐标 → 地址。"""
        lng_s = intent.slots.get("lng", "")
        lat_s = intent.slots.get("lat", "")
        if not lng_s or not lat_s:
            # 尝试用车辆位置
            current = await self._current_position(ctx, meta)
            if current and current.lng and current.lat:
                lng_s, lat_s = str(current.lng), str(current.lat)
            elif current and current.address:
                return AgentResult(speech=f"当前位置：{current.address}",
                                   data={"address": current.address})
            else:
                return AgentResult(status=NEED_SLOT, speech="请提供坐标或位置信息。",
                                   missing_slots=["lng", "lat"])
        try:
            lng, lat = float(lng_s), float(lat_s)
        except ValueError:
            return AgentResult(status=FAILED, speech="坐标格式不正确。")
        try:
            pt = await self.poi.reverse_geocode(lng, lat, meta=meta)
        except ProviderError as e:
            logger.warning("reverse_geocode failed, fallback to mock: %s", e)
            pt = await self._fallback.reverse_geocode(lng, lat, meta=meta)
        speech = f"该位置位于{pt.address}。" if pt.address else "未能解析该位置的地址。"
        return AgentResult(speech=speech,
                           data={"address": pt.address, "lng": lng, "lat": lat})

    async def _locate(self, intent, ctx, meta) -> AgentResult:
        """『我在哪 / 我现在在哪里 / 当前位置』：逆地理编码当前已授权位置 → 当前地址。
        与就近导航、天气统一只用浏览器 GPS；未授权时诚实提示开启定位，绝不回退编造 上海。"""
        current = await self._current_position(ctx, meta)
        if not current or current.lat is None or current.lng is None:
            return AgentResult(
                speech="还没获取到您的位置。在设置里开启定位授权后，我就能告诉您当前在哪，"
                       "也能帮您找最近的地点、导航回家或去公司。",
                follow_up="开启定位后再问我『我在哪』")
        try:
            pt = await self.poi.reverse_geocode(current.lng, current.lat, meta=meta)
        except ProviderError as e:
            logger.warning("locate reverse_geocode failed, fallback to mock: %s", e)
            pt = await self._fallback.reverse_geocode(current.lng, current.lat, meta=meta)
        addr = pt.address or "当前位置"
        return AgentResult(
            speech=f"您当前位于{addr}。",
            data={"address": pt.address, "lat": current.lat, "lng": current.lng})

    async def _poi_detail(self, intent, ctx, meta) -> AgentResult:
        """查询 POI 详情。"""
        poi_id = (intent.slots.get("poi_id") or "").strip()
        if not poi_id:
            return AgentResult(status=NEED_SLOT, speech="请提供地点 ID。",
                               missing_slots=["poi_id"])
        try:
            poi = await self.poi.poi_detail(poi_id, meta=meta)
        except ProviderError as e:
            logger.warning("poi_detail failed, fallback to mock: %s", e)
            poi = await self._fallback.poi_detail(poi_id, meta=meta)
        speech = f"{poi.name}，地址：{poi.address}。"
        if poi.rating:
            speech += f"评分{poi.rating}。"
        card = {"type": "poi_detail", "id": poi.id, "name": poi.name,
                "address": poi.address, "lat": poi.lat, "lng": poi.lng,
                "rating": poi.rating, "category": poi.category}
        return AgentResult(speech=speech, ui_card=card, data={"poi": card})

"""导航 Agent —— 所有 Agent 的参考范本。

演示：意图分发、缺槽位追问(NEED_SLOT)、按引用取上下文(ctx.fetch)、
产出动作(action) 与 HMI 卡片(ui_card)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.landmark import (
    is_landmark_description, landmark_candidates, name_matches)
from .providers import build_poi_provider
from .providers.base import GeoPoint
from .providers.mock import MockPOIProvider

logger = logging.getLogger("agent.navigation")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


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
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个导航请求我还不会处理。")

    async def _current_position(self, ctx, meta) -> GeoPoint | None:
        """优先使用本轮已授权的精确位置；未授权时才回退车辆上下文地址。"""
        current = current_location_from_meta(meta)
        if current:
            return GeoPoint(lat=current.lat, lng=current.lng)
        ctx_values = await ctx.fetch("vehicle.location")
        location_data = ctx_values.get("vehicle.location", "")
        return GeoPoint(address=location_data) if isinstance(location_data, str) and location_data else None

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
        r"(?:附近|周边|顺路|顺道|沿途|中途|那边|那儿|路过)[^，。,、]{0,8}?"
        r"(餐厅|饭店|吃饭|吃的|美食|川菜|火锅|咖啡|奶茶|小吃)")

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

        resolved_name, results = await self._find_destination(dest, meta)
        if not results:
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
        # 即便 planner 误把找餐厅拆成 food.search_restaurant(mock)，导航侧也能产出真实餐厅
        # 途经点候选（聚合器优先 waypoint_choice 卡）。
        stop_category = (intent.slots.get("stop_category") or "").strip()
        if not stop_category:
            m = self._STOP_RAW_RE.search(raw_text)
            if m:
                stop_category = m.group(1)
        if stop_category:
            return await self._navigate_with_stop_choice(
                first, resolved_name, stop_category, items, meta)

        # 普通导航
        prefix = (f"识别到您说的是{first.name}。" if resolved_name != dest else "")
        return AgentResult(
            speech=f"{prefix}为您找到{first.name}（{first.address}）。已为您规划路线。",
            ui_card={"type": "poi_list", "keyword": resolved_name, "items": items},
            data={"items": items},
        ).action("navigate", self._navigate_payload(first.name, first.lat, first.lng, meta))

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

    async def _find_destination(self, description: str, meta) -> tuple[str, list]:
        """解析目的地 POI。

        视觉地标描述（“像笋的建筑”）：高德直接搜常返回勉强的模糊匹配，必须先经 LLM
        解析正式名称再由地图验证，避免被垃圾匹配抢占（否则导航到错误 POI）。
        普通目的地：原话直搜优先，未命中再尝试地标解析兜底。
        """
        async def _direct() -> list:
            try:
                return await self.poi.search(description, limit=3, meta=meta)
            except ProviderError as e:
                logger.warning("destination POI search failed: %s", e)
                return []

        async def _via_landmark() -> tuple[str, list]:
            for candidate in await self._landmark_candidates(description):
                try:
                    results = await self.poi.search(candidate, limit=3, meta=meta)
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
            results = await _direct()        # 地标候选验证不出来 → 退回原话直搜
            return (description, results) if results else ("", [])

        results = await _direct()
        if results:
            return description, results
        return await _via_landmark()

    async def _landmark_candidates(self, description: str) -> list[str]:
        """把视觉化地标描述转换为少量地图可检索的正式 POI 候选（共享解析器，导航/充电共用）。"""
        return await landmark_candidates(self.llm, description, logger=logger)

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

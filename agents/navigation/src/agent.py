"""导航 Agent —— 所有 Agent 的参考范本。

演示：意图分发、缺槽位追问(NEED_SLOT)、按引用取上下文(ctx.fetch)、
产出动作(action) 与 HMI 卡片(ui_card)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
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

    async def _search_poi(self, intent, ctx, meta) -> AgentResult:
        keyword = intent.slots.get("keyword") or intent.slots.get("category")
        if not keyword:
            return AgentResult(status=NEED_SLOT, speech="您想找什么类型的地点呢？",
                               follow_up="请提供搜索关键词，如『充电站』『川菜馆』")

        # 按引用取车辆当前位置（隐私最小化：只取需要的 scope）
        ctx_values = await ctx.fetch("vehicle.location")
        location_data = ctx_values.get("vehicle.location", "")
        near = None
        if isinstance(location_data, str) and location_data:
            near = GeoPoint(address=location_data)

        rating_min = float(intent.slots.get("rating_min", 0) or 0)
        # 真实 provider 失败（超时/熔断/厂商错误）降级到 mock，保证链路不阻断；
        # 失败本身已由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
        try:
            results = await self.poi.search(keyword, near=near, rating_min=rating_min, meta=meta)
        except ProviderError as e:
            logger.warning("poi search failed, fallback to mock: %s", e)
            results = await self._fallback.search(keyword, near=near, rating_min=rating_min, meta=meta)
        names = "、".join(r.name for r in results[:3])
        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address} for r in results]
        card = {"type": "poi_list", "keyword": keyword, "items": items}
        return AgentResult(
            speech=f"为您找到 {len(results)} 个{keyword}，推荐前三个：{names}。需要导航过去吗？",
            ui_card=card,
            data={"items": items},  # F3：结构化结果供编排 slot_refs 取值（如 s1.data.items.0.id）
            follow_up="可以说『导航去第一个』",
        )

    async def _navigate_to(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您要去哪里？", follow_up="请告诉我目的地")
        return AgentResult(speech=f"好的，已为您规划到{dest}的路线。").action(
            "navigate", {"destination": dest})

    async def _reverse_geocode(self, intent, ctx, meta) -> AgentResult:
        """逆地理编码：坐标 → 地址。"""
        lng_s = intent.slots.get("lng", "")
        lat_s = intent.slots.get("lat", "")
        if not lng_s or not lat_s:
            # 尝试用车辆位置
            ctx_values = await ctx.fetch("vehicle.location")
            loc = ctx_values.get("vehicle.location", "")
            if isinstance(loc, str) and loc:
                return AgentResult(speech=f"当前位置：{loc}",
                                   data={"address": loc})
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

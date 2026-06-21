"""充能规划 Agent（charging-planner）—— Leaf 工具型范本。

帮用户找充电桩、根据电量/续航推荐、规划长途充能策略。
不做车控——只产出导航动作和信息建议。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from .providers import build_charging_provider
from .providers.mock import MockChargingProvider
from .providers.base import GeoPoint

logger = logging.getLogger("agent.charging_planner")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class ChargingPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.charging = build_charging_provider()
        self._fallback = MockChargingProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "charging.find": self._find,
            "charging.plan": self._plan,
            "charging.status": self._status,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="充能助手暂不支持该请求。")

    async def _find(self, intent, ctx, meta) -> AgentResult:
        """找附近的充电站。"""
        # 读电量
        ctx_values = await ctx.fetch("vehicle.battery")
        soc = ctx_values.get("vehicle.battery", "")

        # 获取位置
        current = current_location_from_meta(meta)
        if current:
            near = GeoPoint(lat=current.lat, lng=current.lng)
        else:
            loc_values = await ctx.fetch("vehicle.location")
            location = loc_values.get("vehicle.location", "")
            near = GeoPoint(address=location) if location else GeoPoint()

        # 搜充电站
        prefer = (intent.slots.get("prefer") or "").strip()
        charger_type = "快充" if "快" in prefer else ""
        try:
            stations = await self.charging.find_nearby(
                near, charger_type=charger_type, meta=meta)
        except ProviderError as e:
            logger.warning("charging find failed, fallback: %s", e)
            stations = await self._fallback.find_nearby(near, meta=meta)

        if not stations:
            return AgentResult(speech="附近暂未找到充电站，请稍后重试。")

        # 排序（空闲优先 + 距离近）
        stations.sort(key=lambda s: (-s.available, s.distance_km))

        # 组织回复
        top3 = stations[:3]
        names = "、".join(
            f"{s.name}（快充{s.available}/{s.total}空闲，{s.distance_km}km）"
            for s in top3)
        speech = f"为您找到 {len(stations)} 个充电站，推荐：{names}。需要导航过去吗？"
        items = [
            {"id": s.id, "name": s.name, "available": s.available,
             "total": s.total, "price": s.price_per_kwh,
             "distance_km": s.distance_km, "operator": s.operator}
            for s in stations
        ]
        return AgentResult(
            speech=speech,
            ui_card={"type": "charging_list", "items": items, "soc": soc},
            data={"items": items},
            follow_up="说『导航去第一个』或告诉我你的偏好",
        )

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        """规划长途充能策略。"""
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您要去哪里？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        ctx_values = await ctx.fetch("vehicle.battery")
        soc = ctx_values.get("vehicle.battery", "")

        # 调充电 Provider 规划沿途充电站
        try:
            plan = await self.charging.plan_route(dest, soc=soc, meta=meta)
        except ProviderError as e:
            logger.warning("charging plan failed: %s", e)
            return AgentResult(
                speech="暂无法规划充能路线，请稍后重试。", status=FAILED)

        # NEED_CONFIRM（涉及路线变更）
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"为您规划了充能方案：{plan.summary}。确认按此方案导航吗？",
            follow_up="说『确认』即可",
        ).action("charging.plan", {"stops": plan.stops}, require_confirm=True)

    async def _status(self, intent, ctx, meta) -> AgentResult:
        """查询当前充电状态。"""
        ctx_values = await ctx.fetch("vehicle.battery")
        battery = ctx_values.get("vehicle.battery", "未知")
        return AgentResult(
            speech=f"当前电量：{battery}。",
            data={"battery": battery},
        )

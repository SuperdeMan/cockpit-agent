"""充能规划 Agent（charging-planner）—— Leaf 工具型范本。

帮用户找充电桩、根据电量/续航推荐、规划长途充能策略。
不做车控——只产出导航动作和信息建议。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
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

        # 组织回复：实时空闲已知（mock）才报"X/Y空闲"，高德基础 POI 未知时报距离/评分，不编造
        top3 = stations[:3]

        def _desc(s):
            if s.total > 0:
                return f"{s.name}（{s.available}/{s.total}空闲，{s.distance_km}km）"
            extra = f"，评分{s.rating}" if s.rating else ""
            return f"{s.name}（{s.distance_km}km{extra}）"

        names = "、".join(_desc(s) for s in top3)
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

    # 行政区划级后缀——以此结尾的目的地视为"过泛"，先确认具体地点再规划途经点
    _ADMIN_SUFFIX = ("市", "省", "区", "县", "自治区", "自治州", "地区")

    @classmethod
    def _is_vague_destination(cls, dest: str) -> bool:
        """目的地是否过泛（行政区划级、无具体 POI 后缀）。"""
        d = (dest or "").strip()
        return bool(d) and d.endswith(cls._ADMIN_SUFFIX)

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        """规划长途充能策略。"""
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您要去哪里？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        # 目的地过泛（如"兰州市"）→ 先二次确认具体地点，再据此规划沿途途经点。
        # 候选地点经高德 POI 搜索给出（真实地点，不臆造）；这是澄清式 NEED_SLOT
        # （编排器用用户回复回填 destination 重跑本步），与已移除的"确认导航"冗余确认不同。
        if self._is_vague_destination(dest):
            candidates = []
            try:
                raw = await self.charging.suggest_destinations(dest, meta=meta)
                # 丢弃仍是行政区划级的候选（如"兰州市"自身），否则选它会再次触发追问
                candidates = [c for c in raw
                              if c.get("name") and not self._is_vague_destination(c["name"])]
            except ProviderError as e:
                logger.warning("charging suggest destinations failed: %s", e)
            if candidates:
                names = "、".join(c["name"] for c in candidates[:3])
                return AgentResult(
                    status=NEED_SLOT, missing_slots=["destination"],
                    speech=f"{dest}范围比较大，您具体要去哪个？例如{names}。"
                           f"说出名称或『第几个』，也可以直接告诉我详细地址。",
                    # purpose=dest_choice 让 HMI 把"第N个"回填为目的地槽位（而非发起导航）
                    ui_card={"type": "poi_list", "purpose": "dest_choice",
                             "title": f"{dest} · 选择目的地",
                             "items": [{"id": c.get("id", ""), "name": c["name"],
                                        "address": c.get("address", "")} for c in candidates]},
                    follow_up="选择具体目的地")
            return AgentResult(
                status=NEED_SLOT, missing_slots=["destination"],
                speech=f"{dest}范围比较大，您具体要去哪里？比如火车站、机场，"
                       f"或告诉我详细地址，我再为您规划沿途充电。",
                follow_up="告诉我具体地点")

        ctx_values = await ctx.fetch("vehicle.battery")
        soc = ctx_values.get("vehicle.battery", "")

        # 调充电 Provider 规划（高德：真实路线距离/时长 + 目的地附近真实充电站）
        try:
            plan = await self.charging.plan_route(dest, soc=soc, meta=meta)
        except ProviderError as e:
            logger.warning("charging plan failed, fallback: %s", e)
            try:
                plan = await self._fallback.plan_route(dest, soc=soc, meta=meta)
            except ProviderError:
                return AgentResult(speech="暂无法规划充能路线，请稍后重试。", status=FAILED)

        # 信息建议（advisory）：充能路线卡 = 出发地→沿途途经充电点→目的地，不二次确认、
        # 不发导航动作（导航由「导航」步处理）。专属 type 让聚合器在多意图下优先展示它
        # （否则只取首个卡=导航候选，充电途经点不可见）。
        card = {
            "type": "charging_route",
            "destination": dest,
            "distance_km": plan.distance_km,
            "duration_min": plan.total_duration_min,
            "stops": [{"name": s.get("name", ""), "address": s.get("address", ""),
                       "at_km": s.get("at_km")} for s in plan.stops],
            "soc": soc,
        } if plan.distance_km > 0 else None   # 无路线（需定位/取路失败）→ 纯语音
        return AgentResult(
            speech=plan.summary.rstrip("。") + "。",   # provider summary 可能已带句号，避免"。。"
            ui_card=card,
            data={"stops": plan.stops, "summary": plan.summary},
        )

    async def _status(self, intent, ctx, meta) -> AgentResult:
        """查询当前充电状态。"""
        ctx_values = await ctx.fetch("vehicle.battery")
        battery = ctx_values.get("vehicle.battery", "未知")
        return AgentResult(
            speech=f"当前电量：{battery}。",
            data={"battery": battery},
        )

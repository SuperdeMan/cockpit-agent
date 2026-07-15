"""高德充电 Provider —— 用高德 POI 搜**真实**充电站 + 真实路线距离/时长，替代 mock 假数据。

复用导航 agent 的 `AmapPOIProvider`（monorepo：容器 `COPY agents` 已含 navigation 代码）；
凭证经 env(AMAP_KEY) 注入，绝不进代码/日志。调用失败抛 ProviderError，Agent 据此降级 mock。

注：高德基础 POI 不返回充电桩实时空闲枪数/电价，故只给真实**站点名/地址/距离/评分**，
不编造空闲数（available/total 置 0，由 Agent 话术按"是否已知"自适应展示）。
"""
from __future__ import annotations
import logging

from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents.navigation.src.providers.amap import AmapPOIProvider
from agents.navigation.src.providers.base import GeoPoint as AmapGeo
from .base import ChargingProvider, ChargingStation, ChargingPlan, GeoPoint

logger = logging.getLogger("agent.charging_planner.amap")


class AmapChargingProvider(ChargingProvider):
    def __init__(self, key: str):
        import os
        self._poi = AmapPOIProvider(key)
        # 满电续航假设（公里），用于按电量估算可行驶里程与补电点位置；可经 env 调。
        try:
            self._full_range = float(os.getenv("CHARGING_FULL_RANGE_KM", "500"))
        except ValueError:
            self._full_range = 500.0

    @staticmethod
    def _amap_geo(p: GeoPoint) -> AmapGeo:
        return AmapGeo(lat=p.lat, lng=p.lng, address=p.address)

    @staticmethod
    def _core_place(dest: str) -> str:
        """从行政区划目的地取核心地名用于搜候选：甘肃省兰州市→兰州、朝阳区→朝阳。"""
        q = (dest or "").strip()
        for sep in ("自治区", "省"):
            if sep in q[:-1]:
                q = q.split(sep, 1)[1]
                break
        for suf in ("市", "自治州", "地区", "县", "区"):
            if len(q) > len(suf) and q.endswith(suf):
                q = q[: -len(suf)]
                break
        return q or (dest or "").strip()

    async def suggest_destinations(self, query: str, meta=None) -> list[dict]:
        """目的地过泛时用高德 POI 搜**真实**候选具体地点（火车站/机场/地标等）。"""
        core = self._core_place(query)
        try:
            pois = await self._poi.search(core, limit=8, meta=meta)
        except ProviderError as e:
            logger.warning("amap suggest_destinations failed: %s", e)
            return []
        out, seen = [], set()
        for p in pois:
            name = (p.name or "").strip()
            if not name or name in seen or name in (core, (query or "").strip()):
                continue
            seen.add(name)
            out.append({"id": p.id, "name": name, "address": p.address})
            if len(out) >= 5:
                break
        return out

    @staticmethod
    def _fmt_dur(minutes: float) -> str:
        m = int(minutes or 0)
        if m <= 0:
            return ""
        h, mm = divmod(m, 60)
        return (f"{h}小时" if h else "") + (f"{mm}分钟" if mm else "")

    async def find_nearby(self, location: GeoPoint, radius_km: float = 5,
                          charger_type: str = "", meta=None) -> list[ChargingStation]:
        keyword = "快充站" if charger_type and "快" in charger_type else "充电站"
        pois = await self._poi.search(
            keyword, near=self._amap_geo(location), limit=8, meta=meta)
        return [
            ChargingStation(
                id=p.id, name=p.name, address=p.address, lat=p.lat, lng=p.lng,
                charger_types=(["快充"] if "快" in keyword else []),
                available=0, total=0,            # 高德基础 POI 无实时枪数，不编造
                price_per_kwh="", operator="",
                distance_km=p.distance_km, rating=p.rating,
            )
            for p in pois
        ]

    async def availability(self, station_id: str, meta=None) -> ChargingStation:
        # 高德基础 POI 不提供实时枪数；返回占位（不编造空闲数）
        return ChargingStation(id=station_id)

    async def plan_route(self, destination: str, soc: str = "",
                         meta=None) -> ChargingPlan:
        """出发地 → 沿途途经充电点 → 目的地。

        起点取本轮已授权定位（无定位无法规划路线，诚实说明，不编造）；用高德真实路线几何
        按电量续航在**路线上**放补电途经点（不是目的地附近）。
        """
        soc_pct = 50
        if soc:
            try:
                soc_pct = int(str(soc).replace("%", "").strip())
            except ValueError:
                soc_pct = 50

        origin = current_location_from_meta(meta)
        if not origin:
            return ChargingPlan(
                summary=f"为前往{destination}规划沿途充电需要您的当前位置；"
                        f"开启定位后我再按出发地→途经充电点→目的地为您安排。",
                stops=[], total_duration_min=0)

        try:
            route = await self._poi.get_route(
                AmapGeo(lat=origin.lat, lng=origin.lng),
                AmapGeo(address=destination), meta=meta, with_polyline=True)
        except ProviderError as e:
            logger.warning("amap route failed: %s", e)
            return ChargingPlan(
                summary=f"暂时无法获取前往{destination}的路线，请稍后重试。",
                stops=[], total_duration_min=0)

        distance_km = float(route.get("distance_km") or 0)
        duration_min = float(route.get("duration_min") or 0)
        points = route.get("points") or []
        usable = soc_pct / 100.0 * self._full_range
        dur = self._fmt_dur(duration_min)
        head = f"前往{destination}，全程约{distance_km}公里" + (f"、约{dur}" if dur else "")

        # 续航足够 → 直达。带 15% 保留余量（Q2，旅程 A1-2 抓到：10%→50km 对 47.7km
        # 判「足够直达」只剩 2.3km 余量，真车是抛锚风险）——到达时至少留 15% 可用续航。
        if distance_km <= usable * 0.85 or not points:
            return ChargingPlan(
                summary=f"{head}。当前电量{soc_pct}%（约{round(usable)}公里续航）足够直达，无需途中补电。",
                stops=[], total_duration_min=int(duration_min), distance_km=distance_km)

        # 续航不够 → 沿途按里程放补电途经点：首段用到 ~85% 续航，之后每段约 65% 满电续航
        targets, d = [], usable * 0.85
        while d < distance_km - 20 and len(targets) < 4:
            targets.append(d)
            d += self._full_range * 0.65
        if not targets:
            # 短途但余量不足（首目标落进尾缓冲）→ 至少一个补电点，否则空集等同直达（Q2）
            targets.append(max(1.0, min(usable * 0.85, distance_km - 20)))
        stops = []
        for t in targets:
            pt = next((p for p in points if p["cum_km"] >= t), None)
            if not pt:
                continue
            try:
                near = await self._poi.search(
                    "充电站", near=AmapGeo(lat=pt["lat"], lng=pt["lng"]), limit=1, meta=meta)
            except ProviderError:
                near = []
            if near:
                st = near[0]
                stops.append({"name": st.name, "address": st.address,
                              "at_km": round(t), "charge_to": "80%"})

        if not stops:
            return ChargingPlan(
                summary=f"{head}。当前电量{soc_pct}%约{round(usable)}公里续航，长途需中途补电；"
                        f"沿途充电站暂未取到，到达附近时我再为你推荐。",
                stops=[], total_duration_min=int(duration_min), distance_km=distance_km)

        plan_line = "；".join(f"约{s['at_km']}公里处·{s['name']}" for s in stops)
        summary = (f"{head}。当前电量{soc_pct}%约可行驶{round(usable)}公里，"
                   f"建议途中补电 {len(stops)} 次：{plan_line}；补电后抵达{destination}。")
        return ChargingPlan(summary=summary, stops=stops,
                            total_duration_min=int(duration_min), distance_km=distance_km)

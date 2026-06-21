"""充电 Provider Mock 实现。"""
from __future__ import annotations
import random
from .base import ChargingProvider, ChargingStation, ChargingPlan, GeoPoint


class MockChargingProvider(ChargingProvider):
    """Mock 充电 Provider，生成模拟数据。"""

    _OPERATORS = ["特来电", "星星充电", "国家电网", "小桔充电"]
    _CHARGER_TYPES = [["快充"], ["慢充"], ["快充", "慢充"]]

    async def find_nearby(self, location: GeoPoint, radius_km: float = 5,
                          charger_type: str = "", meta=None) -> list[ChargingStation]:
        stations = []
        for i in range(5):
            available = random.randint(0, 4)
            total = random.randint(available, available + 3)
            stations.append(ChargingStation(
                id=f"station_{i}",
                name=f"{random.choice(self._OPERATORS)}·{location.address or '当前位置'}站{i+1}号",
                address=f"{location.address or '当前位置'}附近{random.randint(1,9)}号",
                lat=location.lat + random.uniform(-0.01, 0.01),
                lng=location.lng + random.uniform(-0.01, 0.01),
                charger_types=random.choice(self._CHARGER_TYPES),
                available=available,
                total=total,
                price_per_kwh=f"{random.uniform(0.8, 1.5):.1f}",
                operator=random.choice(self._OPERATORS),
                distance_km=round(random.uniform(0.3, 5.0), 1),
                rating=round(random.uniform(3.5, 5.0), 1),
            ))
        # 按空闲优先 + 距离近排序
        stations.sort(key=lambda s: (-s.available, s.distance_km))
        return stations

    async def availability(self, station_id: str, meta=None) -> ChargingStation:
        available = random.randint(0, 4)
        return ChargingStation(
            id=station_id,
            name=f"充电站 {station_id}",
            available=available,
            total=available + random.randint(0, 3),
        )

    async def plan_route(self, destination: str, soc: str = "",
                         meta=None) -> ChargingPlan:
        # 解析 SOC 百分比
        soc_pct = 50
        if soc:
            try:
                soc_pct = int(str(soc).replace("%", "").strip())
            except ValueError:
                soc_pct = 50

        # 模拟长途充能方案
        stops = []
        if soc_pct < 80:
            stops.append({
                "name": "嘉兴服务区·国网快充站",
                "km": 85,
                "charge_to": "80%",
                "duration_min": 25,
            })
        if soc_pct < 50:
            stops.append({
                "name": "杭州东服务区·特来电快充站",
                "km": 170,
                "charge_to": "70%",
                "duration_min": 20,
            })

        total_min = sum(s["duration_min"] for s in stops) + 120  # 行驶时间
        summary = f"从当前位置到{destination}约170km"
        if stops:
            stop_names = "、".join(s["name"] for s in stops)
            summary += f"，建议在{stop_names}充电，预计总行程{total_min}分钟（含充电）"
        else:
            summary += f"，当前电量{soc_pct}%足够直达，无需中途充电"

        return ChargingPlan(summary=summary, stops=stops, total_duration_min=total_min)

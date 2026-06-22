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
        """基于电量给**诚实**的充能策略。

        无真实路线/充电站数据源（mock）时，绝不编造具体服务区名、里程、总时长
        （旧实现对任意目的地都硬编码"嘉兴/杭州东服务区、145分钟"，明显失真）。
        只给电量相关的策略建议，具体站点交由到达沿途时的 charging.find 实时推荐。
        接入真实 EV 路线/充电 Provider 后，可在此返回精确站点与时间。
        """
        soc_pct = 50
        if soc:
            try:
                soc_pct = int(str(soc).replace("%", "").strip())
            except ValueError:
                soc_pct = 50

        if soc_pct >= 80:
            stops: list[dict] = []
            advice = f"当前电量{soc_pct}%较充足，中短途可直达；若为长途，建议出发前补满"
        elif soc_pct >= 50:
            stops = [{"note": "长途中段补电", "charge_to": "80%"}]
            advice = (f"当前电量{soc_pct}%，中短途够用；长途建议中途补电约 1 次，"
                      f"到达沿途时我再为你推荐附近的快充站")
        else:
            stops = [{"note": "尽快就近补电", "charge_to": "80%"},
                     {"note": "长途中段补电", "charge_to": "80%"}]
            advice = (f"当前电量{soc_pct}%偏低，建议先就近补电；长途约需中途补电 1~2 次，"
                      f"沿途我会为你推荐附近快充站")

        summary = f"前往{destination}：{advice}"
        return ChargingPlan(summary=summary, stops=stops, total_duration_min=0)

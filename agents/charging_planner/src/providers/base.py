"""充电 Provider 接口定义。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class GeoPoint:
    """地理位置。"""
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0


@dataclass
class ChargingStation:
    """充电站信息。"""
    id: str = ""
    name: str = ""
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    charger_types: list[str] = field(default_factory=list)  # ["快充","慢充"]
    available: int = 0     # 空闲枪数
    total: int = 0
    price_per_kwh: str = ""
    operator: str = ""     # 特来电/星星/国网
    distance_km: float = 0.0
    rating: float = 0.0


@dataclass
class ChargingPlan:
    """长途充能方案。"""
    summary: str = ""
    stops: list[dict] = field(default_factory=list)  # [{name, address, at_km, charge_to, lat?, lng?}]
    total_duration_min: int = 0
    distance_km: float = 0.0          # 全程里程（供卡片展示出发地→途经点→目的地）
    # 2026-09-11：路线折线 [[lat, lng], …]（amap 沿途取点面的坐标，粗但足够画一条线）与起点坐标；
    # 没有路线时都是空——卡片据此决定带不带几何，Android 地图页据此决定给不给「查看路线」
    path: list[list[float]] = field(default_factory=list)
    origin_loc: dict | None = None
    # CA2-19 S3：到达目的地时的估算剩余电量（%）。只有读到电量才有，读不到就是 None——不估
    arrive_soc: int | None = None


class ChargingProvider(ABC):
    """充电 Provider 抽象接口。"""

    @abstractmethod
    async def find_nearby(self, location: GeoPoint, radius_km: float = 5,
                          charger_type: str = "", meta=None) -> list[ChargingStation]:
        """搜索附近的充电站。"""
        ...

    @abstractmethod
    async def availability(self, station_id: str, meta=None) -> ChargingStation:
        """查询充电站实时状态。"""
        ...

    @abstractmethod
    async def plan_route(self, destination: str, soc: int | None = None,
                         meta=None, *, soc_note: str = "") -> ChargingPlan:
        """规划长途充能方案。

        `soc` 是当前电量百分比；读不到就是 None——不判断够不够，也不拿任何缺省值代替（CA2-19 S1）。
        `soc_note` 是说出口时跟在电量后面的来源说明（如「（模拟车读数）」），可为空。
        """
        ...

    async def suggest_destinations(self, query: str, meta=None) -> list[dict]:
        """目的地过泛（行政区划级）时给出候选具体地点 [{id,name,address}]。

        默认无候选（mock 等不联网 Provider）；真实 Provider（高德）覆写为 POI 搜索结果。
        """
        return []

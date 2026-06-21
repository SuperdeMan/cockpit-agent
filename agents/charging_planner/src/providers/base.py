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
    stops: list[dict] = field(default_factory=list)  # [{name, km, charge_to, duration_min}]
    total_duration_min: int = 0


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
    async def plan_route(self, destination: str, soc: str = "",
                         meta=None) -> ChargingPlan:
        """规划长途充能方案。"""
        ...

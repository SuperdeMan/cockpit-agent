"""POI Provider 接口。所有地图/POI 厂商实现此接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GeoPoint:
    lat: float = 0.0
    lng: float = 0.0
    address: str = ""


@dataclass
class POI:
    id: str = ""
    name: str = ""
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    rating: float = 0.0
    distance_km: float = 0.0
    category: str = ""
    price_info: str = ""


class POIProvider(ABC):
    @abstractmethod
    async def search(self, keyword: str, near: GeoPoint = None,
                     category: str = "", rating_min: float = 0,
                     limit: int = 5, meta: dict | None = None) -> list[POI]:
        """搜索 POI。meta 透传 trace_id/span_id 供 provider 调用可观测（可选）。"""
        ...

    @abstractmethod
    async def get_route(self, origin: GeoPoint, destination: GeoPoint,
                        meta: dict | None = None) -> dict:
        """获取路线规划。返回 {"distance_km": float, "duration_min": float, "steps": [...]}"""
        ...

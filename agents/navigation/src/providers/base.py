"""POI Provider 接口。所有地图/POI 厂商实现此接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GeoPoint:
    lat: float = 0.0
    lng: float = 0.0
    address: str = ""
    # 逆地理编码顺带回填（批 7 ②）：路名态势接口要城市（adcode 优先）。缺省空串，旧调用方零变化。
    city: str = ""
    adcode: str = ""


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
                     limit: int = 5, page: int = 1,
                     meta: dict | None = None) -> list[POI]:
        """搜索 POI。page 支持翻页（"换一批"取下一页不同结果）。
        meta 透传 trace_id/span_id 供 provider 调用可观测（可选）。"""
        ...

    @abstractmethod
    async def get_route(self, origin: GeoPoint, destination: GeoPoint,
                        meta: dict | None = None, with_polyline: bool = False,
                        waypoints: list[GeoPoint] | None = None,
                        strategy: str = "") -> dict:
        """获取路线规划（可带途经点 waypoints）。返回 {"distance_km", "duration_min", "steps", ...}

        strategy：路线策略（高德 v3 driving strategy 值，如 4=躲避拥堵、6=不走高速），
        空串=厂商默认。EVA 二轮 G11 前该参数全仓从未使用。
        """
        ...

    @abstractmethod
    async def reverse_geocode(self, lng: float, lat: float,
                              meta: dict | None = None) -> GeoPoint:
        """逆地理编码：坐标 → 地址。返回 GeoPoint（address 填充；能给的话再带 city / adcode）。"""
        ...

    async def road_traffic(self, name: str, city: str,
                           meta: dict | None = None) -> dict:
        """按**路名**查实时路况态势（批 7 ②）。返回
        {"name", "status"(0 未知 / 1 畅通 / 2 缓行 / 3 拥堵 / 4 严重拥堵), "description",
         "expedite_pct", "congested_pct", "blocked_pct", "unknown_pct"}。

        非抽象：不是每个厂商都有这条接口，缺省诚实抛 ProviderError（调用方按「拿不到」降级），
        不给 mock 数——与 `get_route` 的 `traffic` 字段一样，**没有就是没有**。
        """
        from agents._sdk.http import ProviderError
        raise ProviderError(f"{type(self).__name__}: road traffic not supported")

    @abstractmethod
    async def poi_detail(self, poi_id: str,
                         meta: dict | None = None) -> POI:
        """查询 POI 详情。"""
        ...

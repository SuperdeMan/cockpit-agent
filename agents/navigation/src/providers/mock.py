"""Mock POI Provider。PoC / 离线 / 单测用。"""
from __future__ import annotations
from .base import POIProvider, POI, GeoPoint


class MockPOIProvider(POIProvider):
    async def search(self, keyword: str, near: GeoPoint = None,
                     category: str = "", rating_min: float = 0,
                     limit: int = 5, meta: dict | None = None) -> list[POI]:
        items = []
        for i in range(1, limit + 1):
            poi = POI(
                id=f"mock_{keyword}_{i}",
                name=f"{keyword}·示例{i}",
                address=f"示例路{i}号",
                lat=31.23 + 0.01 * i,
                lng=121.47 + 0.01 * i,
                rating=round(4.0 + 0.2 * i, 1),
                distance_km=round(0.5 * i, 1),
                category=category or keyword,
            )
            if poi.rating >= rating_min:
                items.append(poi)
        return items

    async def get_route(self, origin: GeoPoint, destination: GeoPoint,
                        meta: dict | None = None) -> dict:
        return {
            "distance_km": 12.5,
            "duration_min": 25,
            "steps": ["直行 2km", "右转进入示例路", "到达目的地"],
        }

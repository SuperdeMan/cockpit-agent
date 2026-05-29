"""Mock 餐厅 Provider。"""
from __future__ import annotations
from .base import RestaurantProvider, Restaurant


class MockRestaurantProvider(RestaurantProvider):
    async def search(self, cuisine: str = "", location: str = "",
                     rating_min: float = 0, limit: int = 5) -> list[Restaurant]:
        return [
            Restaurant(id=f"r{i}", name=f"{cuisine}·名店{i}", cuisine=cuisine,
                       rating=round(4.0 + 0.2 * i, 1), price_per_person=80 + 20 * i,
                       address=f"美食街{i}号", distance_km=round(0.5 * i, 1))
            for i in range(1, limit + 1)
        ]

    async def reserve(self, restaurant_id: str, datetime: str,
                      party_size: int) -> tuple[bool, str]:
        return True, f"已预订 {restaurant_id}，{datetime}，{party_size}位"

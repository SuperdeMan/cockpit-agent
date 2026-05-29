"""餐厅 Provider 接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Restaurant:
    id: str = ""
    name: str = ""
    cuisine: str = ""
    rating: float = 0.0
    price_per_person: int = 0
    address: str = ""
    distance_km: float = 0.0


class RestaurantProvider(ABC):
    @abstractmethod
    async def search(self, cuisine: str = "", location: str = "",
                     rating_min: float = 0, limit: int = 5) -> list[Restaurant]:
        ...

    @abstractmethod
    async def reserve(self, restaurant_id: str, datetime: str,
                      party_size: int) -> tuple[bool, str]:
        """预订。返回 (ok, 确认信息或错误)。"""
        ...

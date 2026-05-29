"""停车场 Provider 接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ParkingLot:
    id: str = ""
    name: str = ""
    available: int = 0
    price_per_hour: float = 0.0
    distance_m: int = 0


class ParkingProvider(ABC):
    @abstractmethod
    async def find(self, location: str = "", limit: int = 3) -> list[ParkingLot]:
        ...

    @abstractmethod
    async def get_fee(self, lot_id: str, plate: str) -> tuple[int, str]:
        """查询停车费用。返回 (金额分, 错误信息)。"""
        ...

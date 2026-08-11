"""Mock 停车场 Provider。"""
from __future__ import annotations
from .base import ParkingProvider, ParkingLot


class MockParkingProvider(ParkingProvider):
    async def find(self, location: str = "", limit: int = 3) -> list[ParkingLot]:
        return [
            ParkingLot(id=f"lot{i}", name=f"停车场{i}", available=10*i,
                       price_per_hour=4+i, distance_m=120*i)
            for i in range(1, limit+1)
        ]

    async def get_fee(self, lot_id: str, plate: str) -> tuple[int, str]:
        return 1500, ""  # 15元

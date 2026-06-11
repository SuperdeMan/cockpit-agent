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

    @abstractmethod
    async def pay(self, order_id: str, plate: str, amount_cents: int) -> tuple[bool, str]:
        """支付停车费。返回 (ok, 凭证号或错误信息)。

        TODO(F3 proto 批次): 切换到 payment-gateway（Authorize/Capture），Agent 不持支付凭证。
        当前 AuthorizeResponse 未返回 confirm_token、Capture 不可达，待 proto 修复后接入。
        """
        ...

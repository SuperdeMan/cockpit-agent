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

    # 2026-08-11 批 2：`pay()` 已从本接口删除——支付不是停车数据源的职责。
    # 缴费经 agents/_sdk/payment_client.py → payment-gateway（Authorize/Capture，
    # §9.17），Agent 与 provider 均不持支付凭证、不产收据。残留一个 mock pay 接口
    # 只会诱惑下一个实现者绕过网关。

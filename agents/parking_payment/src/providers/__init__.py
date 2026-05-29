"""停车场 Provider 工厂。"""
import os
from .base import ParkingProvider
from .mock import MockParkingProvider


def build_parking_provider() -> ParkingProvider:
    vendor = os.getenv("PARKING_VENDOR", "mock")
    if vendor == "etcp" and os.getenv("ETCP_KEY"):
        # TODO(Phase1): from .etcp import EtcpProvider; ...
        pass
    return MockParkingProvider()

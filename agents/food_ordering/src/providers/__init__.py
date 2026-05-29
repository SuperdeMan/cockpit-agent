"""餐厅 Provider 工厂。"""
import os
from .base import RestaurantProvider
from .mock import MockRestaurantProvider


def build_restaurant_provider() -> RestaurantProvider:
    vendor = os.getenv("RESTAURANT_VENDOR", "mock")
    if vendor == "dianping" and os.getenv("DIANPING_KEY"):
        # TODO(Phase1): from .dianping import DianpingProvider; return DianpingProvider(...)
        pass
    return MockRestaurantProvider()

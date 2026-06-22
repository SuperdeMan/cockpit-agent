"""充电 Provider 工厂。"""
from .mock import MockChargingProvider


def build_charging_provider():
    """构建充电 Provider。有 AMAP_KEY → 高德（真实充电站 + 真实路线）；否则 mock。"""
    import os
    import logging
    if os.getenv("AMAP_KEY"):
        try:
            from .amap import AmapChargingProvider
            return AmapChargingProvider(os.getenv("AMAP_KEY"))
        except Exception as e:
            logging.getLogger("agent.charging_planner.providers").warning(
                "AmapChargingProvider init failed, falling back to mock: %s", e)
    return MockChargingProvider()

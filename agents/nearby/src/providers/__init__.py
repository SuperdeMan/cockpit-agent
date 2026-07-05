"""周边 Provider 工厂。按 env 选 real/mock，构造失败回退 mock。

复用与 navigation 同一门控：POI_VENDOR=amap 且 AMAP_KEY 非空 → 真实高德，否则 mock。
"""
import logging
import os
from .base import PlaceProvider
from .mock import MockPlaceProvider

logger = logging.getLogger("agent.nearby.providers")


def build_place_provider() -> PlaceProvider:
    if os.getenv("POI_VENDOR") == "amap" and os.getenv("AMAP_KEY"):
        try:
            from .amap import AmapPlaceProvider
            return AmapPlaceProvider(os.getenv("AMAP_KEY"))
        except Exception as e:            # 构造失败不阻断，回退 mock
            logger.warning("AmapPlaceProvider init failed, fallback mock: %s", e)
    return MockPlaceProvider()

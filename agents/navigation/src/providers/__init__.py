"""导航 Provider 工厂。按环境变量选择 real/mock；构造失败回退 mock（不阻断 PoC）。"""
import logging
import os

from .base import POIProvider
from .mock import MockPOIProvider

logger = logging.getLogger("agent.navigation.providers")


def build_poi_provider() -> POIProvider:
    vendor = os.getenv("POI_VENDOR", "mock")
    if vendor == "amap" and os.getenv("AMAP_KEY"):
        try:
            from .amap import AmapPOIProvider
            return AmapPOIProvider(os.getenv("AMAP_KEY"))
        except Exception as e:  # 构造失败（缺包/参数）不阻断，回退 mock
            logger.warning("AmapPOIProvider init failed, falling back to mock: %s", e)
    if vendor == "baidu" and os.getenv("BAIDU_MAP_KEY"):
        # TODO(Production): 接入 BaiduPOIProvider。
        pass
    return MockPOIProvider()

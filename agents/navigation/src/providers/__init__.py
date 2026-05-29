"""导航 Provider 工厂。按环境变量选择 real/mock。"""
import os
from .base import POIProvider
from .mock import MockPOIProvider


def build_poi_provider() -> POIProvider:
    vendor = os.getenv("POI_VENDOR", "mock")
    if vendor == "amap" and os.getenv("AMAP_KEY"):
        # TODO(Phase1): from .amap import AmapPOIProvider; return AmapPOIProvider(os.getenv("AMAP_KEY"))
        pass
    if vendor == "baidu" and os.getenv("BAIDU_MAP_KEY"):
        # TODO(Phase1): from .baidu import BaiduPOIProvider; ...
        pass
    return MockPOIProvider()

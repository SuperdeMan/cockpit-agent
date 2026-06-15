"""导航 Provider 工厂。按环境变量选择 real/mock。"""
import os
from .base import POIProvider
from .mock import MockPOIProvider


def build_poi_provider() -> POIProvider:
    vendor = os.getenv("POI_VENDOR", "mock")
    if vendor == "amap" and os.getenv("AMAP_KEY"):
        # TODO(Production): 接入 AmapPOIProvider。
        pass
    if vendor == "baidu" and os.getenv("BAIDU_MAP_KEY"):
        # TODO(Production): 接入 BaiduPOIProvider。
        pass
    return MockPOIProvider()

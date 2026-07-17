"""导航 Provider 工厂。按环境变量选择 real/mock。

治理 P0：显式 real 意图（POI_VENDOR 显式非 mock）下构造失败 → fail-fast，不静默
回退 mock；AMAP_KEY 单独在场不构成本域意图——该 key 与 nearby/charging/geocoder
共享，本域门控历来是「POI_VENDOR=amap 且有 key」（见 .env.example）。
"""
import logging
import os

from agents._sdk.provenance import fail, log_resolution

from .base import POIProvider
from .mock import MockPOIProvider

logger = logging.getLogger("agent.navigation.providers")


def build_poi_provider() -> POIProvider:
    vendor = (os.getenv("POI_VENDOR", "mock") or "mock").strip().lower()
    if vendor == "amap":
        key = os.getenv("AMAP_KEY")
        if not key:
            fail("poi", "POI_VENDOR=amap 但 AMAP_KEY 为空")
        try:
            from .amap import AmapPOIProvider
            p = AmapPOIProvider(key)
            log_resolution("poi", "amap", True)
            return p
        except Exception as e:
            fail("poi", f"AmapPOIProvider 构造失败：{e}", e)
    elif vendor == "baidu":
        # TODO(Production): 接入 BaiduPOIProvider。
        fail("poi", "POI_VENDOR=baidu 未接入（TODO），可用 amap 或 mock")
    elif vendor != "mock":
        fail("poi", f"未知 POI_VENDOR={vendor}")
    log_resolution("poi", "mock", False)
    return MockPOIProvider()

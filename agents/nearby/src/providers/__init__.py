"""周边 Provider 工厂。按 env 选 real/mock。

复用与 navigation 同一门控：POI_VENDOR=amap 且 AMAP_KEY 非空 → 真实高德，否则 mock。
治理 P0：显式 real 意图（POI_VENDOR 显式非 mock）下构造失败 → fail-fast，不静默回退 mock。
"""
import logging
import os

from agents._sdk.provenance import fail, log_resolution

from .base import PlaceProvider
from .mock import MockPlaceProvider

logger = logging.getLogger("agent.nearby.providers")


def build_place_provider() -> PlaceProvider:
    vendor = (os.getenv("POI_VENDOR", "mock") or "mock").strip().lower()
    if vendor == "amap":
        key = os.getenv("AMAP_KEY")
        if not key:
            fail("place", "POI_VENDOR=amap 但 AMAP_KEY 为空")
        try:
            from .amap import AmapPlaceProvider
            p = AmapPlaceProvider(key)
            log_resolution("place", "amap", True)
            return p
        except Exception as e:
            fail("place", f"AmapPlaceProvider 构造失败：{e}", e)
    elif vendor != "mock":
        fail("place", f"未知 POI_VENDOR={vendor}（本域仅支持 amap|mock）")
    log_resolution("place", "mock", False)
    return MockPlaceProvider()

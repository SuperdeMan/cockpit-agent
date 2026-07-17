"""充电 Provider 工厂。

治理 P0：本域门控历来是 AMAP_KEY 在场即真实（无独立 vendor env），故 key 即显式
real 意图——构造失败 fail-fast，不静默回退 mock。
"""
import logging
import os

from agents._sdk.provenance import fail, log_resolution

from .mock import MockChargingProvider

logger = logging.getLogger("agent.charging_planner.providers")


def build_charging_provider():
    """构建充电 Provider。有 AMAP_KEY → 高德（真实充电站 + 真实路线）；否则 mock。"""
    key = os.getenv("AMAP_KEY")
    if key:
        try:
            from .amap import AmapChargingProvider
            p = AmapChargingProvider(key)
            log_resolution("charging", "amap", True)
            return p
        except Exception as e:
            fail("charging", f"AmapChargingProvider 构造失败：{e}", e)
    log_resolution("charging", "mock", False)
    return MockChargingProvider()

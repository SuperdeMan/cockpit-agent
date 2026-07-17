"""停车场 Provider 工厂。

治理 P0：PARKING_VENDOR 显式指到未接入的实现时 fail-fast 说清楚，不再静默落回
mock。支付域 PoC 阶段「设计即模拟」（payment-gateway 同契约）——默认 mock 是产品
事实而非数据造假，严格栈豁免见设计文档 §4 D2。
"""
import os

from agents._sdk.provenance import fail, log_resolution

from .base import ParkingProvider
from .mock import MockParkingProvider


def build_parking_provider() -> ParkingProvider:
    vendor = (os.getenv("PARKING_VENDOR", "mock") or "mock").strip().lower()
    if vendor == "etcp":
        # TODO(Production): 接入 EtcpProvider。
        fail("parking", "PARKING_VENDOR=etcp 未接入（TODO）")
    elif vendor != "mock":
        fail("parking", f"未知 PARKING_VENDOR={vendor}")
    log_resolution("parking", "mock", False)
    return MockParkingProvider()

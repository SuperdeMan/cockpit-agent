"""停车场 Provider 工厂。

治理 P0：PARKING_VENDOR 显式指到未接入的实现时 fail-fast 说清楚，不再静默落回
mock。本域豁免的理由是**停车数据源（ETCP）未接真**——支付自 2026-08-11 起是
独立决议域（payment-gateway，§9.17），不再与本域捆绑；provider 接口也不再含
pay()（支付经 agents/_sdk/payment_client.py 走网关）。
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
    m = MockParkingProvider()
    log_resolution("parking", "mock", False, m)
    return m

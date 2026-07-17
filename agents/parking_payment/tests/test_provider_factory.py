"""停车 Provider 工厂契约：默认 mock（设计即模拟）；显式指到未接入实现时 fail-fast（治理 P0）。"""
import pytest

from agents._sdk.provenance import ProviderConfigError
from agents.parking_payment.src.providers import build_parking_provider
from agents.parking_payment.src.providers.mock import MockParkingProvider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PARKING_VENDOR", raising=False)
    monkeypatch.delenv("REQUIRE_REAL_PROVIDERS", raising=False)
    monkeypatch.delenv("REQUIRE_REAL_EXEMPT", raising=False)


def test_default_env_resolves_mock():
    assert isinstance(build_parking_provider(), MockParkingProvider)


def test_explicit_unimplemented_vendor_fails_fast(monkeypatch):
    monkeypatch.setenv("PARKING_VENDOR", "etcp")
    with pytest.raises(ProviderConfigError, match="未接入"):
        build_parking_provider()


def test_strict_stack_default_exempts_parking(monkeypatch):
    """严格栈下 parking 默认豁免（设计即模拟），照常 mock 不炸。"""
    monkeypatch.setenv("REQUIRE_REAL_PROVIDERS", "on")
    assert isinstance(build_parking_provider(), MockParkingProvider)

"""POI 工厂契约：默认 mock；显式 real 意图下构造失败 fail-fast（治理 P0）。"""
import pytest

from agents._sdk.provenance import ProviderConfigError
from agents.navigation.src.providers import build_poi_provider
from agents.navigation.src.providers.mock import MockPOIProvider


class _Boom:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("POI_VENDOR", raising=False)
    monkeypatch.delenv("AMAP_KEY", raising=False)


def test_default_env_resolves_mock():
    assert isinstance(build_poi_provider(), MockPOIProvider)


def test_shared_key_alone_stays_mock(monkeypatch):
    """AMAP_KEY 与 nearby/charging/geocoder 共享，单独在场不构成本域意图（门控不变）。"""
    monkeypatch.setenv("AMAP_KEY", "k")
    assert isinstance(build_poi_provider(), MockPOIProvider)


def test_explicit_vendor_without_key_fails_fast(monkeypatch):
    monkeypatch.setenv("POI_VENDOR", "amap")
    with pytest.raises(ProviderConfigError, match="AMAP_KEY 为空"):
        build_poi_provider()


def test_init_failure_fails_fast_instead_of_mock(monkeypatch):
    monkeypatch.setenv("POI_VENDOR", "amap")
    monkeypatch.setenv("AMAP_KEY", "k")
    monkeypatch.setattr("agents.navigation.src.providers.amap.AmapPOIProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="构造失败"):
        build_poi_provider()


def test_unimplemented_vendor_fails_fast(monkeypatch):
    monkeypatch.setenv("POI_VENDOR", "baidu")
    with pytest.raises(ProviderConfigError, match="未接入"):
        build_poi_provider()

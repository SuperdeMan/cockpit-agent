"""周边 Provider 工厂契约：默认 mock；显式 real 意图下构造失败 fail-fast（治理 P0）。"""
import pytest

from agents._sdk.provenance import ProviderConfigError
from agents.nearby.src.providers import build_place_provider
from agents.nearby.src.providers.mock import MockPlaceProvider


class _Boom:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("POI_VENDOR", raising=False)
    monkeypatch.delenv("AMAP_KEY", raising=False)


def test_default_env_resolves_mock():
    assert isinstance(build_place_provider(), MockPlaceProvider)


def test_explicit_vendor_without_key_fails_fast(monkeypatch):
    monkeypatch.setenv("POI_VENDOR", "amap")
    with pytest.raises(ProviderConfigError, match="AMAP_KEY 为空"):
        build_place_provider()


def test_init_failure_fails_fast_instead_of_mock(monkeypatch):
    monkeypatch.setenv("POI_VENDOR", "amap")
    monkeypatch.setenv("AMAP_KEY", "k")
    monkeypatch.setattr("agents.nearby.src.providers.amap.AmapPlaceProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="构造失败"):
        build_place_provider()

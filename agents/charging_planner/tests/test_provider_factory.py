"""充电 Provider 工厂契约：无 key mock；key 即意图，构造失败 fail-fast（治理 P0）。"""
import pytest

from agents._sdk.provenance import ProviderConfigError
from agents.charging_planner.src.providers import build_charging_provider
from agents.charging_planner.src.providers.mock import MockChargingProvider


class _Boom:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AMAP_KEY", raising=False)


def test_default_env_resolves_mock():
    assert isinstance(build_charging_provider(), MockChargingProvider)


def test_key_with_init_failure_fails_fast(monkeypatch):
    """本域门控历来是 AMAP_KEY 在场即真实，故 key 即显式意图——构造失败不落 mock。"""
    monkeypatch.setenv("AMAP_KEY", "k")
    monkeypatch.setattr(
        "agents.charging_planner.src.providers.amap.AmapChargingProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="构造失败"):
        build_charging_provider()

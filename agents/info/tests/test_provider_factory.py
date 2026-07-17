"""Provider 工厂契约：默认全 mock 不炸；显式 real 意图下构造失败 fail-fast（治理 P0）。"""
import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents._sdk.http import ProviderError
from agents._sdk.provenance import ProviderConfigError
from agents.info.src.providers import (
    build_news_provider, build_search_provider, build_sports_provider,
    build_stock_provider, build_weather_provider,
)
from agents.info.src.providers.mock import (
    MockNewsProvider, MockSearchProvider, MockSportsProvider, MockStockProvider,
    MockWeatherProvider,
)
from agents.info.src.providers.qweather import QWeatherProvider

_ENVS = (
    "WEATHER_VENDOR", "QWEATHER_PROJECT_ID", "QWEATHER_KEY_ID", "QWEATHER_PRIVATE_KEY",
    "QWEATHER_PRIVATE_KEY_PATH", "QWEATHER_KEY",
    "EXA_API_KEY", "ANYSEARCH_API_KEY", "BING_SEARCH_KEY",
    "SERPAPI_API_KEY", "NEWS_API_KEY", "API_FOOTBALL_KEY",
    "TUSHARE_TOKEN", "STOCK_API_KEY",
    "REQUIRE_REAL_PROVIDERS", "REQUIRE_REAL_EXEMPT",
)


class _Boom:
    """替身 Provider 类：构造即炸，用来模拟「配了凭证但构造失败」。"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)


def _ed25519_pem() -> str:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_qweather_credentials_take_precedence_over_implicit_mock_mode(monkeypatch):
    monkeypatch.setenv("WEATHER_VENDOR", "mock")
    monkeypatch.setenv("QWEATHER_PROJECT_ID", "project-id")
    monkeypatch.setenv("QWEATHER_KEY_ID", "credential-id")
    monkeypatch.setenv("QWEATHER_PRIVATE_KEY", _ed25519_pem())

    assert isinstance(build_weather_provider(), QWeatherProvider)


def test_default_env_resolves_all_mock():
    """默认 env（无任何凭证）→ 全 mock 且不 raise：CI / 离线开发路径回归。"""
    assert isinstance(build_weather_provider(), MockWeatherProvider)
    assert isinstance(build_search_provider(), MockSearchProvider)
    assert isinstance(build_news_provider(), MockNewsProvider)
    assert isinstance(build_sports_provider(), MockSportsProvider)
    assert isinstance(build_stock_provider(), MockStockProvider)


def test_weather_explicit_vendor_without_creds_fails_fast(monkeypatch):
    monkeypatch.setenv("WEATHER_VENDOR", "qweather")
    with pytest.raises(ProviderConfigError, match="凭证不齐"):
        build_weather_provider()


def test_weather_partial_jwt_creds_fail_fast(monkeypatch):
    monkeypatch.setenv("QWEATHER_PROJECT_ID", "project-id")  # 三件套只配了一段
    with pytest.raises(ProviderConfigError, match="凭证不齐"):
        build_weather_provider()


def test_weather_init_failure_fails_fast_instead_of_mock(monkeypatch):
    monkeypatch.setenv("QWEATHER_KEY", "legacy-key")
    monkeypatch.setattr("agents.info.src.providers.qweather.QWeatherProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="构造失败"):
        build_weather_provider()


def test_search_single_configured_engine_broken_fails_fast(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setattr("agents.info.src.providers.search_exa.ExaSearchProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="搜索引擎全部构造失败"):
        build_search_provider()


def test_search_degrades_across_real_engines_without_failing(monkeypatch):
    """引擎间降级是真实源之间的排序：Exa 坏、AnySearch 好 → 用 AnySearch，不 raise。"""

    class _Dummy:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setenv("EXA_API_KEY", "k1")
    monkeypatch.setenv("ANYSEARCH_API_KEY", "k2")
    monkeypatch.setattr("agents.info.src.providers.search_exa.ExaSearchProvider", _Boom)
    monkeypatch.setattr("agents.info.src.providers.search_any.AnySearchProvider", _Dummy)
    assert isinstance(build_search_provider(), _Dummy)


def test_news_configured_but_broken_fails_fast(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    monkeypatch.setattr("agents.info.src.providers.news_serpapi.SerpApiNewsProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="新闻源全部构造失败"):
        build_news_provider()


def test_sports_configured_but_broken_fails_fast(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "k")
    monkeypatch.setattr(
        "agents.info.src.providers.sports_apifootball.ApiFootballProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="构造失败"):
        build_sports_provider()


def test_stock_configured_but_broken_fails_fast(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "k")
    monkeypatch.setattr("agents.info.src.providers.stock_tushare.TushareStockProvider", _Boom)
    with pytest.raises(ProviderConfigError, match="股票源全部构造失败"):
        build_stock_provider()


def test_strict_stack_forbids_mock_resolution(monkeypatch):
    """严格栈（治理 P2）：REQUIRE_REAL_PROVIDERS=on 时无凭证的 mock 决议直接拒绝启动。"""
    monkeypatch.setenv("REQUIRE_REAL_PROVIDERS", "on")
    with pytest.raises(ProviderConfigError, match="REQUIRE_REAL_PROVIDERS"):
        build_weather_provider()


def test_strict_stack_exempt_domain_still_mocks(monkeypatch):
    monkeypatch.setenv("REQUIRE_REAL_PROVIDERS", "on")
    monkeypatch.setenv("REQUIRE_REAL_EXEMPT", "weather")
    assert isinstance(build_weather_provider(), MockWeatherProvider)


def test_news_runtime_failure_returns_empty_not_mock(monkeypatch):
    """运行期真实新闻源失败 → 诚实空列表（上层承认拿不到），不再回退 mock 假头条。"""
    from agents.info.src.agent import InfoAgent

    agent = InfoAgent()

    async def boom(**kwargs):
        raise ProviderError("upstream 500")

    monkeypatch.setattr(agent.news, "headlines", boom)
    assert asyncio.run(agent._news_from_provider("", 5, {})) == []

"""信息类 Provider 工厂。按环境变量选择 real/mock；构造失败回退 mock（不阻断 PoC）。"""
import logging
import os

from .base import (WeatherProvider, SearchProvider, NewsProvider, StockProvider,
                   SportsProvider)
from .mock import (MockWeatherProvider, MockSearchProvider, MockNewsProvider,
                   MockStockProvider, MockSportsProvider)

logger = logging.getLogger("agent.info.providers")


def _load_qweather_private_key():
    """和风 JWT 私钥原料。返回 str/bytes 交由 QWeatherJWT 健壮解析（PEM / 裸 base64 / 种子均可）。

    优先 QWEATHER_PRIVATE_KEY（直接粘贴）；否则 QWEATHER_PRIVATE_KEY_PATH——是真实文件就读文件，
    不是文件则容错当作"直接贴进来的私钥内容"（兼容误填到 PATH 字段的情况）。
    """
    inline = os.getenv("QWEATHER_PRIVATE_KEY")
    if inline:
        return inline
    path = os.getenv("QWEATHER_PRIVATE_KEY_PATH")
    if path:
        path = path.strip()
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        logger.warning("QWEATHER_PRIVATE_KEY_PATH 不是文件，按内联私钥内容处理")
        return path  # 容错：值即私钥内容
    return None


def build_weather_provider() -> WeatherProvider:
    vendor = os.getenv("WEATHER_VENDOR", "mock").strip().lower()
    host = os.getenv("QWEATHER_HOST", "devapi.qweather.com")
    project_id = os.getenv("QWEATHER_PROJECT_ID")
    key_id = os.getenv("QWEATHER_KEY_ID")
    private_key = _load_qweather_private_key()
    # Compose historically defaults WEATHER_VENDOR to mock.  If a complete
    # QWeather credential set is present, that implicit default must not hide
    # the real provider; absent/invalid credentials still fall back to mock.
    has_jwt = bool(project_id and key_id and private_key)
    has_legacy_key = bool(os.getenv("QWEATHER_KEY"))
    if vendor == "qweather" or has_jwt or has_legacy_key:
        try:
            if has_jwt:  # JWT（和风新版，优先）
                from .qweather import QWeatherProvider, QWeatherJWT
                return QWeatherProvider(
                    jwt_auth=QWeatherJWT(project_id, key_id, private_key), host=host)
            if has_legacy_key:                           # API Key（旧版）
                from .qweather import QWeatherProvider
                return QWeatherProvider(api_key=os.getenv("QWEATHER_KEY"), host=host)
        except Exception as e:  # 构造失败（缺包/密钥格式错）不阻断，回退 mock
            logger.warning("QWeatherProvider init failed, falling back to mock: %s", e)
    return MockWeatherProvider()


def build_search_provider() -> SearchProvider:
    """联网搜索 Provider 工厂。优先 Exa（返回正文级内容），降级 AnySearch → Bing → mock。"""
    # Exa（优先，正文级检索）
    if os.getenv("EXA_API_KEY"):
        try:
            from .search_exa import ExaSearchProvider
            return ExaSearchProvider(
                os.getenv("EXA_API_KEY"),
                base_url=os.getenv("EXA_BASE_URL", ""),
            )
        except Exception as e:
            logger.warning("ExaSearchProvider init failed: %s", e)
    # AnySearch（兜底搜索）
    if os.getenv("ANYSEARCH_API_KEY"):
        try:
            from .search_any import AnySearchProvider
            return AnySearchProvider(
                os.getenv("ANYSEARCH_API_KEY"),
                base_url=os.getenv("ANYSEARCH_BASE_URL", ""),
            )
        except Exception as e:
            logger.warning("AnySearchProvider init failed: %s", e)
    # Bing（再降级）
    if os.getenv("BING_SEARCH_KEY"):
        try:
            from .search_bing import BingSearchProvider
            return BingSearchProvider(os.getenv("BING_SEARCH_KEY"))
        except Exception as e:
            logger.warning("BingSearchProvider init failed: %s", e)
    return MockSearchProvider()


def build_news_provider() -> NewsProvider:
    """新闻 Provider 工厂。SerpApi（Google+Baidu News，AnySearch 兜底）→ mock。"""
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    if serpapi_key:
        try:
            # AnySearch 兜底（可选）
            anysearch = None
            if os.getenv("ANYSEARCH_API_KEY"):
                from .search_any import AnySearchProvider
                anysearch = AnySearchProvider(
                    os.getenv("ANYSEARCH_API_KEY"),
                    base_url=os.getenv("ANYSEARCH_BASE_URL", ""),
                )
            from .news_serpapi import SerpApiNewsProvider
            return SerpApiNewsProvider(serpapi_key, anysearch_provider=anysearch)
        except Exception as e:
            logger.warning("SerpApiNewsProvider init failed, falling back to mock: %s", e)
    # 旧 NewsAPI 降级（向后兼容）
    if os.getenv("NEWS_API_KEY"):
        try:
            from .news_api import NewsAPIProvider
            return NewsAPIProvider(os.getenv("NEWS_API_KEY"))
        except Exception as e:
            logger.warning("NewsAPIProvider init failed, falling back to mock: %s", e)
    return MockNewsProvider()


def build_extractor():
    """正文补抓 Provider（AnySearch extract，MCP）。无 ANYSEARCH_API_KEY 返回 None。

    用于 Exa 结果正文为空时的 best-effort 补抓；不配置则跳过（不影响主链）。
    """
    if os.getenv("ANYSEARCH_API_KEY"):
        try:
            from .search_any import AnySearchProvider
            return AnySearchProvider(
                os.getenv("ANYSEARCH_API_KEY"),
                base_url=os.getenv("ANYSEARCH_BASE_URL", ""),
            )
        except Exception as e:
            logger.warning("extractor init failed: %s", e)
    return None


def build_sports_provider() -> SportsProvider:
    """赛事 Provider 工厂。api-football（实时比分/赛程）→ mock。"""
    key = os.getenv("API_FOOTBALL_KEY")
    if key:
        try:
            from .sports_apifootball import ApiFootballProvider
            return ApiFootballProvider(key, host=os.getenv("API_FOOTBALL_HOST", ""))
        except Exception as e:
            logger.warning("ApiFootballProvider init failed, falling back to mock: %s", e)
    return MockSportsProvider()


def build_stock_provider() -> StockProvider:
    """股票 Provider 工厂。Tushare（免费 API）→ mock。"""
    tushare_token = os.getenv("TUSHARE_TOKEN")
    if tushare_token:
        try:
            from .stock_tushare import TushareStockProvider
            return TushareStockProvider(tushare_token)
        except Exception as e:
            logger.warning("TushareStockProvider init failed, falling back to mock: %s", e)
    # 旧 Alpha Vantage 降级（向后兼容）
    if os.getenv("STOCK_API_KEY"):
        try:
            from .stock_quote import QuoteStockProvider
            return QuoteStockProvider(os.getenv("STOCK_API_KEY"))
        except Exception as e:
            logger.warning("QuoteStockProvider init failed, falling back to mock: %s", e)
    return MockStockProvider()

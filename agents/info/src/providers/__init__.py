"""信息类 Provider 工厂。按环境变量选择 real/mock。

治理 P0/P1（docs/design/2026-07-17-data-authenticity-governance.md §4 D2/D1）：
- 显式要求真实数据（vendor env 显式指定，或配了该域凭证）而构造失败 → fail-fast
  启动即炸，绝不静默回退 mock；默认 env（无凭证）照旧 mock，CI/离线开发不受影响。
- 每个工厂返回前 `log_resolution(domain, vendor, real, provider)`：统一决议日志
  `provider[<domain>]=...` 供全栈 grep 审计 + 给 provider 盖来源章（出卡处
  `provenance.attach()` 据此写 ui_card `_prov`，conventions §9.3）。
"""
import logging
import os

from agents._sdk.provenance import fail, log_resolution

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
    has_jwt = bool(project_id and key_id and private_key)
    has_legacy_key = bool(os.getenv("QWEATHER_KEY"))
    # 完整凭证在场即用真实源（压过 compose 历史默认的 WEATHER_VENDOR=mock）。
    if has_jwt or has_legacy_key:
        try:
            from .qweather import QWeatherProvider, QWeatherJWT
            if has_jwt:  # JWT（和风新版，优先）
                p = QWeatherProvider(
                    jwt_auth=QWeatherJWT(project_id, key_id, private_key), host=host)
            else:        # API Key（旧版）
                p = QWeatherProvider(api_key=os.getenv("QWEATHER_KEY"), host=host)
            log_resolution("weather", "qweather", True, p)
            return p
        except Exception as e:
            fail("weather", f"QWeatherProvider 构造失败：{e}", e)
    # 显式 real 意图但凭证不齐：vendor 点名 qweather，或配了任意一段和风凭证。
    if vendor == "qweather" or any([project_id, key_id, private_key]):
        fail("weather", "和风凭证不齐：JWT 需 QWEATHER_PROJECT_ID+QWEATHER_KEY_ID+"
                        "QWEATHER_PRIVATE_KEY(_PATH) 三件套，或旧版 QWEATHER_KEY")
    m = MockWeatherProvider()
    log_resolution("weather", "mock", False, m)
    return m


def build_search_provider() -> SearchProvider:
    """联网搜索 Provider 工厂。优先 Exa（返回正文级内容），降级 AnySearch → Bing。

    引擎间降级是真实源之间的排序（记 warning，不算造假）；**已配置的引擎全部构造
    失败**才 fail-fast——那时唯一的出路是 mock，而这正是要禁止的静默路径。
    """
    errors: list[str] = []
    # Exa（优先，正文级检索）
    if os.getenv("EXA_API_KEY"):
        try:
            from .search_exa import ExaSearchProvider
            p = ExaSearchProvider(
                os.getenv("EXA_API_KEY"),
                base_url=os.getenv("EXA_BASE_URL", ""),
            )
            log_resolution("search", "exa", True, p)
            return p
        except Exception as e:
            errors.append(f"exa: {e}")
            logger.warning("ExaSearchProvider init failed: %s", e)
    # AnySearch（兜底搜索）
    if os.getenv("ANYSEARCH_API_KEY"):
        try:
            from .search_any import AnySearchProvider
            p = AnySearchProvider(
                os.getenv("ANYSEARCH_API_KEY"),
                base_url=os.getenv("ANYSEARCH_BASE_URL", ""),
            )
            log_resolution("search", "anysearch", True, p)
            return p
        except Exception as e:
            errors.append(f"anysearch: {e}")
            logger.warning("AnySearchProvider init failed: %s", e)
    # Bing（再降级）
    if os.getenv("BING_SEARCH_KEY"):
        try:
            from .search_bing import BingSearchProvider
            p = BingSearchProvider(os.getenv("BING_SEARCH_KEY"))
            log_resolution("search", "bing", True, p)
            return p
        except Exception as e:
            errors.append(f"bing: {e}")
            logger.warning("BingSearchProvider init failed: %s", e)
    if errors:
        fail("search", "已配置的搜索引擎全部构造失败：" + "；".join(errors))
    m = MockSearchProvider()
    log_resolution("search", "mock", False, m)
    return m


def build_news_provider() -> NewsProvider:
    """新闻 Provider 工厂。SerpApi（Google+Baidu News，AnySearch 兜底）→ 旧 NewsAPI。"""
    errors: list[str] = []
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
            p = SerpApiNewsProvider(serpapi_key, anysearch_provider=anysearch)
            log_resolution("news", "serpapi", True, p)
            return p
        except Exception as e:
            errors.append(f"serpapi: {e}")
            logger.warning("SerpApiNewsProvider init failed: %s", e)
    # 旧 NewsAPI 降级（向后兼容）
    if os.getenv("NEWS_API_KEY"):
        try:
            from .news_api import NewsAPIProvider
            p = NewsAPIProvider(os.getenv("NEWS_API_KEY"))
            log_resolution("news", "newsapi", True, p)
            return p
        except Exception as e:
            errors.append(f"newsapi: {e}")
            logger.warning("NewsAPIProvider init failed: %s", e)
    if errors:
        fail("news", "已配置的新闻源全部构造失败：" + "；".join(errors))
    m = MockNewsProvider()
    log_resolution("news", "mock", False, m)
    return m


def build_extractor():
    """正文补抓 Provider（AnySearch extract，MCP）。无 ANYSEARCH_API_KEY 返回 None。

    纯增强件（Exa 结果正文为空时 best-effort 补抓），缺席/构造失败只是少一层补抓、
    不产生任何替代数据——不适用 fail-fast，保持宽松降级。
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
    """赛事 Provider 工厂。api-football（实时比分/赛程）。"""
    key = os.getenv("API_FOOTBALL_KEY")
    if key:
        try:
            from .sports_apifootball import ApiFootballProvider
            p = ApiFootballProvider(key, host=os.getenv("API_FOOTBALL_HOST", ""))
            log_resolution("sports", "api-football", True, p)
            return p
        except Exception as e:
            fail("sports", f"ApiFootballProvider 构造失败：{e}", e)
    m = MockSportsProvider()
    log_resolution("sports", "mock", False, m)
    return m


def build_stock_provider() -> StockProvider:
    """股票 Provider 工厂。Tushare（免费 API）→ 旧 Alpha Vantage。"""
    errors: list[str] = []
    if os.getenv("TUSHARE_TOKEN"):
        try:
            from .stock_tushare import TushareStockProvider
            p = TushareStockProvider(os.getenv("TUSHARE_TOKEN"))
            log_resolution("stock", "tushare", True, p)
            return p
        except Exception as e:
            errors.append(f"tushare: {e}")
            logger.warning("TushareStockProvider init failed: %s", e)
    # 旧 Alpha Vantage 降级（向后兼容）
    if os.getenv("STOCK_API_KEY"):
        try:
            from .stock_quote import QuoteStockProvider
            p = QuoteStockProvider(os.getenv("STOCK_API_KEY"))
            log_resolution("stock", "alphavantage", True, p)
            return p
        except Exception as e:
            errors.append(f"alphavantage: {e}")
            logger.warning("QuoteStockProvider init failed: %s", e)
    if errors:
        fail("stock", "已配置的股票源全部构造失败：" + "；".join(errors))
    m = MockStockProvider()
    log_resolution("stock", "mock", False, m)
    return m

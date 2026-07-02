"""信息 Agent（info）。实时天气 + 预报 + 预警 + 生活指数 + 联网搜索 + 新闻 + 赛事 + 股票。

Phase 1：使用 Provider 适配层（mock/real 可切换）。真实 provider 抖动时降级到 mock，
保证链路不阻断；失败本身由 provider span(outcome=error) 记录，便于在 Dashboard 发现。

R2.4：按域拆分为 `handlers/{weather,search,sports,news,stock,briefing}` mixin，本文件只留
意图分发（handle）、公共件（城市解析/定位标注）与 provider 装配（__init__）。域方法经 self
相互调用（MRO），共享工具见 `handlers/_util`。历史上定义于本文件的模块级 helper 在文件尾
向后兼容重导出（测试/外部按 `agents.info.src.agent` 导入路径不变）。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from .providers import (
    build_weather_provider, build_search_provider,
    build_news_provider, build_stock_provider, build_sports_provider,
    build_extractor,
)
from .providers.mock import MockNewsProvider
from .providers.amap_geocoder import build_location_resolver
from .handlers import (
    WeatherMixin, SearchMixin, SportsMixin, NewsMixin, StockMixin, BriefingMixin,
)

logger = logging.getLogger("agent.info")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class InfoAgent(WeatherMixin, SearchMixin, SportsMixin, NewsMixin, StockMixin,
                BriefingMixin, BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.weather = build_weather_provider()
        self.search = build_search_provider()
        self.news = build_news_provider()
        self.stock = build_stock_provider()
        self.sports = build_sports_provider()
        self.extractor = build_extractor()  # 正文补抓（AnySearch extract，可为 None）
        self.location_resolver = build_location_resolver()
        # 东方财富实时行情（免费无 key，全市场）：Tushare 无港美股权限时的降级
        try:
            from .providers.stock_eastmoney import EastMoneyStockProvider
            self._stock_eastmoney = EastMoneyStockProvider()
        except Exception:
            self._stock_eastmoney = None
        self._fallback_news = MockNewsProvider()  # 新闻 provider 失败时的离线兜底
        # 主动早报（P2 雏形）：NATS 连接 + 每日一次去重
        self._nc = None
        self._last_briefing_date = ""

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "info.weather": self._weather,
            "info.forecast": self._forecast,
            "info.alerts": self._alerts,
            "info.indices": self._indices,
            "info.air_quality": self._air_quality,
            "info.search": self._search,
            "info.sports": self._sports,
            "info.news": self._news,
            "info.stock": self._stock,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个信息查询我还不会处理。")

    # ── 公共件：城市解析 / 定位标注（weather、search 等域经 self 调用）──────────

    async def _resolve_city(self, intent, ctx, meta: dict | None = None) -> str:
        """从 intent slots 或浏览器定位解析城市名。空串表示无法解析。"""
        city = (intent.slots.get("city") or "").strip()
        current = current_location_from_meta(meta)
        if not city and current:
            # 和风 GeoAPI 接受 ``lng,lat``，再由 Provider 解析为规范城市与空气接口坐标。
            city = f"{current.lng:.6f},{current.lat:.6f}"
        # 不再使用 vehicle.location 的 mock 默认值
        # 如果没有定位且没有指定城市，返回空串，让调用者返回 NEED_SLOT
        return city

    async def _display_city(self, intent, city: str, meta: dict | None = None) -> str:
        """坐标仅用于请求上游；展示时优先用高德反查出的可读地址。"""
        explicit_city = (intent.slots.get("city") or "").strip()
        if explicit_city:
            return explicit_city
        current = current_location_from_meta(meta)
        if current:
            try:
                return await self.location_resolver.reverse(current.lng, current.lat, meta)
            except ProviderError as e:
                logger.warning("weather reverse geocode unavailable: %s", e)
                return ""
        return city

    @staticmethod
    def _location_accuracy_note(meta: dict | None = None) -> str:
        """定位精度较差时附加提示，引导用户手动指定城市。"""
        try:
            accuracy_m = float((meta or {}).get("current_accuracy_m", ""))
        except (TypeError, ValueError):
            return ""
        if accuracy_m > 5000:
            return "（定位精度较低，如不准确请直接告诉我城市名）"
        return ""


# ── 向后兼容重导出 ────────────────────────────────────────────────
# 这些模块级 helper 历史上定义在本文件，测试/外部按 `agents.info.src.agent` 导入。
# R2.4 拆域后迁入 handlers/，此处重导出保持导入路径不变（改一处不破外部契约）。
from .handlers._util import (  # noqa: E402,F401
    _shanghai_now, _to_simplified, _is_coordinate_label,
)
from .handlers.search import _plan_search, _is_fresh_sensitive  # noqa: E402,F401
from .handlers.sports import _season_candidates, _detect_league  # noqa: E402,F401
from .handlers.news import (  # noqa: E402,F401
    _extract_news_subject, _news_interest_keywords, _rank_news_by_interest,
    _normalize_publish_time, _rank_news_quality, _recent_only,
)

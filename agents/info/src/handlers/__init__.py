"""info Agent 分域 handler（mixin）：weather / search / sports / news / stock / briefing。

各域方法经 self 相互调用（MRO 透明），共享工具见 `_util`，跨域调度入口在 `agent.InfoAgent`。
"""
from .weather import WeatherMixin
from .search import SearchMixin
from .sports import SportsMixin
from .news import NewsMixin
from .stock import StockMixin
from .briefing import BriefingMixin

__all__ = ["WeatherMixin", "SearchMixin", "SportsMixin",
           "NewsMixin", "StockMixin", "BriefingMixin"]

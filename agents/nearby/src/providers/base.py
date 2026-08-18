"""周边地点 Provider 接口。所有地图/生活服务厂商实现此接口。

领域语义（不是厂商 endpoint）：search(周边搜索) / detail(详情增强)。
富字段覆盖评分/人均/电话/营业时间/特色/图片——补齐 navigation 薄 POI 丢掉的维度。
评分/人均等字段厂商常缺，缺则留默认（0/空），Agent 话术按「是否已知」自适应，绝不编造。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class GeoPoint:
    lat: float = 0.0
    lng: float = 0.0
    address: str = ""


@dataclass
class Place:
    """一个周边地点（餐饮/酒店/景点/影院/停车/充电…通用）。"""
    id: str = ""
    name: str = ""
    category: str = ""          # 高德主类目（type 首段）
    address: str = ""
    city: str = ""              # 高德 cityname（商户官方检索 searchType=2 需要城市）
    lat: float = 0.0
    lng: float = 0.0
    distance_km: float = 0.0
    rating: float = 0.0         # business.rating（常缺→0）
    cost: str = ""              # business.cost 人均（字符串，可能空）
    tel: str = ""               # business.tel 电话（可能多号，; 分隔）
    open_today: str = ""        # business.opentime_today 今日营业时间
    open_week: str = ""         # business.opentime_week 营业时间描述
    tags: str = ""              # business.tag 特色标签（逗号分隔）
    area: str = ""              # business.business_area 商圈
    photos: list[str] = field(default_factory=list)  # photos[].url


# 营业时间解析 2026-08-19 收敛进 `runtime/openhours.py`（QA Q2 残余）。
# **这里只转口，不再有第二份实现**：云侧编排要用同一份解析算「哪家最晚关门」，
# 而它的镜像不 COPY `agents/`——落点判据是镜像依赖闭包（CLAUDE.md §3）。
# 保留本名字是为了不动既有的三处 import（agent.py / amap.py / mock.py + 两个测试）。
from runtime.openhours import is_open_now  # noqa: E402,F401


class PlaceProvider(ABC):
    @abstractmethod
    async def search(self, keyword: str, *, category: str = "", near: GeoPoint | None = None,
                     rating_min: float = 0, price_min: float = 0, price_max: float = 0,
                     brand: str = "", open_now: bool = False, sort: str = "",
                     limit: int = 10, page: int = 1,
                     meta: dict | None = None) -> list[Place]:
        """周边搜索。near 为空走关键字检索；page 支持翻页（「换一批」）。rating_min/price 区间
        [price_min,price_max]（0=该端不限，价位查询丢无人均）/open_now/sort 由实现做客户端过滤。"""
        ...

    @abstractmethod
    async def detail(self, place_id: str = "", *, name: str = "",
                     near: GeoPoint | None = None, meta: dict | None = None) -> Place:
        """详情增强。有 place_id 直查详情；否则用 name 搜一个取首个。"""
        ...

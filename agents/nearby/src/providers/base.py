"""周边地点 Provider 接口。所有地图/生活服务厂商实现此接口。

领域语义（不是厂商 endpoint）：search(周边搜索) / detail(详情增强)。
富字段覆盖评分/人均/电话/营业时间/特色/图片——补齐 navigation 薄 POI 丢掉的维度。
评分/人均等字段厂商常缺，缺则留默认（0/空），Agent 话术按「是否已知」自适应，绝不编造。
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


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


_TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-~到至]\s*(\d{1,2}):(\d{2})")


def is_open_now(open_today: str, now_min: int | None = None) -> bool | None:
    """按今日营业时间判断此刻是否营业。返回 True/False；无法解析→None（未知）。
    now_min: 当前「时:分」折算分钟（测试注入）；缺省取北京时间。"""
    s = (open_today or "").strip()
    if not s:
        return None
    if "24小时" in s or "00:00-24:00" in s or "全天" in s:
        return True
    if now_min is None:
        n = datetime.now(timezone(timedelta(hours=8)))
        now_min = n.hour * 60 + n.minute
    ranges = _TIME_RANGE_RE.findall(s)
    if not ranges:
        return None
    for h1, m1, h2, m2 in ranges:
        start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        if end <= start:                       # 跨零点（如 17:00-02:00）
            if now_min >= start or now_min <= end:
                return True
        elif start <= now_min <= end:
            return True
    return False


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

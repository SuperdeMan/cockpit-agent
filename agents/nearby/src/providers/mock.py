"""Mock 周边 Provider。PoC / 离线 / 单测 / 降级兜底用（确定性富假数据）。"""
from __future__ import annotations
from .base import PlaceProvider, Place

# 类目 → 一批确定性示例名，让 mock 也能体现「多类目」
_SAMPLE_NAMES = {
    "餐饮": ["蜀香源川菜馆", "老灶火锅", "初色日料"],
    "美食": ["蜀香源川菜馆", "老灶火锅", "初色日料"],
    "酒店": ["星程酒店", "全季酒店", "亚朵酒店"],
    "景点": ["城市公园", "滨江绿道", "市博物馆"],
    "影院": ["万达影城", "CGV影城", "横店影城"],
    "停车": ["科苑路停车场", "万象城地库", "路边停车位"],
    "充电": ["特来电充电站", "星星充电", "国家电网充电站"],
}


def _names_for(category: str, keyword: str) -> list[str] | None:
    for k, names in _SAMPLE_NAMES.items():
        if k in (category or "") or k in (keyword or ""):
            return names
    return None


class MockPlaceProvider(PlaceProvider):
    async def search(self, keyword, *, category="", near=None, rating_min=0,
                     price_max=0, brand="", open_now=False, sort="", limit=10, page=1,
                     meta=None) -> list[Place]:
        base_names = _names_for(category, keyword)
        start = (max(1, page) - 1) * limit
        out: list[Place] = []
        for i in range(1, limit + 1):
            idx = start + i
            name = (base_names[(idx - 1) % len(base_names)] if base_names
                    else f"{brand or keyword or '地点'}·示例{idx}")
            p = Place(
                id=f"mock_{keyword or category or 'poi'}_{idx}",
                name=name, category=category or keyword or "地点",
                address=f"示例路{idx}号",
                lat=31.23 + 0.01 * idx, lng=121.47 + 0.01 * idx,
                distance_km=round(0.4 * idx, 1),
                rating=round(4.0 + 0.1 * (idx % 6), 1),
                cost=str(60 + 10 * (idx % 5)),
                tel=f"021-1234{idx:04d}",
                open_today="10:00-22:00", open_week="周一至周日 10:00-22:00",
                tags="环境好,服务佳", area="示例商圈",
            )
            if rating_min and p.rating < rating_min:
                continue
            if price_max and float(p.cost or 0) > price_max:
                continue
            out.append(p)
        if sort == "rating":
            out.sort(key=lambda x: x.rating, reverse=True)
        return out

    async def detail(self, place_id="", *, name="", near=None, meta=None) -> Place:
        return Place(
            id=place_id or "mock_detail", name=name or "蜀香源川菜馆",
            category="餐饮", address="示例路1号", lat=31.23, lng=121.47,
            rating=4.6, cost="88", tel="021-88888888",
            open_today="10:00-22:00", open_week="周一至周日 10:00-22:00",
            tags="招牌毛血旺,水煮鱼", area="示例商圈",
            photos=["https://example.com/p1.jpg"],
        )

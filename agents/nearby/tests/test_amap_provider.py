"""高德 AmapPlaceProvider 单测：mock 掉底层 HTTP，喂 POI 2.0 富字段黄金响应。

验证富字段解析（评分/人均/电话/营业时间/标签/图片）、"lng,lat" 坐标顺序、空字段([])归一、
评分/人均过滤、评分排序、status!=1 报错、地名先地理编码再周边、详情按 id/按 name。不发真实网络。
"""
import asyncio

import pytest

from agents._sdk.http import ProviderError
from agents.nearby.src.providers.amap import AmapPlaceProvider
from agents.nearby.src.providers.base import GeoPoint, is_open_now


def _provider(responses: dict):
    """responses: {path 子串 -> json|Exception}。返回装好 fake get_json 的 provider。"""
    p = AmapPlaceProvider(key="test-key")

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    return p


_AROUND_OK = {
    "status": "1", "info": "OK", "infocode": "10000",
    "pois": [
        {"id": "B1", "name": "蜀香源川菜馆", "address": "科苑路1号",
         "location": "121.500,31.230", "type": "餐饮服务;中餐厅;川菜",
         "distance": "800",
         "business": {"rating": "4.6", "cost": "88", "tel": "021-12345678",
                      "opentime_today": "10:00-22:00",
                      "opentime_week": "周一至周日 10:00-22:00",
                      "tag": "水煮鱼,毛血旺", "business_area": "科技园"},
         "photos": [{"title": "门脸", "url": "https://img.amap.com/p1.jpg"},
                    {"title": [], "url": "https://img.amap.com/p2.jpg"}]},
        {"id": "B2", "name": "老灶火锅", "address": [],  # 高德空字段返回 []
         "location": "121.510,31.240", "type": "餐饮服务;火锅店",
         "distance": "1500",
         "business": {"rating": "4.2", "cost": "120"}},
    ],
}


def test_search_parses_rich_fields():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("川菜", near=GeoPoint(lng=121.5, lat=31.23)))
    assert len(res) == 2
    a = res[0]
    assert a.name == "蜀香源川菜馆"
    assert a.lng == 121.500 and a.lat == 31.230       # lng,lat 顺序
    assert a.rating == 4.6
    assert a.cost == "88"
    assert a.tel == "021-12345678"
    assert a.open_today == "10:00-22:00"
    assert a.tags == "水煮鱼,毛血旺"
    assert a.distance_km == 0.8                        # 800m → 0.8km
    assert a.photos == ["https://img.amap.com/p1.jpg", "https://img.amap.com/p2.jpg"]
    assert res[1].address == ""                        # [] 归一成空串


def test_search_text_when_no_location():
    p = _provider({"/v5/place/text": _AROUND_OK})      # 无 near → 关键字检索
    res = asyncio.run(p.search("川菜"))
    assert len(res) == 2


def test_search_rating_min_filters():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("川菜", near=GeoPoint(lng=121.5, lat=31.23), rating_min=4.5))
    assert len(res) == 1 and res[0].rating == 4.6


def test_search_price_max_filters():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("川菜", near=GeoPoint(lng=121.5, lat=31.23), price_max=100))
    assert [r.name for r in res] == ["蜀香源川菜馆"]   # 人均120 的老灶火锅被剔


def test_search_sort_by_rating():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("川菜", near=GeoPoint(lng=121.5, lat=31.23), sort="rating"))
    assert [r.rating for r in res] == [4.6, 4.2]


def test_status_not_one_raises():
    bad = {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}
    p = _provider({"/v5/place/around": bad})
    with pytest.raises(ProviderError):
        asyncio.run(p.search("川菜", near=GeoPoint(lng=121.5, lat=31.23)))


def test_geocode_then_around_for_address_near():
    geo = {"status": "1", "info": "OK", "geocodes": [{"location": "116.397,39.908"}]}
    p = _provider({"/v3/geocode/geo": geo, "/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("川菜", near=GeoPoint(address="北京市东城区")))
    assert len(res) == 2                               # 地名先地理编码→坐标→周边搜索


_DETAIL_OK = {
    "status": "1", "info": "OK",
    "pois": [{"id": "B1", "name": "蜀香源川菜馆", "address": "科苑路1号",
              "location": "121.500,31.230", "type": "餐饮服务;川菜",
              "business": {"rating": "4.6", "cost": "88", "tel": "021-12345678",
                           "opentime_today": "10:00-22:00"}}]}


def test_detail_by_id_parses():
    p = _provider({"/v5/place/detail": _DETAIL_OK})
    place = asyncio.run(p.detail("B1"))
    assert place.name == "蜀香源川菜馆"
    assert place.tel == "021-12345678"
    assert place.cost == "88"


def test_detail_by_name_searches_first():
    p = _provider({"/v5/place/around": _AROUND_OK, "/v5/place/detail": _DETAIL_OK})
    place = asyncio.run(p.detail("", name="蜀香源", near=GeoPoint(lng=121.5, lat=31.23)))
    assert place.id == "B1"                            # 先搜取首个 id 再查详情


def test_detail_not_found_raises():
    bad = {"status": "1", "info": "OK", "pois": []}
    p = _provider({"/v5/place/detail": bad})
    with pytest.raises(ProviderError):
        asyncio.run(p.detail("nonexistent"))


def test_is_open_now_ranges():
    # now_min 注入避免依赖真实时钟
    assert is_open_now("11:00-14:00 17:00-21:30", now_min=12 * 60) is True    # 12:00 在第一段
    assert is_open_now("11:00-14:00 17:00-21:30", now_min=15 * 60) is False   # 15:00 两段之间
    assert is_open_now("17:00-02:00", now_min=1 * 60) is True                 # 跨零点 01:00 营业
    assert is_open_now("24小时营业", now_min=3 * 60) is True
    assert is_open_now("", now_min=12 * 60) is None                           # 未知


_PRICED = {
    "status": "1", "info": "OK",
    "pois": [
        {"id": "A", "name": "贵店", "location": "1,1", "type": "餐饮", "business": {"cost": "120"}},
        {"id": "B", "name": "中店", "location": "1,1", "type": "餐饮", "business": {"cost": "90"}},
        {"id": "C", "name": "便宜店", "location": "1,1", "type": "餐饮", "business": {"cost": "20"}},
        {"id": "D", "name": "无价店", "location": "1,1", "type": "餐饮", "business": {}},
    ],
}


def test_search_price_band_drops_cheap_and_nocost():
    """价位区间 [60,140]：便宜(20<60)与无人均 均剔除（修『左右返回18/30 与无人均』）。"""
    p = _provider({"/v5/place/text": _PRICED})
    res = asyncio.run(p.search("餐厅", price_min=60, price_max=140))
    assert [r.name for r in res] == ["贵店", "中店"]

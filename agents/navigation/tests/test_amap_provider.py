"""高德 AmapPOIProvider 单测：mock 掉底层 HTTP，喂高德黄金响应。

验证 POI 解析 / "lng,lat" 坐标顺序 / 空字段([])归一 / 评分过滤 / status!=1 报错 /
地名先地理编码再周边 / 路线解析。不发真实网络。
"""
import asyncio

import pytest

from agents._sdk.http import ProviderError
from agents.navigation.src.providers.amap import AmapPOIProvider
from agents.navigation.src.providers.base import GeoPoint


def _provider(responses: dict):
    """responses: {path 子串 -> json|Exception}。返回装好 fake get_json 的 provider。"""
    p = AmapPOIProvider(key="test-key")

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
        {"id": "B1", "name": "特来电充电站", "address": "科苑路1号",
         "location": "121.500,31.230", "type": "汽车服务;充电站",
         "distance": "800", "business": {"rating": "4.6"}},
        {"id": "B2", "name": "星星充电", "address": [],  # 高德空字段返回 []
         "location": "121.510,31.240", "type": "汽车服务;充电站",
         "distance": "1200", "business": {"rating": "4.2"}},
    ],
}


def test_search_around_parses_pois():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("充电站", near=GeoPoint(lng=121.5, lat=31.23)))
    assert len(res) == 2
    first = res[0]
    assert first.name == "特来电充电站"
    assert first.lng == 121.500 and first.lat == 31.230  # lng,lat 顺序
    assert first.rating == 4.6
    assert first.distance_km == 0.8   # 800m → 0.8km
    assert res[1].address == ""       # [] 归一成空串


def test_search_text_when_no_location():
    p = _provider({"/v5/place/text": _AROUND_OK})  # 无 near → 关键字检索
    res = asyncio.run(p.search("川菜馆"))
    assert len(res) == 2


def test_search_rating_min_filters():
    p = _provider({"/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("充电站", near=GeoPoint(lng=121.5, lat=31.23), rating_min=4.5))
    assert len(res) == 1 and res[0].rating == 4.6


def test_status_not_one_raises():
    bad = {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}
    p = _provider({"/v5/place/around": bad})
    with pytest.raises(ProviderError):
        asyncio.run(p.search("充电站", near=GeoPoint(lng=121.5, lat=31.23)))


def test_geocode_then_around_for_address_near():
    geo = {"status": "1", "info": "OK", "geocodes": [{"location": "116.397,39.908"}]}
    p = _provider({"/v3/geocode/geo": geo, "/v5/place/around": _AROUND_OK})
    res = asyncio.run(p.search("充电站", near=GeoPoint(address="北京市东城区")))
    assert len(res) == 2  # 地名先地理编码→坐标→周边搜索


def test_get_route_parses():
    route = {"status": "1", "info": "OK",
             "route": {"paths": [{"distance": "12500", "duration": "1500",
                                  "steps": [{"instruction": "直行500米"},
                                            {"instruction": "右转进入科苑路"}]}]}}
    p = _provider({"/v3/direction/driving": route})
    out = asyncio.run(p.get_route(GeoPoint(lng=121.4, lat=31.2), GeoPoint(lng=121.5, lat=31.3)))
    assert out["distance_km"] == 12.5
    assert out["duration_min"] == 25.0
    assert out["steps"] == ["直行500米", "右转进入科苑路"]


def test_reverse_geocode_parses():
    regeo = {"status": "1", "info": "OK",
             "regeocode": {"formatted_address": "上海市浦东新区张江高科技园区"}}
    p = _provider({"/v3/geocode/regeo": regeo})
    pt = asyncio.run(p.reverse_geocode(121.500, 31.230))
    assert pt.address == "上海市浦东新区张江高科技园区"
    assert pt.lng == 121.500 and pt.lat == 31.230


def test_poi_detail_parses():
    detail = {"status": "1", "info": "OK",
              "pois": [{"id": "B1", "name": "特来电充电站", "address": "科苑路1号",
                        "location": "121.500,31.230", "type": "汽车服务;充电站",
                        "business": {"rating": "4.6"}}]}
    p = _provider({"/v5/place/detail": detail})
    poi = asyncio.run(p.poi_detail("B1"))
    assert poi.name == "特来电充电站"
    assert poi.rating == 4.6
    assert poi.lng == 121.500


def test_poi_detail_not_found_raises():
    bad = {"status": "1", "info": "OK", "pois": []}
    p = _provider({"/v5/place/detail": bad})
    with pytest.raises(ProviderError):
        asyncio.run(p.poi_detail("nonexistent"))


# ── 批 7 ②：实时路况——路线逐段 tmcs 聚合 / 路名态势 / 逆地理编码带城市 ─────────────────────

_ROUTE_ALL = {"status": "1", "info": "OK",
              "route": {"paths": [{"distance": "35200", "duration": "2520", "steps": [
                  {"instruction": "直行", "distance": "12000", "polyline": "113.94,22.54;113.95,22.55",
                   "tmcs": [{"status": "畅通", "distance": "9000"},
                            {"status": "缓行", "distance": "3000"}]},
                  {"instruction": "右转", "distance": "23200", "polyline": "113.95,22.55;113.96,22.56",
                   "tmcs": [{"status": "拥堵", "distance": "800"},
                            {"status": "严重拥堵", "distance": "400"},
                            {"status": "畅通", "distance": "21000"},
                            {"status": "神秘", "distance": "1000"}]},
              ]}]}}


def test_get_route_with_polyline_aggregates_traffic_segments():
    p = _provider({"/v3/direction/driving": _ROUTE_ALL})
    out = asyncio.run(p.get_route(GeoPoint(lng=113.94, lat=22.54), GeoPoint(lng=113.96, lat=22.56),
                                  with_polyline=True))
    assert out["traffic"] == {"expedite_km": 30.0, "slow_km": 3.0, "congested_km": 0.8,
                              "blocked_km": 0.4, "unknown_km": 1.0}


def test_get_route_without_tmcs_has_no_traffic_field():
    """**没有就是没有**：base 档（无 tmcs）不得编一份 traffic。"""
    route = {"status": "1", "info": "OK",
             "route": {"paths": [{"distance": "12500", "duration": "1500",
                                  "steps": [{"instruction": "直行500米", "polyline": "1,2;3,4"}]}]}}
    p = _provider({"/v3/direction/driving": route})
    out = asyncio.run(p.get_route(GeoPoint(lng=121.4, lat=31.2), GeoPoint(lng=121.5, lat=31.3),
                                  with_polyline=True))
    assert "traffic" not in out
    plain = asyncio.run(p.get_route(GeoPoint(lng=121.4, lat=31.2), GeoPoint(lng=121.5, lat=31.3)))
    assert "traffic" not in plain


def test_road_traffic_parses_evaluation():
    body = {"status": "1", "info": "OK",
            "trafficinfo": {"description": "深南大道：整体畅通",
                            "evaluation": {"expedite": "88.50%", "congested": "8.00%",
                                           "blocked": "0.00%", "unknown": "3.50%",
                                           "status": "1", "description": "整体畅通"}}}
    p = _provider({"/v3/traffic/status/road": body})
    out = asyncio.run(p.road_traffic("深南大道", "440300"))
    assert out == {"name": "深南大道", "status": 1, "description": "整体畅通",
                   "expedite_pct": 88.5, "congested_pct": 8.0, "blocked_pct": 0.0,
                   "unknown_pct": 3.5}


def test_road_traffic_without_evaluation_or_city_raises():
    p = _provider({"/v3/traffic/status/road": {"status": "1", "info": "OK", "trafficinfo": {}}})
    with pytest.raises(ProviderError):
        asyncio.run(p.road_traffic("深南大道", "440300"))
    with pytest.raises(ProviderError):
        asyncio.run(p.road_traffic("深南大道", ""))


def test_reverse_geocode_carries_city_and_adcode_and_falls_back_to_province_for_municipalities():
    regeo = {"status": "1", "info": "OK",
             "regeocode": {"formatted_address": "广东省深圳市南山区科技园",
                           "addressComponent": {"province": "广东省", "city": "深圳市",
                                                "adcode": "440305"}}}
    p = _provider({"/v3/geocode/regeo": regeo})
    pt = asyncio.run(p.reverse_geocode(113.94, 22.54))
    assert (pt.city, pt.adcode) == ("深圳市", "440305")
    municipality = {"status": "1", "info": "OK",
                    "regeocode": {"formatted_address": "北京市朝阳区",
                                  "addressComponent": {"province": "北京市", "city": [],
                                                       "adcode": "110105"}}}
    p = _provider({"/v3/geocode/regeo": municipality})
    pt = asyncio.run(p.reverse_geocode(116.4, 39.9))
    assert (pt.city, pt.adcode) == ("北京市", "110105")

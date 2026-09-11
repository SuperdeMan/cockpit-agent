"""路线几何 → 卡片字段（2026-09-11，Android 地图页画路线）。纯函数守卫 + provider / agent 两处消费点。"""
import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import GeoPoint, POI
from agents.navigation.src.route_geometry import (
    PATH_MAX_POINTS, card_geometry, parse_amap_polyline, route_path_from_amap, sample_path)
from agents.navigation.tests.test_amap_provider import _provider


def test_parse_amap_polyline_flips_to_lat_lng_and_skips_junk():
    """高德是 "lng,lat"，卡片是 [lat, lng]；坏段与 0,0 跳过不抛。"""
    assert parse_amap_polyline("121.4,31.2;121.5,31.3;junk;0,0;121.6,31.4") == \
        [[31.2, 121.4], [31.3, 121.5], [31.4, 121.6]]
    assert parse_amap_polyline("") == []


def test_sample_path_keeps_ends_and_caps():
    pts = [[float(i), float(i)] for i in range(1000)]
    out = sample_path(pts, cap=50)
    assert len(out) == 50 and out[0] == [0.0, 0.0] and out[-1] == [999.0, 999.0]
    assert sample_path(pts[:10], cap=50) == pts[:10]          # 不足上限原样


def test_route_path_from_amap_concatenates_steps_dedups_joints_and_rounds():
    """每步末点 = 下一步首点，只留一份；抽样到 ≤240、5 位小数。"""
    path = {"steps": [
        {"polyline": "121.400001,31.200001;121.41,31.21"},
        {"polyline": "121.41,31.21;121.42,31.22"},
    ]}
    assert route_path_from_amap(path) == [[31.2, 121.4], [31.21, 121.41], [31.22, 121.42]]
    long = {"steps": [{"polyline": ";".join(f"{121 + i / 10000},{31 + i / 10000}" for i in range(2000))}]}
    out = route_path_from_amap(long)
    assert len(out) == PATH_MAX_POINTS and out[0] == [31.0, 121.0]


def test_card_geometry_writes_only_what_it_has():
    """起终点缺席不写键；途经点没坐标只写 name/address；折线空不写 path。"""
    assert card_geometry() == {}
    g = card_geometry(origin=GeoPoint(lat=31.2, lng=121.4),
                      destination={"lat": 31.3, "lng": 121.5},
                      waypoints=[POI(id="a", name="A", address="x", lat=31.25, lng=121.45),
                                 {"name": "B", "address": "y"}],
                      path=[[31.2, 121.4], [31.3, 121.5]])
    assert g["origin_loc"] == {"lat": 31.2, "lng": 121.4}
    assert g["destination_loc"] == {"lat": 31.3, "lng": 121.5}
    assert g["waypoints"] == [{"name": "A", "address": "x", "lat": 31.25, "lng": 121.45},
                              {"name": "B", "address": "y"}]
    assert g["path"] == [[31.2, 121.4], [31.3, 121.5]]
    # 0,0 与 None 都算缺席（几内亚湾那条）
    assert "origin_loc" not in card_geometry(origin=GeoPoint(lat=0, lng=0))
    assert "destination_loc" not in card_geometry(destination=GeoPoint(address="只有地址"))
    assert "path" not in card_geometry(path=[])


def test_amap_provider_get_route_with_polyline_returns_path():
    """extensions=all 的 steps[].polyline → result["path"]；不要 polyline 时没有这个键。"""
    route = {"status": "1", "info": "OK",
             "route": {"paths": [{"distance": "12500", "duration": "1500",
                                  "steps": [{"instruction": "直行", "distance": "500",
                                             "polyline": "121.4,31.2;121.41,31.21"},
                                            {"instruction": "右转", "distance": "700",
                                             "polyline": "121.41,31.21;121.42,31.22"}]}]}}
    p = _provider({"/v3/direction/driving": route})
    out = asyncio.run(p.get_route(GeoPoint(lng=121.4, lat=31.2), GeoPoint(lng=121.5, lat=31.3),
                                  with_polyline=True))
    assert out["path"] == [[31.2, 121.4], [31.21, 121.41], [31.22, 121.42]]
    assert out["points"][-1]["cum_km"] == 1.2
    plain = asyncio.run(p.get_route(GeoPoint(lng=121.4, lat=31.2), GeoPoint(lng=121.5, lat=31.3)))
    assert "path" not in plain


class _GeoRouteProvider:
    """脚本化 provider：搜索固定命中；get_route 记录 with_polyline，要折线时给折线。"""

    def __init__(self):
        self.route_calls = []

    async def search(self, keyword, **kwargs):
        return [POI(id="d", name="深圳湾公园", address="南山区", lat=22.52, lng=113.94)]

    async def get_route(self, origin, destination, meta=None, with_polyline=False,
                        waypoints=None, strategy=""):
        self.route_calls.append(with_polyline)
        route = {"distance_km": 8.0, "duration_min": 20, "steps": []}
        if with_polyline:
            route["path"] = [[origin.lat, origin.lng], [22.51, 113.93], [destination.lat, destination.lng]]
        return route


def test_navigate_route_plan_card_carries_geometry():
    """普通导航的 route_plan 卡带起终点坐标与折线；provider 被要求 with_polyline。"""
    agent = NavigationAgent()
    agent.poi = _GeoRouteProvider()
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "深圳湾公园"},
        raw_text="导航去深圳湾公园", ctx=make_context(),
        meta={"current_lat": "22.5", "current_lng": "113.9"}))
    card = res.ui_card
    assert card["type"] == "route_plan"
    assert card["origin_loc"] == {"lat": 22.5, "lng": 113.9}
    assert card["destination_loc"] == {"lat": 22.52, "lng": 113.94}
    assert card["path"][0] == [22.5, 113.9] and card["path"][-1] == [22.52, 113.94]
    assert agent.poi.route_calls and agent.poi.route_calls[0] is True
    # 既有字段一个不少（HMI 只读这些）
    assert card["waypoints"] == [] and card["destination"] == "深圳湾公园"


def test_navigate_without_current_location_has_no_geometry_but_destination():
    """拿不到当前位置 ⇒ 算不了路、没有折线；终点坐标照旧写（地图页至少能标终点）。"""
    agent = NavigationAgent()
    agent.poi = _GeoRouteProvider()
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "深圳湾公园"},
        raw_text="导航去深圳湾公园", ctx=make_context()))
    card = res.ui_card
    assert card["type"] == "route_plan"
    assert "path" not in card and "origin_loc" not in card
    assert card["destination_loc"] == {"lat": 22.52, "lng": 113.94}

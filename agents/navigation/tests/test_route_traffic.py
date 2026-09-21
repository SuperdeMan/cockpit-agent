"""`navigation.route_traffic`（内部意图，批 7 ②）：实时路况的三种入口与诚实降级。

此前 `safety.road_condition` 拿「X 路况」搜 POI，空结果播「为您找到 0 个X 路况，推荐前三个：。
需要导航过去吗？」——名字存在能力不可达。这里接的是高德一直在响应里、一直被丢掉的逐段 tmcs
与路名态势接口。三条主张：**永不产出 navigate**；**数据缺席就说拿不到**；**不进 manifest**。
"""
import asyncio
import json
import time

import yaml

from agents._sdk.http import ProviderError
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import GeoPoint, POI

_HERE = {"current_lat": "22.5410", "current_lng": "113.9412"}
_AIRPORT = POI(id="d1", name="深圳宝安国际机场", address="宝安区", lat=22.6393, lng=113.8107)
_TRAFFIC = {"expedite_km": 30.0, "slow_km": 3.0, "congested_km": 0.8, "blocked_km": 0.4,
            "unknown_km": 1.0}


def _agent(search_results=None, route=None, road_status=None, regeo=None, route_error=None):
    agent = NavigationAgent()
    calls = {"search": [], "route": [], "road": [], "regeo": []}

    async def search(keyword, near=None, **kwargs):
        calls["search"].append(keyword)
        hits = search_results or {}
        return list(hits.get(keyword, [])) if isinstance(hits, dict) else list(hits)

    async def get_route(a, b, **kwargs):
        calls["route"].append((a, b, kwargs))
        if route_error:
            raise route_error
        return dict(route if route is not None else
                    {"distance_km": 35.2, "duration_min": 42, "traffic": dict(_TRAFFIC)})

    async def road_traffic(name, city, meta=None):
        calls["road"].append((name, city))
        if isinstance(road_status, Exception):
            raise road_status
        return dict(road_status or {"name": name, "status": 1, "description": "整体畅通",
                                    "expedite_pct": 88.5, "congested_pct": 8.0,
                                    "blocked_pct": 0.0, "unknown_pct": 3.5})

    async def reverse_geocode(lng, lat, meta=None):
        calls["regeo"].append((lng, lat))
        return regeo if regeo is not None else GeoPoint(lat=lat, lng=lng, address="广东省深圳市南山区",
                                                         city="深圳市", adcode="440305")

    agent.poi.search = search
    agent.poi.get_route = get_route
    agent.poi.road_traffic = road_traffic
    agent.poi.reverse_geocode = reverse_geocode
    return agent, calls


def _traffic(agent, slots, raw_text="路况怎么样", meta=None):
    return asyncio.run(run_handle(agent, "navigation.route_traffic", slots=slots,
                                  raw_text=raw_text, ctx=make_context(),
                                  meta=dict(meta if meta is not None else _HERE)))


def test_route_traffic_is_an_internal_intent_not_in_the_manifest():
    """进 manifest 就是在 planner 面前摆两个等价工具（与 safety.road_condition 掷硬币）。"""
    with open("agents/navigation/manifest.yaml", encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle)
    intents = {cap["intent"] for cap in manifest["capabilities"]}
    assert "navigation.route_traffic" not in intents
    agent, _ = _agent()
    res = _traffic(agent, {"road": "深南大道"})
    assert "还不会处理" not in res.speech, "handle() 的处理表要认这条内部意图"


def test_destination_traffic_reports_distance_duration_and_congestion_and_never_navigates():
    agent, calls = _agent({"宝安机场": [_AIRPORT]})
    res = _traffic(agent, {"destination": "宝安机场"}, "去宝安机场的路况")
    assert res.status == "ok" and not res.actions
    assert "35.2公里" in res.speech and "42分钟" in res.speech
    assert "缓行约3.0公里" in res.speech and "拥堵约0.8公里" in res.speech and "严重拥堵约0.4公里" in res.speech
    for junk in ("为您找到", "推荐前三个", "需要导航过去吗"):
        assert junk not in res.speech
    assert res.data["traffic"] == _TRAFFIC and res.data["traffic_source"] == "route_tmcs"
    assert res.ui_card["type"] == "route_plan" and res.ui_card["estimate"] is True
    assert res.ui_card["traffic"] == _TRAFFIC
    _a, _b, kwargs = calls["route"][0]
    assert kwargs.get("with_polyline") is True, "tmcs 只在 extensions=all 里"


def test_clear_route_says_so():
    agent, _ = _agent({"宝安机场": [_AIRPORT]},
                      route={"distance_km": 35.2, "duration_min": 42,
                             "traffic": {"expedite_km": 35.0, "slow_km": 0.0, "congested_km": 0.04,
                                         "blocked_km": 0.0, "unknown_km": 0.2}})
    res = _traffic(agent, {"destination": "宝安机场"})
    assert "沿途基本畅通" in res.speech and "拥堵" not in res.speech


def test_missing_tmcs_is_reported_as_unavailable_not_invented():
    agent, _ = _agent({"宝安机场": [_AIRPORT]}, route={"distance_km": 35.2, "duration_min": 42})
    res = _traffic(agent, {"destination": "宝安机场"})
    assert "35.2公里" in res.speech and "实时拥堵数据这会儿拿不到" in res.speech
    assert res.data["traffic"] == {} and res.data["traffic_source"] == ""
    assert "traffic" not in res.ui_card


def test_no_destination_falls_back_to_the_active_route():
    agent, calls = _agent()
    active = {"destination": "深圳宝安国际机场", "lat": 22.6393, "lng": 113.8107,
              "ts": int(time.time()) - 60, "waypoints": [{"name": "加油站", "lat": 22.60, "lng": 113.90}]}
    res = _traffic(agent, {}, meta={**_HERE, "focus_active_route": json.dumps(active, ensure_ascii=False)})
    assert res.status == "ok" and "深圳宝安国际机场" in res.speech
    assert calls["search"] == [], "活动路线自带坐标，不该再搜地点"
    _a, dest, kwargs = calls["route"][0]
    assert (dest.lat, dest.lng) == (22.6393, 113.8107)
    assert len(kwargs.get("waypoints") or []) == 1


def test_no_destination_and_no_active_route_asks_for_one():
    agent, calls = _agent()
    res = _traffic(agent, {})
    assert res.status == "need_slot" and "destination" in res.missing_slots
    assert calls["route"] == []


def test_without_position_it_says_so_instead_of_guessing_an_origin():
    agent, calls = _agent({"宝安机场": [_AIRPORT]})
    res = _traffic(agent, {"destination": "宝安机场"}, meta={})
    assert res.status == "ok" and "拿不到车辆的位置" in res.speech
    assert res.data["traffic_lookup"] == "no_position" and calls["route"] == []


def test_provider_failure_is_honest():
    agent, _ = _agent({"宝安机场": [_AIRPORT]}, route_error=ProviderError("amap down"))
    res = _traffic(agent, {"destination": "宝安机场"})
    assert res.status == "ok" and "查不到" in res.speech and not res.actions
    assert res.data["traffic_lookup"] == "provider_error"


def test_road_name_uses_the_current_city_from_reverse_geocode():
    agent, calls = _agent()
    res = _traffic(agent, {"road": "深南大道"}, "深南大道路况")
    assert res.status == "ok" and not res.actions
    assert calls["regeo"] == [(113.9412, 22.541)]
    assert calls["road"] == [("深南大道", "440305")], "adcode 优先于城市名"
    assert res.speech.startswith("深南大道目前整体畅通") and "畅通路段约占88.5%" in res.speech
    assert res.data["traffic_source"] == "road_status" and res.data["status"] == 1


def test_road_name_without_position_cannot_pick_a_city():
    agent, calls = _agent()
    res = _traffic(agent, {"road": "深南大道"}, meta={})
    assert "没法确定是哪个城市的深南大道" in res.speech and calls["road"] == []


def test_road_name_provider_failure_is_honest():
    agent, _ = _agent(road_status=ProviderError("not supported"))
    res = _traffic(agent, {"road": "深南大道"})
    assert res.speech == "暂时查不到深南大道的实时路况。"

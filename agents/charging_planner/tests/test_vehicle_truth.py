"""CA2-19 S1：能源链不编车况——读不到电量就如实说、不判断够不够；0% 是合法读数；读数连同来源上卡片。"""
from __future__ import annotations

import asyncio
import time

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.charging_planner.src.agent import ChargingPlannerAgent
from agents.charging_planner.src.low_battery import LowBatteryWatcher
from agents.charging_planner.src.providers.amap import AmapChargingProvider
from agents.charging_planner.src.providers.base import ChargingStation
from agents.charging_planner.src.providers.mock import MockChargingProvider
from agents.navigation.src.providers.base import POI
from runtime.vehicle_reading import Reading

LOC = {"current_lat": "22.5", "current_lng": "113.8"}


def _amap(distance_km=870.0):
    provider = AmapChargingProvider(key="test-key")

    async def route(o, d, meta=None, with_polyline=False):
        return {"distance_km": distance_km, "duration_min": 600.0, "steps": [],
                "points": [{"lng": 114.0, "lat": 23.0, "cum_km": 250.0},
                           {"lng": 116.0, "lat": 24.0, "cum_km": 600.0},
                           {"lng": 118.1, "lat": 24.46, "cum_km": distance_km}]}

    async def search(keyword, near=None, **kw):
        return [POI(id="s", name=f"沿途充电站{near.lat}", address="服务区", lat=near.lat, lng=near.lng)]

    provider._poi.get_route = route
    provider._poi.search = search
    return provider


def test_unknown_soc_plan_lists_stations_without_judging_range():
    plan = asyncio.run(_amap().plan_route("厦门火车站", soc=None, meta=LOC))
    assert "没读到当前电量" in plan.summary and "没法判断够不够直达" in plan.summary
    assert "足够直达" not in plan.summary and "补电 " not in plan.summary and "50%" not in plan.summary
    assert plan.stops and all("charge_to" not in s for s in plan.stops)   # 只作参考，不建议充到多少


def test_short_route_with_unknown_soc_says_so_without_stations():
    plan = asyncio.run(_amap(distance_km=120.0).plan_route("近郊", soc=None, meta=LOC))
    assert "没读到当前电量" in plan.summary and plan.stops == []


def test_zero_percent_is_a_real_reading_not_fifty():
    plan = asyncio.run(_amap().plan_route("厦门火车站", soc=0, meta=LOC, soc_note="（模拟车读数）"))
    assert "当前电量0%（模拟车读数）" in plan.summary and "估算约0公里续航" in plan.summary
    assert "建议途中补电" in plan.summary


def test_known_soc_names_its_source_and_marks_range_as_estimate():
    plan = asyncio.run(_amap(distance_km=120.0).plan_route("近郊", soc=90, meta=LOC, soc_note="（模拟车读数）"))
    assert "当前电量90%（模拟车读数），估算约450公里续航，足够直达" in plan.summary


def test_mock_provider_unknown_soc_is_honest():
    plan = asyncio.run(MockChargingProvider().plan_route("苏州", soc=None))
    assert "没读到当前电量" in plan.summary and "50%" not in plan.summary and plan.stops == []


def test_agent_plan_without_a_reading_hides_the_soc_bar_and_says_so():
    agent = ChargingPlannerAgent()
    agent.charging = _amap()
    res = asyncio.run(run_handle(agent, "charging.plan", slots={"destination": "厦门火车站"},
                                 raw_text="去厦门要充几次电", ctx=make_context(), meta=dict(LOC)))
    assert "没读到当前电量" in res.speech
    assert res.ui_card["soc"] == "" and res.ui_card["soc_note"] == "没读到"


def test_agent_plan_with_a_simulated_reading_carries_its_source():
    agent = ChargingPlannerAgent()
    agent.charging = _amap()
    ctx = make_context(context_values={"vehicle.battery": "35"})
    res = asyncio.run(run_handle(agent, "charging.plan", slots={"destination": "厦门火车站"},
                                 raw_text="去厦门要充几次电", ctx=ctx, meta=dict(LOC)))
    assert "当前电量35%（模拟车读数）" in res.speech
    assert res.ui_card["soc"] == "35%" and res.ui_card["soc_note"] == "模拟车读数"
    assert res.ui_card["soc_source"]["kind"] == "simulated"


def test_status_without_a_reading_does_not_report_a_number():
    res = asyncio.run(run_handle(ChargingPlannerAgent(), "charging.status", raw_text="还有多少电",
                                 ctx=make_context()))
    assert "没读到" in res.speech and not any(ch.isdigit() for ch in res.speech)
    assert res.data["battery"] == "" and res.data["battery_note"] == "没读到"


def test_reading_needs_the_vehicle_state_grant():
    ctx = make_context(context_values={"vehicle.battery": "35"}, granted_permissions=("location.read",))
    assert ctx.vehicle_reading("battery").known is False


def test_charging_list_card_carries_station_provenance():
    agent = ChargingPlannerAgent()

    async def nearby(point, charger_type="", meta=None):
        return [ChargingStation(id="s1", name="国网充电站", distance_km=1.2)]

    agent.charging.find_nearby = nearby
    res = asyncio.run(run_handle(agent, "charging.find", raw_text="附近的充电站",
                                 ctx=make_context(context_values={"vehicle.battery": "40"}), meta=dict(LOC)))
    assert res.ui_card["type"] == "charging_list" and "_prov" in res.ui_card
    assert res.ui_card["soc"] == "40%" and res.ui_card["soc_note"] == "模拟车读数"


class _Cap:
    def __init__(self):
        self.sent = []

    async def publish(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_low_battery_alert_names_its_source_and_stamps_the_station_card():
    cap = _Cap()

    async def find(_point):
        return [ChargingStation(id="s1", name="国网充电站", distance_km=1.2)]

    watcher = LowBatteryWatcher(cap.publish, find, now_fn=lambda: 1000.0,
                                attach_prov=lambda card: {**card, "_prov": {"vendor": "amap"}})
    state = {"battery": 18, "location": {"lat": 39.9, "lng": 116.4}}
    battery = Reading("battery", 18, time.time() * 1000, "simulated")
    assert await watcher.on_state([], state, battery) is True
    payload = cap.sent[0]
    assert "电量只剩 18%（模拟车读数） 了" in payload["speech"]
    assert payload["card"]["_prov"] == {"vendor": "amap"} and payload["card"]["soc_note"] == "模拟车读数"


# ─── CA2-19 S3：选站可追溯——到站估算电量、选择理由、空闲状态未知如实标 ───

def test_plan_stops_carry_arrival_estimate_and_reason():
    plan = asyncio.run(_amap().plan_route("厦门火车站", soc=72, meta=LOC))
    assert plan.stops
    first = plan.stops[0]
    # 72% × 500km = 360km 可用，首段放在 85% 处（306km），到站估算 72 − 306/500×100 ≈ 11%
    assert first["arrive_soc"] == 11 and "约剩11%" in first["reason"] and "补到80%" in first["reason"]
    assert plan.arrive_soc is not None and f"到达时估算剩余约{plan.arrive_soc}%" in plan.summary


def test_direct_plan_states_the_arrival_estimate():
    plan = asyncio.run(_amap(distance_km=120.0).plan_route("近郊", soc=90, meta=LOC))
    assert plan.stops == [] and plan.arrive_soc == 66 and "到达时估算剩余约66%" in plan.summary


def test_unknown_soc_has_no_estimates_at_all():
    plan = asyncio.run(_amap().plan_route("厦门火车站", soc=None, meta=LOC))
    assert plan.arrive_soc is None and all("arrive_soc" not in s for s in plan.stops)


def test_agent_card_and_data_carry_the_estimates():
    agent = ChargingPlannerAgent()
    agent.charging = _amap()
    ctx = make_context(context_values={"vehicle.battery": "72"})
    res = asyncio.run(run_handle(agent, "charging.plan", slots={"destination": "厦门火车站"},
                                 raw_text="去厦门要充几次电", ctx=ctx, meta=dict(LOC)))
    assert res.ui_card["arrive_soc"] == res.data["arrive_soc"]
    assert res.ui_card["stops"][0]["arrive_soc"] == 11 and res.ui_card["stops"][0]["reason"]


def test_destination_station_choice_is_explained_and_availability_marked_unknown():
    agent = ChargingPlannerAgent()

    async def nearby(point, charger_type="", meta=None):
        return [ChargingStation(id="s1", name="国网充电站", distance_km=0.8, lat=24.4, lng=118.1),
                ChargingStation(id="s2", name="南网充电站", distance_km=1.6, lat=24.5, lng=118.2)]

    agent.charging.find_nearby = nearby
    res = asyncio.run(run_handle(agent, "charging.find", slots={"destination": "厦门火车站站前广场"},
                                 raw_text="厦门火车站附近找个充电站", ctx=make_context(), meta=dict(LOC)))
    assert res.data["choice_reason"] == "离目的地最近（空闲状态未知）"
    assert all(item["availability_known"] is False for item in res.data["items"])
    assert set(res.data["waypoint"]) == {"name", "address", "lat", "lng"}     # 途经点不夹带新字段进导航载荷


def test_destination_search_alone_does_not_claim_a_route_was_changed():
    """单独问「X附近有充电站吗」时没有导航步、没有路线——此前说「已为前往X的路线加入途经充电站」。"""
    agent = ChargingPlannerAgent()

    async def nearby(point, charger_type="", meta=None):
        return [ChargingStation(id="s1", name="国网充电站", distance_km=0.8, lat=24.4, lng=118.1)]

    agent.charging.find_nearby = nearby
    res = asyncio.run(run_handle(agent, "charging.find", slots={"destination": "厦门火车站站前广场"},
                                 raw_text="厦门火车站附近有充电站吗", ctx=make_context(), meta=dict(LOC)))
    assert "国网充电站" in res.speech and "已为" not in res.speech and "加入途经" not in res.speech
    assert res.actions == []

"""CA2-19 换站：正在导航且路线上有途经点时，「换一个充电站」零播报改派给导航改路线；没有活动路线照旧找站。"""
from __future__ import annotations

import asyncio
import json
import time

from agents._sdk.testing import make_context, run_handle
from agents.charging_planner.src.agent import ChargingPlannerAgent
from agents.charging_planner.src.providers.base import ChargingStation
from runtime.charger_swap import asks_to_swap_charger

LOC = {"current_lat": "22.5", "current_lng": "113.8"}
_STATION = {"name": "国网充电站", "lat": 22.61, "lng": 114.03}


def _route(waypoints) -> str:
    return json.dumps({"destination": "深圳北站", "lat": 22.609, "lng": 114.029,
                       "waypoints": waypoints, "ts": int(time.time())}, ensure_ascii=False)


def _agent():
    agent = ChargingPlannerAgent()
    calls = []

    async def nearby(point, charger_type="", meta=None):
        calls.append(point)
        return [ChargingStation(id="s1", name=_STATION["name"], distance_km=0.8,
                                lat=_STATION["lat"], lng=_STATION["lng"])]

    agent.charging.find_nearby = nearby
    return agent, calls


def _find(agent, raw_text, meta):
    return asyncio.run(run_handle(agent, "charging.find", slots={"destination": "深圳北站"},
                                  raw_text=raw_text, ctx=make_context(), meta=meta))


def test_swap_on_an_active_route_is_handed_to_navigation():
    agent, calls = _agent()
    res = _find(agent, "换一个充电站", {**LOC, "focus_active_route": _route([_STATION])})
    assert res.speech == ""
    assert res.data["_escalate"] == {"intent": "navigation.reroute", "slots": {},
                                     "reason": "swap_route_charger"}
    assert not calls          # 不自己再搜一遍、再推荐同一个站


def test_without_a_route_or_without_stops_it_finds_stations_as_before():
    for meta in (dict(LOC), {**LOC, "focus_active_route": _route([])}):
        agent, calls = _agent()
        res = _find(agent, "换一个充电站", meta)
        assert "_escalate" not in (res.data or {}) and "国网充电站" in res.speech and calls


def test_a_plain_station_search_on_an_active_route_is_not_handed_off():
    agent, calls = _agent()
    res = _find(agent, "深圳北站附近还有哪些充电站", {**LOC, "focus_active_route": _route([_STATION])})
    assert "_escalate" not in (res.data or {}) and calls


def test_the_follow_up_hint_is_itself_a_working_swap_request():
    """手机端把追问提示整句做成 chip、点按原样发出；用户也会照着引号里那句说——两种都得是认得出的换站说法。
    此前提示教用户说『换一个』，裸「换一个」认不出是换充电站。"""
    agent, _ = _agent()
    res = _find(agent, "深圳北站附近找个充电站", dict(LOC))
    quoted = res.follow_up.split("『", 1)[1].split("』", 1)[0]
    assert asks_to_swap_charger(res.follow_up) and asks_to_swap_charger(quoted)

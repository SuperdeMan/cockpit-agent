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


def _kv_ctx():
    """`make_context` 的共享态是不真存的替身——钉进内存 dict。"""
    kv = {}
    ctx = make_context()

    async def save(key, value):
        kv[key] = value
        return True

    async def load(key):
        return kv.get(key)

    ctx.save_shared_state, ctx.load_shared_state = save, load
    return ctx, kv


def _stations_agent(names):
    agent = ChargingPlannerAgent()

    async def nearby(point, charger_type="", meta=None):
        return [ChargingStation(id=f"s{i}", name=name, distance_km=0.5 * (i + 1), lat=22.5 + 0.01 * i, lng=113.9)
                for i, name in enumerate(names)]

    agent.charging.find_nearby = nearby
    return agent


def test_without_a_route_a_swap_skips_the_stations_already_recommended():
    """CA2-19 §9.1：没有活动路线时「换一个充电站」照常找站，又推荐了同一个站。推荐过的这一会话里记着，换站时排除、
    连着换一直往后推，都推荐过了就如实说；新的找站从头推荐。"""
    agent = _stations_agent(["A站", "B站", "C站", "D站", "E站"])
    ctx, _ = _kv_ctx()

    def ask(text):
        return asyncio.run(run_handle(agent, "charging.find", slots={}, raw_text=text, ctx=ctx, meta=dict(LOC)))

    assert "推荐：A站" in ask("附近有充电站吗").speech
    second = ask("换一个充电站")
    assert "推荐：D站" in second.speech and "A站" not in second.speech and "C站" not in second.speech
    assert "都推荐过了" in ask("换一个充电站").speech
    assert "推荐：A站" in ask("附近有充电站吗").speech


def test_a_swap_near_a_destination_picks_the_next_station():
    """按目的地找站推荐的是一个站：「换一个充电站」（没有活动路线，目的地取自焦点）换成下一个。"""
    agent = _stations_agent(["A站", "B站"])
    ctx, _ = _kv_ctx()

    def ask(text):
        return asyncio.run(run_handle(agent, "charging.find", slots={"destination": "深圳北站"}, raw_text=text,
                                      ctx=ctx, meta=dict(LOC)))

    assert "A站" in ask("深圳北站附近找个充电站").speech
    swapped = ask("换一个充电站")
    assert "B站" in swapped.speech and "A站" not in swapped.speech
    assert "都推荐过了" in ask("换一个充电站").speech


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


def _swap_hint():
    from agents._sdk.manifest import load_manifest
    from pathlib import Path

    manifest = load_manifest(str(Path(__file__).resolve().parents[1] / "manifest.yaml"))
    return next(h for h in manifest.route_hints if h.intent == "charging.find")


_SWAP_SAYINGS = ["换一个充电站", "换个充电桩吧", "帮我换一家快充站", "能不能换个别的充电站", "换个地方充电", "换一个超充站。"]
_NOT_THE_HINT = ["这个充电站不行，换一家", "导航去深圳北站，在附近找个充电桩", "换一条路", "附近有换电站吗",
                 "换个桩", "把充电站换成特来电", "别换充电站", "为什么换一个充电站", "换个充电站然后放首歌"]


def test_the_swap_hint_is_a_subset_of_the_predicate():
    """充电 manifest 里的换站路由提示是 `asks_to_swap_charger` 的子集：提示命中的说法，导航与充电的换站判据一定认得——
    提示只负责把整句送到充电，换不换由那一份判据决定（声明源只有一份，YAML 里的正则按它对账）。"""
    import re

    pattern = re.compile(_swap_hint().pattern)
    for text in _SWAP_SAYINGS:
        assert pattern.search(text), text
        assert asks_to_swap_charger(text), text
    for text in _NOT_THE_HINT:
        assert not pattern.search(text), text
    for text in _SWAP_SAYINGS + _NOT_THE_HINT:
        if pattern.search(text):
            assert asks_to_swap_charger(text), text


def test_the_swap_hint_replaces_a_talk_only_plan():
    """规划器判「无需动作」交出闲聊时，提示把整句换成 charging.find（由它决定改派换站还是找站）。"""
    from types import SimpleNamespace
    from agents._sdk.manifest import load_manifest
    from orchestrator.cloud.models import Plan, Step
    from orchestrator.cloud.route_hints import RouteHintEngine
    from pathlib import Path

    manifest = load_manifest(str(Path(__file__).resolve().parents[1] / "manifest.yaml"))
    amap = {"charging-planner": SimpleNamespace(manifest=manifest, endpoint="x:0")}
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk")])
    assert RouteHintEngine(lambda raws, agent_map: [_step_from(item) for item in raws]).apply(
        plan, "换一个充电站", amap) is True
    assert [s.intent for s in plan.steps] == ["charging.find"]


def _step_from(raw):
    from orchestrator.cloud.models import Step
    return Step(id=raw["id"], agent_id=raw["agent_id"], intent=raw["intent"], slots=dict(raw["slots"]))

"""G8 navigation.reroute：增量改道（删/加途经点、换路线、改目的地）+ 路线会话保留键。

不起 gRPC，直接驱动 handle。活动路线经 meta.focus_active_route（engine 注入的
服务端事实）传入；断言覆盖「未点名的约束保持」与降级面。
"""
import asyncio
import json
import time

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI


def _session_meta(waypoints=None, strategy="", arrive_by_ts=None, ts=None,
                  destination="万象天地", lat=22.53, lng=113.95, **extra):
    session = {"destination": destination, "lat": lat, "lng": lng,
               "waypoints": waypoints or [], "strategy": strategy,
               "ts": int(ts if ts is not None else time.time())}
    if arrive_by_ts:
        session["arrive_by_ts"] = int(arrive_by_ts)
    return {"focus_active_route": json.dumps(session, ensure_ascii=False),
            "current_lat": "22.54", "current_lng": "113.93", **extra}


def _agent(search_results=None, route=None):
    agent = NavigationAgent()
    calls = {"search": [], "route": []}

    async def search(keyword, near=None, **kwargs):
        calls["search"].append((keyword, near))
        return list(search_results or [])

    async def get_route(a, b, **kwargs):
        calls["route"].append(kwargs)
        return dict(route or {"distance_km": 12.5, "duration_min": 25})

    agent.poi.search = search
    agent.poi.get_route = get_route
    return agent, calls


_KFC = {"name": "肯德基(海岸城店)", "lat": 22.52, "lng": 113.94}
_SBUX = {"name": "星巴克(保利店)", "lat": 22.51, "lng": 113.96}


def test_reroute_without_session_degrades():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="刚才那个途经点不去了",
        ctx=make_context(), meta={}))
    assert res.status == "ok"
    assert "没有正在进行的导航" in res.speech
    assert not res.actions


def test_reroute_expired_session_degrades():
    agent, _ = _agent()
    meta = _session_meta(waypoints=[_KFC], ts=time.time() - 8 * 3600)
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="肯德基不去了",
        ctx=make_context(), meta=meta))
    assert "没有正在进行的导航" in res.speech
    assert not res.actions


def test_reroute_remove_waypoint_by_name():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="肯德基不去了",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC, _SBUX])))
    assert res.status == "ok"
    assert "已去掉途经点肯德基(海岸城店)" in res.speech
    nav = next(a for a in res.actions if a["type"] == "navigate")
    names = [w["name"] for w in nav["payload"].get("waypoints", [])]
    assert names == ["星巴克(保利店)"]          # 未点名的途经点保持
    assert nav["payload"]["destination"] == "万象天地"   # 目的地保持
    # 会话滚动更新：新 _route_session 只剩星巴克
    session = res.data["_route_session"]
    assert [w["name"] for w in session["waypoints"]] == ["星巴克(保利店)"]


def test_reroute_remove_generic_pops_latest():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={},
        raw_text="刚才那个途经点不去了",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC, _SBUX])))
    assert "已去掉途经点星巴克(保利店)" in res.speech   # 泛指 → 删最近加入
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert [w["name"] for w in nav["payload"]["waypoints"]] == ["肯德基(海岸城店)"]


def test_reroute_add_waypoint_prepend():
    station = POI(id="gas-1", name="中石化加油站", lat=22.55, lng=113.92)
    agent, calls = _agent(search_results=[station])
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={},
        raw_text="先去加油站，别迟到",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC])))
    assert "已顺路加上中石化加油站" in res.speech
    assert calls["search"][0][0] == "加油站"
    # 「先」语义：插到途经点首位；既有途经点保持
    session = res.data["_route_session"]
    assert [w["name"] for w in session["waypoints"]] == \
        ["中石化加油站", "肯德基(海岸城店)"]


def test_reroute_remove_and_add_in_one_turn():
    """EVA 动态重规划组：「咖啡不买了，先去加油站」——删+加一轮完成。"""
    station = POI(id="gas-1", name="中石化加油站", lat=22.55, lng=113.92)
    agent, _ = _agent(search_results=[station])
    coffee = {"name": "瑞幸咖啡(海岸城店)", "lat": 22.52, "lng": 113.94}
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={},
        raw_text="咖啡不买了，先去加油站，别迟到",
        ctx=make_context(), meta=_session_meta(waypoints=[coffee])))
    assert "已去掉途经点瑞幸咖啡(海岸城店)" in res.speech
    assert "已顺路加上中石化加油站" in res.speech
    session = res.data["_route_session"]
    assert [w["name"] for w in session["waypoints"]] == ["中石化加油站"]


def test_reroute_change_strategy_plain():
    agent, calls = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="换条路走",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC])))
    assert "避开拥堵" in res.speech
    assert res.data["_route_session"]["strategy"] == "4"
    assert calls["route"][0].get("strategy") == "4"


def test_reroute_route_pref_slot():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"route_pref": "不走高速"},
        raw_text="换条不走高速的路",
        ctx=make_context(), meta=_session_meta()))
    assert res.data["_route_session"]["strategy"] == "6"


def test_reroute_change_destination_keeps_waypoints():
    airport = POI(id="apt-1", name="深圳宝安国际机场", address="宝安区",
                  lat=22.64, lng=113.81)
    agent, _ = _agent(search_results=[airport])
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={},
        raw_text="目的地改成宝安机场",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC])))
    assert "目的地已改为深圳宝安国际机场" in res.speech
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "深圳宝安国际机场"
    assert [w["name"] for w in nav["payload"]["waypoints"]] == \
        ["肯德基(海岸城店)"]                     # 途经点保持


def test_reroute_keeps_arrive_by_deadline():
    """「别迟到」不产新时限——原 arrive_by 保持并重出 G1 判定。"""
    agent, _ = _agent(route={"distance_km": 10.0, "duration_min": 20})
    deadline = int(time.time()) + 3600
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="肯德基不去了，别迟到",
        ctx=make_context(),
        meta=_session_meta(waypoints=[_KFC], arrive_by_ts=deadline)))
    assert res.data["_route_session"]["arrive_by_ts"] == deadline
    assert res.data["arrive_by_ts"] == deadline
    assert "预计" in res.speech                  # deadline note 在话术里兑现


def test_reroute_remove_destination_word_guides():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="不去万象天地了",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC])))
    assert "您是想不去万象天地了吗" in res.speech
    assert not res.actions                       # 终止导航不在本能力域，只引导


def test_reroute_no_recognized_edit_asks():
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="调整一下路线",
        ctx=make_context(), meta=_session_meta(waypoints=[_KFC])))
    assert "怎么调整" in res.speech
    assert not res.actions


def test_navigate_to_stamps_route_session():
    """挂点验证：普通导航成功 → data._route_session 结构完整（G8 抽取的数据源）。"""
    dest = POI(id="d1", name="万象天地", address="南山区", lat=22.53, lng=113.95)
    agent, _ = _agent(search_results=[dest])
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "万象天地"},
        raw_text="导航去万象天地，五点前要到",
        ctx=make_context(), meta={"current_lat": "22.54", "current_lng": "113.93"}))
    session = res.data["_route_session"]
    assert session["destination"] == "万象天地"
    assert session["lat"] == 22.53 and session["lng"] == 113.95
    assert session["ts"] > 0
    assert session.get("arrive_by_ts", 0) > int(time.time())

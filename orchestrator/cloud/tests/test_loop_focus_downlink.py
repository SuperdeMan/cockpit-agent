"""T2 循环再规划出的步也要收到焦点下发（200×5 后续批 §18.3，核心旅程 EN07）。

新规划轮、确认 / 补槽续接、改派都下发焦点事实（活动路线、候选集、目的地坐标…），唯独 T2 循环中途再规划出来的每一批此前一份都收不到：
`5ebc0bb4` 复测 EN07 r2 第 2 句「途经再加一个充电站」、r3 第 3 句「换一个充电站」，改线那一步都是 T2 再规划出来的，导航读不到
`focus_active_route`，答「当前没有正在进行的导航」——同一轮走改派的那一句就读得到。下发的焦点是「本轮开始时的焦点 + 本轮已执行
步骤刚产生的事实」（合并与落盘同一份 `context.merge_focus`）：只用本轮开始的那份，同一轮前面刚建的路线会被旧路线顶掉。
"""
from __future__ import annotations

import asyncio
import json
import time

from orchestrator.cloud.models import Plan, ReplanDecision, Step
from orchestrator.cloud.tests.test_engine_loop_settle import _engine, _request, _response, _run

_OLD_ROUTE = {"destination": "深圳北站", "lat": 22.609, "lng": 114.029,
              "waypoints": [{"name": "路特斯汽车充电站", "lat": 22.6095, "lng": 114.0295}],
              "strategy": "", "arrive_by_ts": 0, "ts": int(time.time())}
_NEW_ROUTE = {"destination": "深圳湾公园", "lat": 22.512, "lng": 113.946, "waypoints": [],
              "strategy": "", "arrive_by_ts": 0}


def _reroute(step_id="s2"):
    return Step(id=step_id, agent_id="navigation", endpoint="nav:1", intent="navigation.reroute",
                slots={"add_waypoint": "充电站"}, context_scopes=["location"])


def _route_meta(clients, intent="navigation.reroute"):
    metas = [meta for name, meta in clients.calls if name == intent]
    assert metas, f"没有派出 {intent}：{clients.calls}"
    raw = metas[-1].get("focus_active_route")
    return json.loads(raw) if raw else None


def test_a_replanned_step_sees_the_route_of_the_previous_turn():
    """EN07 r2 第 2 句：初计划找充电站，T2 再规划出改线——改线要看到上一轮建的那条路线。"""
    plan = Plan(steps=[Step(id="s1", agent_id="charging-planner", endpoint="charging:1", intent="charging.find",
                            slots={"destination": "深圳北站"})],
                complexity="adaptive", goal="给正在进行的导航加一个充电站", raw_text="途经再加一个充电站")
    engine, clients, session, _planner = _engine(
        plan,
        {"charging.find": _response("找到深铁灵晔超级充电站", {"items": [
            {"name": "深铁灵晔超级充电站", "lat": 22.611, "lng": 114.031}]}),
         "navigation.reroute": _response("已加入途经点", {"destination": "深圳北站"}, actions=("navigate",))},
        decisions=[ReplanDecision(done=False, steps=[_reroute()]), ReplanDecision(done=True)])
    asyncio.run(session.save_focus("sess-t2", {"active_route": _OLD_ROUTE, "last_intent": "charging.find",
                                               "last_agent_id": "charging-planner"}, owner_user_id="u1"))

    _run(engine, _request("途经再加一个充电站"))

    route = _route_meta(clients)
    assert route is not None and route["destination"] == "深圳北站", clients.calls


def test_a_route_built_earlier_in_the_same_turn_wins_over_the_stored_one():
    """同一轮前面刚建了新路线：下发的必须是它，不能是会话里存着的上一条——否则改线就改到了旧路线上。"""
    plan = Plan(steps=[Step(id="s1", agent_id="navigation", endpoint="nav:1", intent="navigation.navigate_to",
                            slots={"destination": "深圳湾公园"}, context_scopes=["location"])],
                complexity="adaptive", goal="去深圳湾公园，顺路加个充电站", raw_text="去深圳湾公园，顺路加个充电站")
    engine, clients, session, _planner = _engine(
        plan,
        {"navigation.navigate_to": _response("出发去深圳湾公园", {"destination": "深圳湾公园",
                                                             "_route_session": _NEW_ROUTE}, actions=("navigate",)),
         "navigation.reroute": _response("已加入途经点", {"destination": "深圳湾公园"}, actions=("navigate",))},
        decisions=[ReplanDecision(done=False, steps=[_reroute()]), ReplanDecision(done=True)])
    asyncio.run(session.save_focus("sess-t2", {"active_route": _OLD_ROUTE}, owner_user_id="u1"))

    _run(engine, _request("去深圳湾公园，顺路加个充电站"))

    route = _route_meta(clients)
    assert route is not None and route["destination"] == "深圳湾公园", route


def test_the_reactive_upgrade_also_counts_the_steps_run_before_the_loop(monkeypatch):
    """simple 计划先跑完再升级 T2（reactive）：初计划的步在进循环之前就执行了、不在循环自己的 `executed_steps` 里。
    按**步**抽的那几维（候选集等）要连它们一起算——路线是从结果的保留键读的、本来就在种子结果里，这里用候选集钉住。"""
    plan = Plan(steps=[Step(id="s1", agent_id="nearby", endpoint="nearby:1", intent="nearby.search",
                            slots={"keyword": "咖啡"})],
                complexity="simple", goal="找家咖啡店看看详情", raw_text="附近找家咖啡店，看看第一家的详情")
    detail = Step(id="s2", agent_id="nearby", endpoint="nearby:1", intent="nearby.detail",
                  slots={"name": "第一家"}, context_scopes=["candidates"])
    engine, clients, session, _planner = _engine(
        plan,
        {"nearby.search": _response("找到两家", {"items": [
            {"name": "海岸咖啡", "lat": 22.51, "lng": 113.94, "rating": 4.6},
            {"name": "湾畔咖啡", "lat": 22.52, "lng": 113.95, "rating": 4.4}]}),
         "nearby.detail": _response("海岸咖啡营业到 22 点", {"name": "海岸咖啡"})},
        decisions=[ReplanDecision(done=False, steps=[detail]), ReplanDecision(done=True)])

    async def needs_replan(*_args, **_kwargs):
        return True

    monkeypatch.setattr(engine, "_needs_replan", needs_replan)

    _run(engine, _request("附近找家咖啡店，看看第一家的详情"))

    metas = [meta for name, meta in clients.calls if name == "nearby.detail"]
    assert metas, clients.calls
    listed = json.loads(metas[-1].get("focus_candidate_set") or "{}")
    assert [item.get("name") for item in listed.get("items") or []] == ["海岸咖啡", "湾畔咖啡"], metas[-1]


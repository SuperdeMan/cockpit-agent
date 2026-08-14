"""G8 路线会话：保留键 `_route_session` → Focus.active_route → prompt/meta 两个出口。

三条纪律逐条锁：粘性接力不续期、prompt 只渲染名字不渲染坐标、非法元素直接丢
（不做 str() 转换）。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from orchestrator.cloud.context import (
    ContextManager, Focus, _render_focus, _valid_route_session, extract_focus)
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus
from orchestrator.cloud.session import SessionStore


def _route_result(step_id="s1", **overrides):
    session = {"destination": "万象天地", "lat": 22.53, "lng": 113.95,
               "waypoints": [{"name": "肯德基(海岸城店)", "lat": 22.52,
                              "lng": 113.94}],
               "strategy": "6", "arrive_by_ts": int(time.time()) + 3600,
               "ts": int(time.time())}
    session.update(overrides)
    return StepResult(step_id=step_id, status=StepStatus.OK,
                      source_intent="navigation.navigate_to",
                      data={"destination": "万象天地", "_route_session": session})


def test_route_session_reserved_key_feeds_active_route():
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.navigate_to")])
    focus = extract_focus(plan, [_route_result()])
    assert focus is not None
    route = focus.active_route
    assert route["destination"] == "万象天地"
    assert [w["name"] for w in route["waypoints"]] == ["肯德基(海岸城店)"]
    assert route["strategy"] == "6"
    assert route["arrive_by_ts"] > 0 and route["ts"] > 0


def test_route_session_rejects_bad_elements_without_str_coercion():
    """waypoints 非法元素直接丢（CLAUDE.md §6：不做 str() 转换）；坏坐标整条不进。"""
    ok = _valid_route_session({
        "destination": "万象天地", "lat": 22.53, "lng": 113.95,
        "waypoints": [
            {"name": "好点", "lat": 22.52, "lng": 113.94},
            {"name": "坏点", "lat": "abc", "lng": 113.9},
            ["not", "a", "dict"],
            {"name": "", "lat": 22.5, "lng": 113.9},
        ]})
    assert [w["name"] for w in ok["waypoints"]] == ["好点"]

    assert _valid_route_session({"destination": "X", "lat": "abc", "lng": 1}) == {}
    assert _valid_route_session({"destination": "", "lat": 22.5, "lng": 113.9}) == {}
    assert _valid_route_session({"destination": "X", "lat": 999, "lng": 113.9}) == {}
    assert _valid_route_session("not a dict") == {}


def test_failed_step_route_session_ignored():
    result = _route_result()
    result.status = StepStatus.FAILED
    focus = extract_focus(Plan(steps=[]), [result])
    assert focus is None or not focus.active_route


def test_active_route_only_focus_persists():
    assert not Focus(active_route={"destination": "X", "lat": 1.0,
                                   "lng": 2.0}).is_empty()


def test_render_focus_route_names_no_coords():
    """prompt 只有名字与时限，绝不渲染坐标——坐标进 prompt 只会诱导模型自己编。"""
    arrive = int(time.mktime((2026, 8, 15, 16, 50, 0, 0, 0, -1)))
    block = _render_focus(Focus(active_route={
        "destination": "万象天地", "lat": 22.53, "lng": 113.95,
        "waypoints": [{"name": "肯德基(海岸城店)", "lat": 22.52, "lng": 113.94}],
        "arrive_by_ts": arrive, "ts": int(time.time())}))
    assert "当前正在导航：目的地=万象天地" in block
    assert "途经：肯德基(海岸城店)" in block
    assert "16:50前到达" in block
    assert "22.5" not in block and "113.9" not in block


def test_active_route_survives_non_navigation_turn_without_ts_renewal():
    """粘性接力：非导航轮保留活动路线，且 ts 原样携带不续期。"""
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    old_ts = int(time.time()) - 600
    nav_plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                                intent="navigation.navigate_to")])
    nav_results = [_route_result(ts=old_ts)]
    info_plan = Plan(steps=[Step(id="s1", agent_id="info",
                                 intent="info.weather")])
    info_results = [StepResult(step_id="s1", status=StepStatus.OK,
                               source_intent="info.weather", data={})]

    async def _run():
        await manager.update_focus("sess", nav_plan, nav_results, user_id="u1")
        await manager.update_focus("sess", info_plan, info_results, user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())
    assert focus is not None
    assert focus.active_route["destination"] == "万象天地"
    assert focus.active_route["ts"] == old_ts, "接力原样携带 ts，不许续期"
    assert focus.last_intent == "info.weather", "其余焦点字段仍按本轮刷新"


def test_new_navigate_replaces_active_route():
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.navigate_to")])

    async def _run():
        await manager.update_focus("sess", plan, [_route_result()], user_id="u1")
        await manager.update_focus(
            "sess", plan,
            [_route_result(destination="宝安机场", waypoints=[])], user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())
    assert focus.active_route["destination"] == "宝安机场"
    assert focus.active_route["waypoints"] == []


def test_apply_focus_meta_injects_route_only_into_location_scoped_steps():
    """下发面：focus_active_route 只注给声明 location scope 的步（同 focus_destination_*
    门控——LLM 与客户端都写不到 step.meta）。"""
    focus = Focus(active_route={
        "destination": "万象天地", "lat": 22.53, "lng": 113.95,
        "waypoints": [], "ts": int(time.time())})
    nav = Step(id="s1", agent_id="navigation", intent="navigation.reroute",
               context_scopes=["location"])
    chat = Step(id="s2", agent_id="chitchat", intent="chitchat.reply")
    plan = Plan(steps=[nav, chat])

    PlannerEngine._apply_focus_meta(plan, focus)

    injected = json.loads(nav.meta["focus_active_route"])
    assert injected["destination"] == "万象天地"
    assert "focus_active_route" not in chat.meta

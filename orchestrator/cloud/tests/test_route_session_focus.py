"""G8 路线会话：保留键 `_route_session` → Focus.active_route → prompt/meta 两个出口。

三条纪律逐条锁：粘性接力不续期、prompt 只渲染名字不渲染坐标、非法元素直接丢
（不做 str() 转换）。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from runtime.clock import epoch_at
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


def test_route_session_end_clears_the_active_route():
    """QA I-017：导航被取消 ⇒ 活动路线**清空**（保留键 `_route_session_end`）。

    不清的话 `focus.active_route` 还挂着，下一句「换条路」会去改一条已经取消的路线
    ——而用户已经听到「已结束导航」了。**说了取消却还挂着**正是本条要修的形态。
    """
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.cancel")])
    ended = StepResult(step_id="s1", status=StepStatus.OK,
                       source_intent="navigation.cancel",
                       data={"_route_session_end": True})
    # 先有一条活动路线，同一份 results 里再终止它（跨轮由焦点接力，这里只验消费）
    focus = extract_focus(plan, [_route_result(), ended])
    assert focus is None or not focus.active_route,         f"取消之后活动路线还在：{getattr(focus, 'active_route', None)}"


def test_route_session_end_only_when_declared_true():
    """反向对照：没声明就不许清——恒清等于把 G8 整个关掉。"""
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.navigate_to")])
    noise = StepResult(step_id="s2", status=StepStatus.OK,
                       source_intent="navigation.locate",
                       data={"_route_session_end": "true"})   # 字符串不是 True
    focus = extract_focus(plan, [_route_result(), noise])
    assert focus is not None and focus.active_route["destination"] == "万象天地"


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
    arrive = epoch_at(2026, 8, 15, 16, 50)
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


def test_route_session_end_survives_the_sticky_relay_across_turns():
    """**跨轮**：取消之后下一轮不许再看到那条路线（QA I-017，真栈 CA5 抓到）。

    ⚠ 这条是 2026-08-19 补的，补它的原因值得记：上面那两条同轮断言**当时全绿，
    而真栈第 3 轮 3/3 红**——「取消导航」发出去了，下一句「换条路走」照样改到那条
    已取消的路线。根因是**接力比清除更强**：粘性接力的条件是 `not focus.active_route`，
    而清空恰恰让它成立 ⇒ 上一轮那条被原样搬回来。
    ⇒ **同轮测试替被测系统提供了「同轮」这个前提**，而这条机制的全部意义在跨轮
    （§4.3「测试若替被测系统提供了某个前提，那条前提就不再被验证」的又一例）。
    """
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    nav_plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                                intent="navigation.navigate_to")])
    cancel_plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                                   intent="navigation.cancel")])
    cancel_results = [StepResult(step_id="s1", status=StepStatus.OK,
                                 source_intent="navigation.cancel",
                                 data={"_route_session_end": True})]
    later_plan = Plan(steps=[Step(id="s1", agent_id="info",
                                  intent="info.weather")])
    later_results = [StepResult(step_id="s1", status=StepStatus.OK,
                                source_intent="info.weather", data={})]

    async def _run():
        await manager.update_focus("sess", nav_plan, [_route_result()], user_id="u1")
        await manager.update_focus("sess", cancel_plan, cancel_results, user_id="u1")
        after_cancel = await manager._load_focus("sess", "u1")
        # 再走一轮无关轮：**旗子不许粘住**，否则以后任何一次导航都接力不了
        await manager.update_focus("sess", later_plan, later_results, user_id="u1")
        return after_cancel, await manager._load_focus("sess", "u1")

    after_cancel, after_later = asyncio.run(_run())
    assert not (after_cancel and after_cancel.active_route),         "取消之后下一轮仍看得见活动路线——接力把清空搬回来了"
    assert not getattr(after_cancel, "route_ended", False),         "`route_ended` 是本轮事实，存进焦点会永久关掉接力"
    assert not (after_later and after_later.active_route)


def test_route_ended_does_not_block_a_later_new_navigation():
    """反向对照：取消过之后**再导一次**，活动路线要正常建立。

    只验「取消能清掉」那一半，一个恒不接力的实现也能过——那会把 G8 整个关掉。
    """
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    nav_plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                                intent="navigation.navigate_to")])
    cancel_plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                                   intent="navigation.cancel")])
    cancel_results = [StepResult(step_id="s1", status=StepStatus.OK,
                                 source_intent="navigation.cancel",
                                 data={"_route_session_end": True})]
    info_plan = Plan(steps=[Step(id="s1", agent_id="info", intent="info.weather")])
    info_results = [StepResult(step_id="s1", status=StepStatus.OK,
                               source_intent="info.weather", data={})]

    async def _run():
        await manager.update_focus("sess", nav_plan, [_route_result()], user_id="u1")
        await manager.update_focus("sess", cancel_plan, cancel_results, user_id="u1")
        await manager.update_focus(
            "sess", nav_plan, [_route_result(destination="宝安机场", waypoints=[])],
            user_id="u1")
        await manager.update_focus("sess", info_plan, info_results, user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())
    assert focus.active_route["destination"] == "宝安机场",         "取消过之后新导航建不起来 / 或接力被永久关掉了"


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

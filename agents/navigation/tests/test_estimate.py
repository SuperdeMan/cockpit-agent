"""navigation.estimate：只算不导 + origin 槽（QA 卡 Q8 / I-016 与 I-029②）。

两条缺口本来是**一件事**：能力面里只有「导航过去」这一个工具，于是
- 「从A到B多远多久」被 planner 就近挑成 navigate_to，**真的开始导航**（I-016）；
- 「从X出发」没有槽可放，被静默丢掉或塞进 destination（I-029②，探针 SL4 四轮 12/13 红）。

所以这份测试的两组断言只有一个主张：**能力面上「问数」与「动身」是两件事，
起点是用户能说了算的一维。**
"""
import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

_HERE = {"current_lat": "22.5410", "current_lng": "113.9412"}


def _agent(search_results=None, route=None):
    agent = NavigationAgent()
    calls = {"search": [], "route": []}

    async def search(keyword, near=None, **kwargs):
        calls["search"].append(keyword)
        hits = search_results or {}
        if isinstance(hits, dict):
            return list(hits.get(keyword, []))
        return list(hits)

    async def get_route(a, b, **kwargs):
        calls["route"].append((a, b))
        return dict(route or {"distance_km": 21.4, "duration_min": 33})

    agent.poi.search = search
    agent.poi.get_route = get_route
    return agent, calls


_CIVIC = POI(id="o1", name="深圳市民中心", address="福中路", lat=22.5460, lng=114.0590)
_NORTH = POI(id="d1", name="深圳北站", address="致远中路28号", lat=22.6100, lng=114.0290)
_XIAMEN = POI(id="x1", name="厦门火车站", address="厦禾路900号", lat=24.4680, lng=118.1170)
#: 「从深圳欢乐海岸出发去世界之窗」那几条的桩：名字要和说法沾边——名字毫不相干的兜底结果不再当起终点（2026-10-08）
_COAST = POI(id="o2", name="欢乐海岸", address="白石路东8号", lat=22.5460, lng=114.0590)
_WINDOW = POI(id="d2", name="世界之窗", address="深南大道9037号", lat=22.6100, lng=114.0290)


def _estimate(agent, slots, raw_text, meta=None):
    return asyncio.run(run_handle(agent, "navigation.estimate", slots=slots,
                                  raw_text=raw_text, ctx=make_context(),
                                  meta=dict(meta if meta is not None else _HERE)))


def test_estimate_answers_the_number_and_never_navigates():
    """本卡的**危险形态**：纯查询不得产生导航动作。"""
    agent, _ = _agent({"深圳市民中心": [_CIVIC], "深圳北站": [_NORTH]})
    res = _estimate(agent, {"origin": "深圳市民中心", "destination": "深圳北站"},
                    "从深圳市民中心到深圳北站开车大概多远、要多久")
    assert res.status == "ok"
    assert not res.actions, f"只算不导却发了动作：{res.actions}"
    assert "21.4公里" in res.speech and "33分钟" in res.speech
    assert res.ui_card["estimate"] is True
    assert res.ui_card["origin"] == "深圳市民中心"
    assert res.ui_card["destination"] == "深圳北站"


def test_estimate_defaults_origin_to_current_position():
    agent, calls = _agent({"深圳北站": [_NORTH]})
    res = _estimate(agent, {"destination": "深圳北站"}, "到深圳北站还有多远")
    assert not res.actions
    assert res.ui_card["origin"] == "当前位置"
    start, _end = calls["route"][0]
    assert (round(start.lat, 4), round(start.lng, 4)) == (22.5410, 113.9412)


def test_a_placeholder_origin_from_the_planner_is_the_current_position():
    """2026-10-04 真栈：规划器把「去上海虹桥站要开多久」的起点写成字面「当前位置」，估算拿这四个字去搜地点，
    搜到一个停车场入口当起点（还因此跳过了续航提醒）。占位值就是没给起点（判据 `runtime.slots`）。"""
    for placeholder in ("当前位置", "我的位置", "这里"):
        agent, calls = _agent({"深圳北站": [_NORTH], placeholder: [_CIVIC]})
        res = _estimate(agent, {"destination": "深圳北站", "origin": placeholder}, "去深圳北站要开多久")
        assert res.ui_card["origin"] == "当前位置" and placeholder not in calls["search"]
        start, _end = calls["route"][0]
        assert (round(start.lat, 4), round(start.lng, 4)) == (22.5410, 113.9412)


def test_estimate_without_position_asks_instead_of_guessing():
    """**没有起点就没有路程**——不许拿一个假起点算出一个像模像样的数。"""
    agent, calls = _agent({"深圳北站": [_NORTH]})
    res = _estimate(agent, {"destination": "深圳北站"}, "到深圳北站还有多远", meta={})
    assert res.status == "need_slot"
    assert "origin" in res.missing_slots
    assert not calls["route"], "拿不到起点却仍然算了一次路"


def test_estimate_unresolvable_destination_is_honest():
    agent, calls = _agent({})
    res = _estimate(agent, {"destination": "不存在的地方"}, "到不存在的地方多远")
    assert not res.actions and not calls["route"]
    assert "没找到" in res.speech


def test_an_origin_the_user_never_said_is_ignored():
    """2026-10-08 真栈：「去深南大道9028号要开多久」规划器填了 origin「北京」，估出 2164 公里——原话里一个字都不沾的起点
    不算，按当前位置算（估算与到目的地导航两条路）；规划器给起点加了城市（「深圳欢乐海岸」之于原话「欢乐海岸」）照样认。"""
    beijing = POI(id="bj", name="北京", address="北京市", lat=39.9042, lng=116.4074)
    agent, calls = _agent({"深圳北站": [_NORTH], "北京": [beijing]})
    res = _estimate(agent, {"destination": "深圳北站", "origin": "北京"}, "去深圳北站要开多久")
    start, _end = calls["route"][0]
    assert (round(start.lat, 4), round(start.lng, 4)) == (22.5410, 113.9412) and res.ui_card["origin"] == "当前位置"
    assert "北京" not in calls["search"]
    agent, calls = _agent({"世界之窗": [_WINDOW], "北京": [beijing]})
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "世界之窗", "origin": "北京"},
                                 raw_text="导航去世界之窗", ctx=make_context(), meta=dict(_HERE)))
    assert res.ui_card["origin"] == "当前位置" and "北京" not in calls["search"]
    agent, calls = _agent({"深圳欢乐海岸": [_COAST], "深圳北站": [_NORTH]})
    res = _estimate(agent, {"destination": "深圳北站", "origin": "深圳欢乐海岸"}, "从欢乐海岸到深圳北站多远")
    assert res.ui_card["origin"] == "欢乐海岸"


def test_navigate_to_uses_the_spoken_origin():
    """I-029②：用户明说了出发地，算路/卡片/动作载荷三处都要按它来。

    这条是 SL4 的单测对应物——探针判的是卡片里不再是「当前位置」。
    """
    agent, calls = _agent({"深圳欢乐海岸": [_COAST], "世界之窗": [_WINDOW]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"origin": "深圳欢乐海岸", "destination": "世界之窗"},
        raw_text="从深圳欢乐海岸出发去世界之窗",
        ctx=make_context(), meta=dict(_HERE)))
    assert res.ui_card["origin"] == "欢乐海岸"      # provider 桩返回的 POI 名
    start, _end = calls["route"][0]
    assert (round(start.lat, 4), round(start.lng, 4)) == (22.5460, 114.0590), \
        "算路仍然用了当前位置——静默回落正是本条要修的形态"
    payload = res.actions[0]["payload"]
    assert round(payload["origin_lat"], 4) == 22.5460


def test_navigate_to_unresolvable_origin_asks_instead_of_silently_falling_back():
    agent, calls = _agent({"世界之窗": [_WINDOW]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"origin": "查无此地", "destination": "世界之窗"},
        raw_text="从查无此地出发去世界之窗",
        ctx=make_context(), meta=dict(_HERE)))
    assert res.status == "need_slot" and "origin" in res.missing_slots
    assert not calls["route"]


def test_navigate_to_without_origin_is_unchanged():
    """反向对照：没说出发地时，行为与本批之前逐字一致（当前位置起算）。"""
    agent, calls = _agent({"世界之窗": [_WINDOW]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "世界之窗"},
        raw_text="导航去世界之窗", ctx=make_context(), meta=dict(_HERE)))
    assert res.ui_card["origin"] == "当前位置"
    start, _end = calls["route"][0]
    assert (round(start.lat, 4), round(start.lng, 4)) == (22.5410, 113.9412)
    assert res.actions and res.actions[0]["type"] == "navigate"


# ── navigation.cancel：终止当前导航（QA 卡 Q8 / I-017）──────────────────────
# 它同时是**同批那条判据收窄的必要配套**：`pending_cancel._no_substance_left` 把
# 「取消导航」从前置闸里放了出来，放开一条路却不给它落点，是把一个错换成另一个错。

def _session_meta(destination="深圳科技园", lat=22.53, lng=113.95):
    import json
    import time as _t
    return {"focus_active_route": json.dumps(
        {"destination": destination, "lat": lat, "lng": lng, "waypoints": [],
         "strategy": "", "ts": int(_t.time())}, ensure_ascii=False)}


def test_cancel_ends_the_route_and_clears_the_server_side_fact():
    agent, _ = _agent()
    res = asyncio.run(run_handle(agent, "navigation.cancel", slots={},
                                 raw_text="取消导航", ctx=make_context(),
                                 meta=_session_meta()))
    assert res.status == "ok"
    assert "深圳科技园" in res.speech, "没说清取消的是哪一趟"
    assert res.data["_route_session_end"] is True, "服务端事实没被清掉"
    assert res.ui_card["cancelled"] is True
    assert res.actions and res.actions[0]["type"] == "navigate_cancel"


def test_cancel_without_active_route_is_honest_not_a_fake_ack():
    """**没有正在导航时不许回「已取消」**——那正是 I-017 里用户看到「已取消」
    却什么都没变的来源。"""
    agent, _ = _agent()
    res = asyncio.run(run_handle(agent, "navigation.cancel", slots={},
                                 raw_text="取消导航", ctx=make_context(), meta={}))
    assert "没有正在进行的导航" in res.speech
    assert not res.actions
    assert "已取消" not in res.speech and "已结束" not in res.speech


# ─── CA2-19 S4：出发前问「多远 / 多久」，续航覆盖不了就提电量（与导航同一个判断）───

def _long_trip(reading=None, slots=None, raw_text="去厦门火车站要开多久"):
    agent, _ = _agent({"厦门火车站": [_XIAMEN], "深圳北站": [_NORTH]},
                      route={"distance_km": 600.3, "duration_min": 373})
    ctx = make_context(context_values={"vehicle.battery": reading} if reading is not None else None)
    return asyncio.run(run_handle(agent, "navigation.estimate", slots=slots or {"destination": "厦门火车站"},
                                  raw_text=raw_text, ctx=ctx, meta=dict(_HERE)))


def test_estimate_from_here_mentions_charging_when_the_reading_cannot_cover_the_trip():
    res = _long_trip("40")
    assert "提醒一下：当前电量约40%（模拟车读数）（估算续航约200公里）" in res.speech
    assert res.speech.endswith("需要我导航过去吗？") and not res.actions


def test_estimate_says_nothing_about_battery_without_a_reading_or_on_a_short_trip():
    assert "电量" not in _long_trip().speech
    short, _ = _agent({"深圳北站": [_NORTH]})
    near = asyncio.run(run_handle(short, "navigation.estimate", slots={"destination": "深圳北站"},
                                  raw_text="去深圳北站多远",
                                  ctx=make_context(context_values={"vehicle.battery": "40"}), meta=dict(_HERE)))
    assert "电量" not in near.speech


def test_estimate_from_another_origin_ignores_the_current_battery():
    """「从深圳北站到厦门多远」：车现在的电量跟那段路无关。"""
    res = _long_trip("40", slots={"origin": "深圳北站", "destination": "厦门火车站"},
                     raw_text="从深圳北站到厦门火车站多远")
    assert "电量" not in res.speech


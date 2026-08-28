"""G8 navigation.reroute：增量改道（删/加途经点、换路线、改目的地）+ 路线会话保留键。

不起 gRPC，直接驱动 handle。活动路线经 meta.focus_active_route（engine 注入的
服务端事实）传入；断言覆盖「未点名的约束保持」与降级面。
"""
import asyncio
import json
import time

import pytest

from runtime.clock import epoch_at
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


def test_pickup_then_new_place_promotes_the_new_place_to_destination():
    """「接孩子后去万象城」的先后关系是学校作途经点、万象城作终点。

    MiniMax 真栈把万象城填进 ``add_waypoint``，旧实现就保留学校为目的地，实际
    规划成「当前位置 -> 万象城 -> 学校」，与用户说的顺序完全相反。Agent 能从
    原话和服务端活动路线确定性消解，不应让模型的槽位极性决定路线顺序。
    """
    mall = POI(id="mall-1", name="深圳湾万象城", address="南山区",
               lat=22.5155, lng=113.9444)
    agent, _ = _agent(search_results=[mall])
    meta = _session_meta(
        destination="深圳市南山实验教育集团明远学校",
        lat=22.5290, lng=113.9289,
    )

    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"add_waypoint": "万象城"},
        raw_text="接孩子后去万象城。", ctx=make_context(), meta=meta))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "深圳湾万象城"
    assert [w["name"] for w in nav["payload"]["waypoints"]] == \
        ["深圳市南山实验教育集团明远学校"]
    assert "当前位置 → 深圳市南山实验教育集团明远学校 → 深圳湾万象城" in res.speech
    assert res.data["_route_session"]["destination"] == "深圳湾万象城"


@pytest.mark.parametrize("raw_text", [
    "别接孩子了，然后去万象城。",
    "不接孩子了，然后去万象城。",
    "不想去接孩子了，然后去万象城。",
    "没打算去接孩子了，然后去万象城。",
    "不是去接孩子，然后去万象城。",
])
def test_cancelled_pickup_does_not_keep_the_old_school_as_a_waypoint(raw_text):
    """「别接孩子了」撤销旧目的；后续新终点不能再经过学校。"""
    mall = POI(id="mall-1", name="深圳湾万象城", address="南山区",
               lat=22.5155, lng=113.9444)
    agent, _ = _agent(search_results=[mall])
    meta = _session_meta(
        destination="深圳市南山实验教育集团明远学校",
        lat=22.5290, lng=113.9289,
    )

    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"destination": "万象城"},
        raw_text=raw_text, ctx=make_context(), meta=meta))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "深圳湾万象城"
    assert nav["payload"].get("waypoints", []) == []
    assert "接孩子" not in res.speech


@pytest.mark.parametrize("raw_text", [
    "不得不接孩子，然后去万象城。",
    "我打算去接孩子，然后去万象城。",
])
def test_explicit_positive_pickup_keeps_the_school_as_a_waypoint(raw_text):
    mall = POI(id="mall-1", name="深圳湾万象城", address="南山区",
               lat=22.5155, lng=113.9444)
    agent, _ = _agent(search_results=[mall])
    meta = _session_meta(
        destination="深圳市南山实验教育集团明远学校",
        lat=22.5290, lng=113.9289,
    )

    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"destination": "万象城"},
        raw_text=raw_text, ctx=make_context(), meta=meta))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert [w["name"] for w in nav["payload"].get("waypoints", [])] == [
        "深圳市南山实验教育集团明远学校"]


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


def test_route_preference_phrase_is_not_reparsed_as_a_destination():
    """「换成避堵路线」只改策略，不能把「避堵路线」拿去做 POI 搜索。

    MiniMax 真栈复现过：Planner 已正确给出 ``route_pref=避开拥堵``，但 Agent 的
    raw fallback 仍先用「换成…」正则抽出新目的地，最终把深圳北站漂成深圳医院。
    """
    agent, calls = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"route_pref": "避开拥堵"},
        raw_text="换成避堵路线",
        ctx=make_context(), meta=_session_meta(destination="深圳北站")))

    assert calls["search"] == []
    assert res.data["_route_session"]["destination"] == "深圳北站"
    assert res.data["_route_session"]["strategy"] == "4"


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


def test_navigate_to_stamps_route_session(monkeypatch):
    """挂点验证：普通导航成功 → data._route_session 结构完整（G8 抽取的数据源）。

    ⚠ **墙钟必须冻住**（2026-08-28，C13-A 落地时被这条抓到）：它原来用真实
    `time.time()` 断言「时限在未来」，而「五点前要到」在**17:00 之后**跑就不再是
    未来——旧解析器靠滚到次日凌晨把这条断言喂绿了，那正是 C13-A 修掉的行为。
    一条**读数取决于几点跑**的用例，绿和红都说明不了问题。
    """
    import agents.navigation.src.agent as nav_mod
    fixed_now = epoch_at(2026, 8, 14, 14, 0)
    monkeypatch.setattr(nav_mod.time, "time", lambda: fixed_now)

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
    assert session.get("arrive_by_ts", 0) > fixed_now


# ── C8：显式出发地（2026-08-28，QA P1-08）────────────────────────────────
# 用户在导航中说「从X出发去Y」时，planner 选 reroute 没错——错的是这个 intent
# 少一维。下面四条把那一维的两侧都钉住：接住了要三处一起换（算路/话术/卡片，
# 外加动作载荷），接不住要**诚实反问**而不是悄悄拿当前位置顶上。

def test_reroute_explicit_origin_replaces_start_everywhere():
    happy = POI(id="hly-1", name="深圳欢乐海岸", address="南山区",
                lat=22.51, lng=113.98)
    agent, calls = _agent(search_results=[happy])
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"origin": "深圳欢乐海岸"},
        raw_text="从深圳欢乐海岸出发",
        ctx=make_context(), meta=_session_meta()))
    # ① 话术与卡片：起点是欢乐海岸，不是「当前位置」
    assert "起点改为深圳欢乐海岸" in res.speech
    assert "当前路线：深圳欢乐海岸 → 万象天地" in res.speech
    assert res.ui_card["origin"] == "深圳欢乐海岸"
    # ② 动作载荷：起点坐标换成解析出来的那对，不是 meta 里的 GPS
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert (nav["payload"]["origin_lat"], nav["payload"]["origin_lng"]) == (22.51, 113.98)
    # ③ 算路的起点也换了（get_route 第一个参数）——三处一起换的第三处
    assert calls["route"], "应重算过路线"


def test_reroute_unresolvable_origin_asks_instead_of_falling_back():
    """`_resolve_point` 的「绝不悄悄回落当前位置」语义在 reroute 上同样成立。"""
    agent, _ = _agent(search_results=[])          # 搜不到 → 解析失败
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={"origin": "野人先生门口"},
        raw_text="从野人先生门口出发",
        ctx=make_context(), meta=_session_meta()))
    assert res.status == "need_slot"
    assert "野人先生门口" in res.speech
    assert not res.actions                        # 一个假起点都不许算出来
    assert res.missing_slots == ["origin"]


def test_reroute_without_origin_keeps_current_position_verbatim():
    """对照：不带 origin 的改道行为与本批之前逐字一致。"""
    agent, _ = _agent()
    res = asyncio.run(run_handle(
        agent, "navigation.reroute", slots={}, raw_text="换条路走",
        ctx=make_context(), meta=_session_meta()))
    assert "当前路线：当前位置 → 万象天地" in res.speech
    assert res.ui_card["origin"] == "当前位置"
    assert "起点改为" not in res.speech


def test_reroute_origin_and_destination_change_together():
    """T22 原句形态：起点与目的地同一轮换，两处都要落进卡片。"""
    happy = POI(id="hly-1", name="深圳欢乐海岸", address="南山区",
                lat=22.51, lng=113.98)
    window = POI(id="ww-1", name="世界之窗", address="南山区",
                 lat=22.54, lng=113.97)
    agent, _ = _agent(search_results=[window])
    # `_resolve_point`（起点）与 `_find_destination`（目的地）共用 poi.search，
    # 且**起点先解析**——按调用序排会写反，按关键词分派才与实现解耦。
    async def search(keyword, near=None, **kwargs):
        return [happy] if "欢乐海岸" in str(keyword) else [window]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, "navigation.reroute",
        slots={"origin": "深圳欢乐海岸", "destination": "世界之窗"},
        raw_text="从深圳欢乐海岸出发去世界之窗",
        ctx=make_context(), meta=_session_meta()))
    assert res.ui_card["origin"] == "深圳欢乐海岸"
    assert res.ui_card["destination"] == "世界之窗"

"""只说了从哪出发、没说去哪：不许替用户定目的地（评审三轮追加批 E，2026-09-23）。

真栈 `c99a9a74` RS21 第二句「从深圳欢乐海岸出发，不走高速」三趟里两趟被执行成导航：
① planner 只给 `origin`，`_navigate_to` 目的地为空就拿**整句原话**当目的地去搜，搜回起点本身
   ⇒「从华侨城欢乐海岸为您导航到华侨城欢乐海岸」；
② planner 编出 `destination=天安门`（原话里一个字都没有）⇒ 2390 km、48 小时。
通过那一趟是 `navigation.estimate` 追问「您想算到哪里的路程？」——那才是对的出口。

判据一份：原话说了从哪出发，去掉那一截后既没有去向表达、目的地也不沾原话、又不是会话里已确立的
目的地（引擎下发的 `focus_destination` / 活动路线）⇒ 追问去哪。解析后起终点是同一处 ⇒ 追问。
"""
import asyncio
import json
import time

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

_HERE = {"current_lat": "22.5410", "current_lng": "113.9412"}
_HAPPY = POI(id="o1", name="华侨城欢乐海岸", address="滨海大道2008号", lat=22.5238, lng=113.9853)
_WINDOW = POI(id="d1", name="世界之窗", address="深南大道9037号", lat=22.5405, lng=113.9740)
_TIANANMEN = POI(id="d2", name="天安门", address="长安街北侧", lat=39.9087, lng=116.3975)
_PARK = POI(id="p1", name="深圳湾公园", address="滨海大道", lat=22.5160, lng=113.9510)
_PARK_GATE = POI(id="p2", name="深圳湾公园南门", address="滨海大道", lat=22.5161, lng=113.9512)
_SCHOOL = POI(id="s1", name="南山实验小学", address="南山大道", lat=22.5300, lng=113.9300)


def _agent(results):
    agent = NavigationAgent()
    calls = {"search": [], "route": []}

    async def search(keyword, near=None, **kwargs):
        calls["search"].append(keyword)
        for key, hits in results.items():
            if key in keyword:
                return list(hits)
        return []

    async def get_route(a, b, **kwargs):
        calls["route"].append((a, b))
        return {"distance_km": 12.3, "duration_min": 25}

    agent.poi.search = search
    agent.poi.get_route = get_route
    return agent, calls


def _run(agent, intent, slots, raw_text, meta=None):
    return asyncio.run(run_handle(agent, intent, slots=slots, raw_text=raw_text,
                                  ctx=make_context(),
                                  meta=dict(meta if meta is not None else _HERE)))


_RS21 = "从深圳欢乐海岸出发，不走高速"
_ALL = {"欢乐海岸": [_HAPPY], "世界之窗": [_WINDOW], "天安门": [_TIANANMEN],
        "深圳湾公园南门": [_PARK_GATE], "深圳湾公园": [_PARK], "南山实验小学": [_SCHOOL]}


def _asks_where(res):
    assert res.status == "need_slot", (res.status, res.speech)
    assert "destination" in (res.missing_slots or []), res.missing_slots
    assert not res.actions, f"没说去哪却发了动作：{res.actions}"
    assert "哪里" in res.speech, res.speech


# ── 真栈两种 planner 写法 ───────────────────────────────────────────────────

def test_origin_only_with_no_destination_asks_instead_of_searching_the_whole_utterance():
    """①：修前原话整句当目的地去搜，搜回起点 ⇒ 从欢乐海岸导航到欢乐海岸。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"origin": "深圳欢乐海岸", "route_pref": "不走高速"}, _RS21)
    _asks_where(res)
    assert "深圳欢乐海岸" in res.speech, res.speech
    assert not calls["route"]


def test_origin_only_with_an_invented_destination_asks_instead_of_navigating():
    """②：修前按 planner 编的「天安门」规划了 2390 km。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "天安门", "origin": "深圳欢乐海岸", "route_pref": "不走高速"}, _RS21)
    _asks_where(res)
    assert not calls["route"]


def test_origin_only_with_the_origin_repeated_as_destination_asks():
    """planner 把起点抄进目的地：它沾原话，但沾的是「从 X 出发」那一截。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "深圳欢乐海岸", "origin": "深圳欢乐海岸"}, _RS21)
    _asks_where(res)


def test_estimate_with_an_invented_destination_asks_too():
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.estimate",
               {"destination": "天安门", "origin": "深圳欢乐海岸"}, _RS21)
    _asks_where(res)
    assert not calls["route"]


# ── 会话里已确立的目的地：放行 ─────────────────────────────────────────────

def _focus(name="世界之窗", lat=22.5405, lng=113.9740):
    return {**_HERE, "focus_destination": name,
            "focus_destination_lat": str(lat), "focus_destination_lng": str(lng)}


def test_destination_established_in_the_conversation_is_trusted():
    """上一轮刚算过到世界之窗多远，这一轮「那就从欢乐海岸出发吧」——目的地是会话里的事实。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "世界之窗", "origin": "深圳欢乐海岸"},
               "那就从深圳欢乐海岸出发吧", meta=_focus())
    assert res.status != "need_slot", res.speech
    assert any(a["type"] == "navigate" for a in res.actions), res.actions


def test_estimate_follow_up_with_a_new_origin_keeps_the_established_destination():
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.estimate",
               {"destination": "世界之窗", "origin": "深圳欢乐海岸"},
               "从深圳欢乐海岸出发呢", meta=_focus())
    assert res.status == "ok" and calls["route"], res.speech


def test_active_route_destination_is_trusted():
    meta = {**_HERE, "focus_active_route": json.dumps(
        {"destination": "世界之窗", "lat": 22.5405, "lng": 113.9740, "waypoints": [],
         "strategy": "", "ts": int(time.time())}, ensure_ascii=False)}
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "世界之窗", "origin": "深圳欢乐海岸"}, _RS21, meta=meta)
    assert any(a["type"] == "navigate" for a in res.actions), (res.status, res.speech)


# ── 原话自己带着去向：一个字不动 ────────────────────────────────────────────

def test_origin_and_destination_in_the_same_utterance_are_unchanged():
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "深圳世界之窗", "origin": "欢乐海岸"},
               "从欢乐海岸出发去世界之窗")
    assert any(a["type"] == "navigate" for a in res.actions), (res.status, res.speech)


def test_pickup_after_the_origin_counts_as_a_destination_expression():
    """「从公司出发接孩子」：接送就是去向；planner 填的校名不在原话里，照样放行。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "南山实验小学", "origin": "深圳湾公园"},
               "从深圳湾公园出发接孩子")
    assert res.status != "need_slot" or "destination" not in (res.missing_slots or []), res.speech


def test_empty_destination_without_origin_still_uses_the_utterance():
    """原话兜底本身不动：「导航到世界之窗」planner 没填槽时照样从原话里取。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to", {}, "导航到世界之窗")
    assert any(a["type"] == "navigate" for a in res.actions), (res.status, res.speech)


def test_empty_destination_fallback_skips_the_origin_part():
    """planner 没填目的地、原话自己带着去向：兜底先去掉「从 X 出发」那截，不许拿起点当目的地去搜。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to", {}, "从欢乐海岸出发去世界之窗")
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and nav[0]["payload"]["destination"] == "世界之窗", (res.status, res.speech)


_EAST = POI(id="e1", name="东部华侨城", address="大梅沙", lat=22.6110, lng=114.2940)


def test_a_multi_clause_utterance_is_not_searched_as_one_place():
    """评审四轮待办（2026-09-25）：两步计划里 navigate_to 的目的地本该引用上一步，上一步没找到 ⇒ 槽是空的。修前把整句当地名去搜、
    原样念回来：「暂时无法确定「先帮我查一下墨汐国际中心在哪，导航过去」对应的具体地点」（真栈 `ee94c595` RS41）。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to", {}, "先帮我查一下墨汐国际中心在哪，然后导航过去")
    assert res.status == "need_slot" and res.missing_slots == ["destination"]
    assert res.speech == "您要去哪里？"
    assert calls["search"] == []


def test_a_navigation_verb_governs_only_its_first_clause():
    """collector 历史：「导航去东部华侨城，沿途帮我找个充电站」修前念成「暂时无法确定「东部华侨城，沿途帮我找个充电站」…」。"""
    agent, calls = _agent({**_ALL, "东部华侨城": [_EAST]})
    res = _run(agent, "navigation.navigate_to", {}, "导航去东部华侨城，沿途帮我找个充电站")
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and nav[0]["payload"]["destination"] == "东部华侨城", (res.status, res.speech)
    assert all("充电站" not in keyword for keyword in calls["search"])


def test_a_trailing_route_preference_still_leaves_one_clause():
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to", {}, "带我去世界之窗，不走高速")
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and nav[0]["payload"]["destination"] == "世界之窗", (res.status, res.speech)


def test_a_route_preference_alone_is_not_a_destination():
    """「不走高速」不是地名：修前拿它去搜，答「暂时无法确定「不走高速」对应的具体地点」。"""
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to", {"route_pref": "不走高速"}, "不走高速")
    _asks_where(res)
    assert not calls["search"], calls["search"]


# ── 起终点是同一处 ──────────────────────────────────────────────────────────

def test_origin_and_destination_resolving_to_the_same_place_asks():
    agent, calls = _agent(_ALL)
    res = _run(agent, "navigation.navigate_to",
               {"destination": "深圳湾公园", "origin": "深圳湾公园南门"},
               "从深圳湾公园南门出发去深圳湾公园")
    _asks_where(res)
    assert not calls["route"]

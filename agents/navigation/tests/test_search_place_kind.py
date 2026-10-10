"""地点搜索：原话以地点类型词结尾、搜到的名字里都没有它，就不是用户说的那个地方（本地目的地排序 §17，2026-10-10）。

修前深圳搜「人民广场」回的是宝安人民医院、南山区人民检察院停车场……照样说「为您找到 4 个人民广场」，下游导航接过第一项去了医院。
"""
from __future__ import annotations

import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SZ = {"current_lat": "22.5410", "current_lng": "113.9412"}


def _search(keyword, results, raw_text=None):
    agent = NavigationAgent()

    async def search(kw, near=None, **kwargs):
        return list(results)

    async def no_landmark(description):
        return []
    agent.poi.search = search
    agent._landmark_candidates = no_landmark
    return asyncio.run(run_handle(agent, "navigation.search_poi", slots={"keyword": keyword},
                                  raw_text=raw_text or f"{keyword}在哪", ctx=make_context(), meta=dict(SZ)))


def _poi(pid, name):
    return POI(id=pid, name=name, lat=22.55, lng=113.90, address="深圳")


def test_a_place_kind_no_result_has_is_reported_as_not_found():
    # 第四个是别的广场（2026-10-10 真栈原样）：只看「广场」在不在会放过它，要名字对得上原话
    res = _search("人民广场", [_poi("h1", "深圳市宝安人民医院"), _poi("p1", "南山区人民检察院停车场"),
                             _poi("h2", "关口村人民医院住宅楼"), _poi("g1", "常兴时代广场")])
    assert "没找到「人民广场」" in res.speech and "宝安人民医院" in res.speech
    assert "items" not in (res.data or {})            # 下游按槽位引用取不到它
    assert not res.actions


def test_a_result_that_is_that_kind_of_place_is_listed_as_before():
    res = _search("市民广场", [_poi("s1", "深圳市民广场"), _poi("h1", "市民中心")])
    assert "为您找到" in res.speech and res.data["items"][0]["name"] == "深圳市民广场"


def test_keywords_that_are_not_a_place_kind_are_untouched():
    res = _search("人民公园", [_poi("h1", "人民医院")])   # 「公园」不在类型词里（没有真栈红例），照旧
    assert "为您找到" in res.speech


def test_a_trip_estimate_question_is_handed_to_the_estimate_not_to_navigation():
    """2026-10-10 冻结 P2：「去KFC要开多久」被规划成地点搜索，按导航词改派「导航去目的地」，问句闸拦下、闲聊答了一句空话。
    问路程 / 时间的交估算（只读），要动身的照旧交导航。"""
    ask = _search("KFC", [_poi("k1", "肯德基(科苑店)")], raw_text="去KFC要开多久")
    assert ask.data["_escalate"]["intent"] == "navigation.estimate"
    assert ask.data["_escalate"]["slots"] == {"destination": "KFC"}
    go = _search("KFC", [_poi("k1", "肯德基(科苑店)")], raw_text="导航去KFC")
    assert go.data["_escalate"]["intent"] == "navigation.navigate_to"


def test_a_not_found_estimate_question_is_handed_to_the_estimate_too():
    """「没找到」那条改派与交接同一个方向（带导航词的原话才改派：视觉地标描述搜不到时走这里）。"""
    ask = NavigationAgent._search_not_found("云岚国际中心", "去云岚国际中心要多久")
    assert "没找到" in ask.speech and ask.data["_escalate"]["intent"] == "navigation.estimate"
    go = NavigationAgent._search_not_found("云岚国际中心", "导航去云岚国际中心")
    assert go.data["_escalate"]["intent"] == "navigation.navigate_to"
    assert not NavigationAgent._search_not_found("云岚国际中心", "云岚国际中心在哪").data

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

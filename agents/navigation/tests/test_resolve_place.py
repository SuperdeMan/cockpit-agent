"""内部意图 `navigation.resolve_place`（2026-10-15）：给提醒的到达围栏把说出口的地点解析成一个点。

与「导航到目的地」同一份解析（`_find_destination` 严格校验 + `_far_namesake` 借名判断），不导航、不出卡、不说话。
修前提醒经 `nearby.search` 取第一个带坐标的候选、不核对名字，乱说一个地点会落到随便一个 POI 上。
"""
from __future__ import annotations

import asyncio

from agents._sdk.manifest import load_manifest
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src import agent as navmod
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SZ = {"current_lat": "22.5410", "current_lng": "113.9412"}


class _Poi:
    def __init__(self, near, wide=(), fail=False):
        self.near, self.wide, self.fail = list(near), list(wide), fail

    async def search(self, keyword, near=None, **kw):
        if self.fail:
            raise navmod.ProviderError("down")
        return list(self.near if near is not None else self.wide)


def _resolve(place, near, wide=(), fail=False):
    agent = NavigationAgent()
    agent.poi = _Poi(near, wide, fail)

    async def no_landmark(description):
        return []
    agent._landmark_candidates = no_landmark
    res = asyncio.run(run_handle(agent, "navigation.resolve_place", slots={"place": place},
                                 meta=SZ, ctx=make_context()))
    assert res.status == "ok" and res.speech == "" and not res.actions and res.ui_card is None
    return res.data


def test_a_named_place_resolves_to_one_verified_point():
    data = _resolve("星巴克", [POI(id="s1", name="星巴克(科技园店)", lat=22.543, lng=113.944, city="深圳市")])
    assert data["status"] == "found" and data["poi"]["name"] == "星巴克(科技园店)"
    assert (data["poi"]["lat"], data["poi"]["lng"]) == (22.543, 113.944)


def test_an_unrelated_nearby_point_is_not_taken():
    """名字毫不沾边的兜底不收（同导航：「北大医院」→ 眼科门诊角膜塑形镜室）——修前提醒会把围栏落在它上面。"""
    data = _resolve("北大医院", [POI(id="x1", name="眼科门诊角膜塑形镜室", lat=22.55, lng=113.95, city="深圳市")])
    assert data == {"status": "not_found"}


def test_a_borrowed_name_nearby_and_the_original_elsewhere_are_both_handed_back():
    """近处借了名、外地另有本体：不替用户挑，两个都交回（同导航的借名反问）。"""
    data = _resolve("南京南站", [POI(id="n1", name="南京南站南广场", lat=22.56, lng=113.95, city="深圳市")],
                    [POI(id="w1", name="南京南站", lat=31.97, lng=118.80, city="南京市")])
    assert data["status"] == "ambiguous"
    assert [c["name"] for c in data["candidates"]] == ["南京南站南广场", "南京南站"]


def test_a_map_outage_is_reported_as_such_not_as_not_found():
    assert _resolve("星巴克", [], fail=True) == {"status": "unavailable"}


def test_the_planner_never_sees_it():
    """内部意图不进 manifest（同 `navigation.route_traffic`）：规划器面前不多摆一个等价工具。"""
    from agents.navigation.src.agent import _MANIFEST
    intents = {c.intent for c in load_manifest(_MANIFEST).capabilities}
    assert "navigation.resolve_place" not in intents and "navigation.navigate_to" in intents

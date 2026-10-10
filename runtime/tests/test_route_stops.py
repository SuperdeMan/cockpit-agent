"""路线停靠点「是不是同一处」的判据（2026-10-10 真栈 swap2）：编排合并途经点、导航加站 / 换站、充电「再加一个」共用。"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.route_stops import on_route, plain_name, same_stop

_A = {"name": "路特斯汽车充电站(深圳北站中心公园闪充站)", "lat": 22.608313, "lng": 114.02513}


def test_the_same_spot_is_one_stop_whatever_it_is_called():
    assert same_stop(_A, {"name": "北站闪充站", "lat": 22.6085, "lng": 114.0252})          # 约 20 米


def test_the_same_name_nearby_is_one_stop_but_far_away_is_another_branch():
    assert same_stop(_A, {"name": _A["name"], "lat": 22.6110, "lng": 114.0251})           # 约 300 米
    assert not same_stop(_A, {"name": _A["name"], "lat": 22.6300, "lng": 114.0251})       # 约 2.4 公里


def test_without_coordinates_only_the_name_decides():
    assert same_stop(_A, {"name": "路特斯汽车充电站（深圳北站中心公园闪充站）"})              # 全角括号同名
    assert not same_stop({"name": ""}, {"name": ""})
    assert not same_stop(_A, {"name": "特来电充电站"})


def test_search_results_are_compared_by_their_attributes():
    poi = SimpleNamespace(name=_A["name"], lat=_A["lat"], lng=_A["lng"])
    assert on_route(poi, [{"name": "肯德基", "lat": 22.52, "lng": 113.94}, _A])
    assert not on_route(poi, [])


def test_plain_name_unifies_brackets_and_spaces():
    assert plain_name(" 特来电 （北站） ") == "特来电(北站)"

"""原话点名了站名的找站（200×5 后续批 §21.3）。

「附近有特斯拉的充电站吗」先按「特斯拉充电站」搜、只留名字里带着它的充电站；一个都没有就照旧按类目搜、如实说没找到、
声明 `_dropped_terms`。修前只按「充电站」搜，回 8 个普通站，「特斯拉」被静默丢掉。
"""
from __future__ import annotations

import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.charging_planner.src.agent import ChargingPlannerAgent
from agents.charging_planner.src.providers.base import ChargingStation

LOC = {"current_lat": "22.5", "current_lng": "113.8"}


def _agent(by_keyword):
    """按检索词回不同的站，并记下每次检索词（空串 = 按类目词搜）。"""
    agent = ChargingPlannerAgent()
    calls = []

    async def nearby(point, charger_type="", meta=None, keyword=""):
        calls.append(keyword)
        names = by_keyword.get(keyword, [])
        return [ChargingStation(id=f"s{i}", name=name, distance_km=0.5 * (i + 1), lat=22.5 + 0.01 * i, lng=113.9)
                for i, name in enumerate(names)]

    agent.charging.find_nearby = nearby
    return agent, calls


def _find(agent, text):
    return asyncio.run(run_handle(agent, "charging.find", slots={}, raw_text=text, ctx=make_context(),
                                  meta=dict(LOC)))


def test_a_named_brand_is_searched_and_only_its_charging_stations_are_kept():
    agent, calls = _agent({"特斯拉充电站": ["特斯拉超级充电站(深圳湾)", "特斯拉中心(深圳湾店)", "特来电充电站"],
                           "": ["国网充电站"]})
    res = _find(agent, "附近有特斯拉的充电站吗")
    assert calls == ["特斯拉充电站"]
    assert [item["name"] for item in res.data["items"]] == ["特斯拉超级充电站(深圳湾)"]   # 展厅、别家的站都不算
    assert "为您找到 1 个特斯拉充电站" in res.speech and "_dropped_terms" not in res.data


def test_a_named_brand_that_is_not_nearby_is_said_so_and_the_list_is_marked():
    agent, calls = _agent({"特斯拉充电站": ["特来电充电站", "特斯拉中心(深圳湾店)"],
                           "": ["国网充电站", "特来电充电站"]})
    res = _find(agent, "附近有特斯拉的充电站吗")
    assert calls == ["特斯拉充电站", ""]
    assert res.speech.startswith("附近没找到特斯拉的充电站，为您找到 2 个充电站"), res.speech
    assert res.data["_dropped_terms"] == ["特斯拉"]       # 后续步不把普通站当成特斯拉站去绑


def test_a_generic_ask_searches_by_category_once():
    agent, calls = _agent({"": ["国网充电站"]})
    res = _find(agent, "附近有充电站吗")
    assert calls == [""]
    assert "为您找到 1 个充电站" in res.speech and "_dropped_terms" not in res.data

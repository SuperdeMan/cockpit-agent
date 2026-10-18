"""原话点名了站名的找站（200×5 后续批 §21.3；带目的地的 §22.1）。

「附近有特斯拉的充电站吗」先按「特斯拉充电站」搜、只留名字里带着它的充电站；一个都没有就照旧按类目搜、如实说没找到、
声明 `_dropped_terms`。修前只按「充电站」搜，回 8 个普通站，「特斯拉」被静默丢掉。带目的地（「导航去深圳北站，在附近找个
特斯拉充电桩」）同理在目的地附近试搜；没有点名的站时照旧把最优的普通站作途经点，话术如实说没找到。
"""
from __future__ import annotations

import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.charging_planner.src.agent import ChargingPlannerAgent
from agents.charging_planner.src.providers.base import ChargingStation, GeoPoint

LOC = {"current_lat": "22.5", "current_lng": "113.8"}


def _agent(by_keyword):
    """按检索词回不同的站，并记下每次检索词（空串 = 按类目词搜）。"""
    agent = ChargingPlannerAgent()
    calls = []

    async def nearby(point, charger_type="", meta=None, keyword=""):
        calls.append(keyword)
        agent.searched_at.append(point.address)
        names = by_keyword.get(keyword, [])
        return [ChargingStation(id=f"s{i}", name=name, distance_km=0.5 * (i + 1), lat=22.5 + 0.01 * i, lng=113.9)
                for i, name in enumerate(names)]

    agent.charging.find_nearby = nearby
    agent.searched_at = []
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


def _find_near(agent, text, destination="深圳北站"):
    return asyncio.run(run_handle(agent, "charging.find", slots={"destination": destination}, raw_text=text,
                                  ctx=make_context(), meta=dict(LOC)))


def test_a_named_brand_near_the_destination_becomes_the_waypoint():
    agent, calls = _agent({"特斯拉充电站": ["特来电充电站", "特斯拉超级充电站(深圳北站)"], "": ["国网充电站"]})
    res = _find_near(agent, "导航去深圳北站，在附近找个特斯拉充电桩")
    assert calls == ["特斯拉充电站"] and agent.searched_at == ["深圳北站"]      # 在目的地附近试搜，不是当前位置
    assert res.data["waypoint"]["name"] == "特斯拉超级充电站(深圳北站)"
    assert [item["name"] for item in res.data["items"]] == ["特斯拉超级充电站(深圳北站)"]
    assert res.speech.startswith("前往深圳北站，") and "_dropped_terms" not in res.data


def test_a_named_brand_missing_near_the_destination_still_adds_the_best_station_and_says_so():
    agent, calls = _agent({"特斯拉充电站": ["特斯拉中心(北站店)"], "": ["国网充电站", "特来电充电站"]})
    res = _find_near(agent, "在深圳北站附近找个特斯拉充电桩")
    assert calls == ["特斯拉充电站", ""]
    assert res.data["waypoint"]["name"] == "国网充电站"            # 补电的诉求照办：最优的普通站照旧作途经点
    assert res.speech.startswith("深圳北站附近没找到特斯拉的充电站，可以在目的地附近的国网充电站补电"), res.speech
    assert res.data["_dropped_terms"] == ["特斯拉"]
    assert res.data["choice_reason"].startswith("附近没有特斯拉的充电站")


def test_a_named_station_far_from_the_destination_is_not_nearby():
    """高德周边检索近处没有时会给十几公里外的（深圳北站附近岚图最近 10.6 km）：作途经点就是为它绕一趟，不算「附近」。"""
    agent, calls = _agent({"岚图充电站": [], "": ["国网充电站"]})
    plain = agent.charging.find_nearby

    async def far(point, charger_type="", meta=None, keyword=""):
        found = await plain(point, charger_type=charger_type, meta=meta, keyword=keyword)
        if keyword:
            found = [ChargingStation(id="far", name="岚图超级充电站(南山)", distance_km=10.6, lat=22.6, lng=113.9)]
        return found

    agent.charging.find_nearby = far
    res = _find_near(agent, "导航去深圳北站，在附近找个岚图充电站")
    assert calls == ["岚图充电站", ""]
    assert res.data["waypoint"]["name"] == "国网充电站"
    assert res.speech.startswith("深圳北站附近没找到岚图的充电站") and res.data["_dropped_terms"] == ["岚图"]


def test_a_failed_named_search_near_the_destination_does_not_claim_none_exists():
    from agents._sdk.http import ProviderError

    agent, calls = _agent({"": ["国网充电站"]})
    plain = agent.charging.find_nearby

    async def flaky(point, charger_type="", meta=None, keyword=""):
        if keyword:
            calls.append(keyword)
            raise ProviderError("timeout")
        return await plain(point, charger_type=charger_type, meta=meta, keyword=keyword)

    agent.charging.find_nearby = flaky
    res = _find_near(agent, "导航去深圳北站，在附近找个特斯拉充电桩")
    assert calls == ["特斯拉充电站", ""]
    assert res.data["waypoint"]["name"] == "国网充电站"
    assert "没找到" not in res.speech and "_dropped_terms" not in res.data


def test_a_generic_ask_near_the_destination_searches_by_category_once():
    agent, calls = _agent({"": ["国网充电站"]})
    res = _find_near(agent, "导航去深圳北站，在附近找个充电桩")
    assert calls == [""] and res.data["waypoint"]["name"] == "国网充电站"
    assert "没找到" not in res.speech and "_dropped_terms" not in res.data


def test_the_destination_is_located_once_for_both_searches():
    """§22.6：点名试搜落空再按类目搜，同一个目的地修前编码两次——线上第二次撞了高德 QPS 限额，答成「服务不可用」。"""
    agent, calls = _agent({"特斯拉充电站": [], "": ["国网充电站"]})
    plain, located, seen = agent.charging.find_nearby, [], []

    async def locate(point, meta=None):
        located.append(point.address)
        return GeoPoint(address=point.address, lat=22.61, lng=114.03)

    async def nearby(point, charger_type="", meta=None, keyword=""):
        seen.append((point.lat, point.lng))
        return await plain(point, charger_type=charger_type, meta=meta, keyword=keyword)

    agent.charging.locate, agent.charging.find_nearby = locate, nearby
    res = _find_near(agent, "导航去深圳北站，在附近找个特斯拉充电桩")
    assert located == ["深圳北站"] and seen == [(22.61, 114.03), (22.61, 114.03)]
    assert res.data["waypoint"]["name"] == "国网充电站"


def test_the_amap_provider_turns_a_place_name_into_coordinates_once():
    from agents.charging_planner.src.providers.amap import AmapChargingProvider

    provider, asked = AmapChargingProvider("test-key"), []

    async def resolve(point, meta=None):
        asked.append(point.address)
        return "114.03,22.61" if point.address == "深圳北站" else None

    provider._poi.resolve_location = resolve
    point = asyncio.run(provider.locate(GeoPoint(address="深圳北站")))
    assert (point.address, point.lat, point.lng) == ("深圳北站", 22.61, 114.03)
    assert asyncio.run(provider.locate(GeoPoint(address="不存在的地方"))) == GeoPoint(address="不存在的地方")
    assert asyncio.run(provider.locate(point)) is point and asked == ["深圳北站", "不存在的地方"]

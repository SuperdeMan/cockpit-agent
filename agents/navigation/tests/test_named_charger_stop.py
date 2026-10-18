"""导航顺路停靠充电站时认原话点名的站名（200×5 后续批 §22.5）。

「导航去深圳北站，在附近找个零跑充电站」常被规划成只导航一步、`stop_category: 充电站`（站名丢在槽外），导航照类目列站、
不提品牌；写进槽里的（「零跑充电站」）修前按字面搜，高德的模糊结果不按名字过滤，会把普通站念成「零跑充电站」。
"""
from __future__ import annotations

import asyncio

from agents._sdk.http import ProviderError
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI


def _poi(name: str, lat: float = 22.61, lng: float = 114.03) -> POI:
    return POI(id=name, name=name, address="深圳市龙华区", lat=lat, lng=lng)


class _Poi:
    """按检索词回结果，记下每次检索词；`fail` 里的词抛服务错误。"""

    def __init__(self, responses, fail=()):
        self.responses, self.fail, self.queries = responses, set(fail), []

    async def search(self, keyword, **kwargs):
        self.queries.append(keyword)
        if keyword in self.fail:
            raise ProviderError("timeout")
        return list(self.responses.get(keyword, []))


def _navigate(poi, raw_text, stop_category="充电站"):
    agent = NavigationAgent()
    agent.poi = poi
    return asyncio.run(run_handle(agent, "navigation.navigate_to",
                                  slots={"destination": "深圳北站", "stop_category": stop_category},
                                  raw_text=raw_text, ctx=make_context()))


def test_a_brand_named_outside_the_slot_lists_only_its_stations():
    poi = _Poi({"深圳北站": [_poi("深圳北站")],
                "零跑充电站": [_poi("零跑超充站(北站)"), _poi("特来电充电站(北站)")], "充电站": [_poi("国网充电站")]})
    res = _navigate(poi, "导航去深圳北站，在附近找个零跑充电站")
    assert "零跑充电站" in poi.queries and "充电站" not in poi.queries
    assert [item["name"] for item in res.ui_card["items"]] == ["零跑超充站(北站)"]
    assert "的零跑充电站有：零跑超充站(北站)" in res.speech and "没找到" not in res.speech
    assert "_dropped_terms" not in res.data


def test_a_brand_written_into_the_slot_is_not_read_out_over_generic_stations():
    poi = _Poi({"深圳北站": [_poi("深圳北站")],
                "零跑充电站": [_poi("一能充电站(裕和大厦)")], "充电站": [_poi("国网充电站"), _poi("一能充电站(裕和大厦)")]})
    res = _navigate(poi, "导航去深圳北站，在附近找个零跑充电站", stop_category="零跑充电站")
    assert "没找到零跑的充电站，" in res.speech and "的充电站有：国网充电站" in res.speech
    assert "零跑充电站有" not in res.speech                     # 修前：「目的地附近的零跑充电站有：一能充电站…」
    assert res.ui_card["title"].startswith("顺路充电站") and res.data["_dropped_terms"] == ["零跑"]
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "深圳北站"          # 导航照发，途经点留给用户挑


def test_a_brand_only_in_the_slot_still_filters_by_name():
    """这句话没说品牌、槽里有（上一句说过，规划器带进来）：照样按名字过滤，不把普通站当成它。"""
    poi = _Poi({"深圳北站": [_poi("深圳北站")], "零跑充电站": [_poi("一能充电站(裕和大厦)")],
                "充电站": [_poi("国网充电站")]})
    res = _navigate(poi, "导航去深圳北站，路上顺便充个电", stop_category="零跑充电站")
    assert "没找到零跑的充电站，" in res.speech and "零跑充电站有" not in res.speech


def test_a_failed_named_search_does_not_claim_none_exists():
    poi = _Poi({"深圳北站": [_poi("深圳北站")], "充电站": [_poi("国网充电站")]}, fail={"零跑充电站"})
    res = _navigate(poi, "导航去深圳北站，在附近找个零跑充电站")
    assert "没找到" not in res.speech and "的充电站有：国网充电站" in res.speech
    assert "_dropped_terms" not in res.data


def test_a_generic_charger_stop_searches_by_category_once():
    poi = _Poi({"深圳北站": [_poi("深圳北站")], "充电站": [_poi("国网充电站")]})
    res = _navigate(poi, "导航去深圳北站，在附近找个充电桩")
    assert [q for q in poi.queries if "充电" in q] == ["充电站"]
    assert "没找到" not in res.speech and "_dropped_terms" not in res.data

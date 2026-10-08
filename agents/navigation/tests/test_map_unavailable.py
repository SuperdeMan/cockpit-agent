"""地图检索报错 ≠ 没找到（2026-10-08）。

高德基础搜索日配额用尽时，每次检索都报 `USER_DAILY_QUERY_OVER_LIMIT (10044)`；目的地解析把报错吞成空列表，用户听到的是
「暂时无法确定「海岸城」对应的具体地点」，还被要求补充城市——补了也一样查不到。一段地点解析里检索一次都没成功、却有报错
⇒ 如实说地图服务暂时不可用（与 `search_poi` 的诚实降级同一句）。
"""
from __future__ import annotations

import asyncio

from agents._sdk.http import ProviderError
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SZ = {"current_lat": "22.5410", "current_lng": "113.9412"}
_QUOTA = "amap place_around failed: USER_DAILY_QUERY_OVER_LIMIT (10044)"
_COAST = POI(id="c1", name="南山海岸城购物中心", category="购物服务;商场;购物中心", lat=22.517, lng=113.936, city="深圳市")


class _Poi:
    """`down` 里的关键字一律报错（像配额用尽，"*" = 全部），其余按表返回（缺省空）。"""

    def __init__(self, down=(), table=None, delay=0.0):
        self.down, self.table, self.delay = set(down), dict(table or {}), delay

    async def search(self, keyword, near=None, **kw):
        await asyncio.sleep(self.delay)
        if "*" in self.down or keyword in self.down:
            raise ProviderError(_QUOTA)
        return list(self.table.get(keyword, []))

    async def get_route(self, a, b, **kw):
        return {"distance_km": 3.0, "duration_min": 9}


class _NoLandmarkLlm:
    async def complete(self, *args, **kwargs):
        return "[]"


def _agent(poi):
    agent = NavigationAgent()
    agent.poi = poi
    agent.llm = _NoLandmarkLlm()
    return agent


def _run(agent, intent, slots, raw):
    return asyncio.run(run_handle(agent, intent, slots=slots, raw_text=raw, ctx=make_context(), meta=dict(SZ)))


def _honest(what):
    return f"地图服务暂时不可用，没查到「{what}」，请稍后再试。"


def test_navigate_to_says_the_map_service_is_down_instead_of_asking_for_a_city():
    res = _run(_agent(_Poi(down={"*"})), "navigation.navigate_to", {"destination": "海岸城"}, "导航去海岸城")
    assert res.speech == _honest("海岸城") and not res.actions
    assert res.status == "ok" and not res.missing_slots          # 不追问：补了城市也一样查不到


def test_estimate_and_route_traffic_say_the_same():
    for intent, raw in (("navigation.estimate", "去海岸城要开多久"), ("navigation.route_traffic", "去海岸城路上堵不堵")):
        res = _run(_agent(_Poi(down={"*"})), intent, {"destination": "海岸城"}, raw)
        assert res.speech == _honest("海岸城") and not res.actions, (intent, res.speech)


def test_the_origin_is_judged_on_its_own_lookups():
    """终点查到了、起点那几次检索全报错 ⇒ 起点照样如实说服务不可用（只看起点这一段，不被终点那次成功盖掉）。"""
    poi = _Poi(down={"欢乐海岸"}, table={"海岸城": [_COAST]})
    res = _run(_agent(poi), "navigation.estimate", {"destination": "海岸城", "origin": "欢乐海岸"}, "从欢乐海岸去海岸城要多久")
    assert res.speech == _honest("欢乐海岸")
    res = _run(_agent(poi), "navigation.navigate_to", {"destination": "海岸城", "origin": "欢乐海岸"}, "从欢乐海岸导航去海岸城")
    assert res.speech == _honest("欢乐海岸") and not res.actions


def test_an_empty_search_is_still_not_found():
    """对照：检索成功、只是没有结果 ⇒ 照旧「没找到」并追问。"""
    res = _run(_agent(_Poi()), "navigation.navigate_to", {"destination": "云岚国际中心"}, "导航去云岚国际中心")
    assert res.status == "need_slot" and "暂时无法确定" in res.speech


def test_a_lookup_that_worked_once_is_not_called_unavailable():
    """同一段解析里近处检索成功（搜到一个不相干的点），本城 / 全国 / 外地本体三次都报错 ⇒ 不算服务不可用，照旧「没找到」并追问。"""
    shop = POI(id="m1", name="美宜佳(华富洋大厦店)", category="购物服务;便利店;便利店", lat=22.5401, lng=113.9420,
               city="深圳市")

    class _NearOnly(_Poi):
        async def search(self, keyword, near=None, **kw):
            if near is None:
                raise ProviderError(_QUOTA)
            return [shop]

    res = _run(_agent(_NearOnly()), "navigation.navigate_to", {"destination": "云岚国际中心"}, "导航去云岚国际中心")
    assert res.status == "need_slot" and "暂时无法确定" in res.speech


def test_concurrent_requests_do_not_share_the_lookup_log():
    """每个请求一份检索记录：一个请求的报错不会让同时进行的另一个请求说服务不可用。"""
    agent = _agent(_Poi(down={"海岸城"}, delay=0.01))

    async def both():
        return await asyncio.gather(
            run_handle(agent, "navigation.navigate_to", slots={"destination": "海岸城"}, raw_text="导航去海岸城",
                       ctx=make_context(), meta=dict(SZ)),
            run_handle(agent, "navigation.navigate_to", slots={"destination": "云岚国际中心"}, raw_text="导航去云岚国际中心",
                       ctx=make_context(), meta=dict(SZ)))

    down, empty = asyncio.run(both())
    assert down.speech == _honest("海岸城")
    assert empty.status == "need_slot" and "暂时无法确定" in empty.speech

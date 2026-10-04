"""远处地标不被近处借名地点顶替（docs/design/2026-10-04-destination-borrowed-name.md）。

2026-10-04 真栈（深圳南山出发、真实高德 + 模型）：20 个外地知名地标里 5 个被本地借名地点顶替——「南京南站」→「南京南站南广场」4 km、
「东方明珠」→「东方·明珠城」38 km、「外滩」→「外滩夜市」31 km、「黄鹤楼」→「小黄鹤楼餐馆」17 km、「故宫」→「锦绣中华-故宫」5 km。
"""
from __future__ import annotations

import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SZ = {"current_lat": "22.5410", "current_lng": "113.9412"}
_LOCAL = POI(id="n1", name="小黄鹤楼餐馆", category="餐饮服务;中餐厅;中餐厅", lat=22.60, lng=114.05)
_FAR = POI(id="w1", name="黄鹤楼", category="风景名胜;风景名胜;国家级景点", lat=30.5450, lng=114.3020, city="武汉市")


class _Poi:
    def __init__(self, near, wide):
        self.near, self.wide, self.calls = near, wide, []

    async def search(self, keyword, near=None, **kw):
        self.calls.append((keyword, near is not None))
        return list(self.near if near is not None else self.wide)

    async def get_route(self, origin, dest, **kw):
        return {"distance_km": 1100.0, "duration_min": 660.0, "path": []}


class _KV:
    """会话共享态：候选写进去、续接轮「第N个」读出来。"""
    def __init__(self):
        self.kv = {}


def _ctx(kv):
    ctx = make_context()

    async def save(key, value):
        kv.kv[key] = value

    async def load(key):
        return kv.kv.get(key)

    ctx.save_shared_state = save
    ctx.load_shared_state = load
    return ctx


def _agent(near, wide):
    agent = NavigationAgent()
    agent.poi = _Poi(near, wide)

    async def no_landmark(description):   # 模型地标解析没给出结果（真栈「厦门火车站」那一次）
        return []
    agent._landmark_candidates = no_landmark
    return agent


def test_a_borrowed_local_name_with_a_far_landmark_is_asked_not_navigated():
    agent, kv = _agent([_LOCAL], [_FAR]), _KV()
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "黄鹤楼"},
                                 raw_text="导航去黄鹤楼", ctx=_ctx(kv), meta=dict(SZ)))
    assert res.status == "need_slot" and not res.actions
    assert res.ui_card["purpose"] == "dest_choice"
    assert [it["name"] for it in res.ui_card["items"]] == ["武汉黄鹤楼", "小黄鹤楼餐馆"]
    assert "武汉黄鹤楼" in res.speech and "小黄鹤楼餐馆" in res.speech
    assert [it["name"] for it in kv.kv["navigation_dest_choices"]["items"]] == ["武汉黄鹤楼", "小黄鹤楼餐馆"]


def test_the_ordinal_answer_navigates_to_the_chosen_place_without_searching_again():
    """问的那一轮把候选连坐标一起存下；续接轮「第一个」就是那个具体地点，不再重新搜（重新搜要靠模型地标解析，会波动）。"""
    agent, kv = _agent([_LOCAL], [_FAR]), _KV()
    asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "黄鹤楼"},
                           raw_text="导航去黄鹤楼", ctx=_ctx(kv), meta=dict(SZ)))
    resume = _agent([], [])
    res = asyncio.run(run_handle(resume, "navigation.navigate_to", slots={"destination": "第一个"},
                                 raw_text="第一个", ctx=_ctx(kv), meta=dict(SZ)))
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 30.5450) < 1e-6
    assert resume.poi.calls == [] and kv.kv["navigation_dest_choices"] == {}


def test_a_clicked_name_resumes_the_same_way():
    """车机点选会把候选名当槽值发回：同样认出是候选里的那个地点。"""
    agent, kv = _agent([_LOCAL], [_FAR]), _KV()
    asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "黄鹤楼"},
                           raw_text="导航去黄鹤楼", ctx=_ctx(kv), meta=dict(SZ)))
    resume = _agent([], [])
    res = asyncio.run(run_handle(resume, "navigation.navigate_to", slots={"destination": "小黄鹤楼餐馆"},
                                 raw_text="小黄鹤楼餐馆", ctx=_ctx(kv), meta=dict(SZ)))
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 22.60) < 1e-6 and resume.poi.calls == []


def test_an_exact_local_name_is_used_without_any_extra_search():
    exact = POI(id="n1", name="南山书城(深圳湾店)", category="购物服务;专卖店;书店", lat=22.52, lng=113.94)
    agent = _agent([exact], [POI(id="w1", name="南山书城", lat=36.0, lng=120.3)])
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "南山书城"},
                                 raw_text="导航去南山书城", meta=dict(SZ)))
    assert any(a["type"] == "navigate" for a in res.actions)
    assert ("南山书城", False) not in agent.poi.calls


def test_category_searches_never_look_for_a_far_namesake():
    station = POI(id="n1", name="中国石化加油站(南山店)", category="汽车服务;加油站;中国石化", lat=22.53, lng=113.93)
    agent = _agent([station], [POI(id="w1", name="加油站", lat=39.9, lng=116.4)])
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "加油站"},
                                 raw_text="导航去加油站", meta=dict(SZ)))
    assert res.status != "need_slot"
    assert ("加油站", False) not in agent.poi.calls


def test_an_unmatched_local_fallback_gives_way_to_the_far_landmark():
    """真栈「厦门火车站」：全国重搜只看第一名（「厦门站」对不上），模型地标解析没给出结果 ⇒ 兜底退回本地对不上的「厦门园(公交站)」。
    外地另有名字对得上的（全国第二名）⇒ 直接用它，不拿一个对不上的近处结果来问。"""
    far = POI(id="w2", name="厦门火车站(地铁站)", category="交通设施服务;地铁站;地铁站", lat=24.4681, lng=118.1162, city="厦门市")
    wide = [POI(id="w1", name="厦门站", category="交通设施服务;火车站;火车站", lat=24.4686, lng=118.1166, city="厦门市"), far]
    agent = _agent([POI(id="n1", name="厦门园(公交站)", category="交通设施服务;公交车站;公交车站相关", lat=22.58, lng=113.99)], wide)
    name, point, ambiguity = asyncio.run(agent._resolve_point_checked("厦门火车站", make_context(), dict(SZ)))
    assert name == "厦门火车站(地铁站)" and abs(point.lat - 24.4681) < 1e-6 and ambiguity is None
    res = asyncio.run(run_handle(_agent(agent.poi.near, wide), "navigation.navigate_to", slots={"destination": "厦门火车站"},
                                 raw_text="导航去厦门火车站", meta=dict(SZ)))
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 24.4681) < 1e-6


def test_a_local_namesake_in_the_wide_search_is_not_a_far_one():
    """全国搜索里名字对得上的也在本地半径内 ⇒ 没有外地本体，照旧用近处结果。"""
    local_too = POI(id="w1", name="黄鹤楼大酒店", category="住宿服务;宾馆酒店;宾馆酒店", lat=22.55, lng=114.10)
    agent = _agent([_LOCAL], [local_too])
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "黄鹤楼"},
                                 raw_text="导航去黄鹤楼", meta=dict(SZ)))
    assert res.status != "need_slot" and any(a["type"] == "navigate" for a in res.actions)


def test_no_namesake_probe_when_the_top_result_is_already_far_or_has_no_coordinates():
    """近处第一名本身已在本地半径外（它就是远处那个）、或没有坐标（算不了距离）⇒ 不探查。"""
    from agents.navigation.src.providers.base import GeoPoint
    agent = _agent([], [_FAR])
    here = GeoPoint(lat=22.5410, lng=113.9412)
    far_top = POI(id="n9", name="武汉黄鹤楼公园", lat=30.54, lng=114.30)
    no_coords = POI(id="n8", name="小黄鹤楼餐馆")
    assert asyncio.run(agent._far_namesake("黄鹤楼", far_top, here, dict(SZ))) is None
    assert asyncio.run(agent._far_namesake("黄鹤楼", no_coords, here, dict(SZ))) is None
    assert agent.poi.calls == []


def test_estimate_asks_too_and_keeps_the_estimate_on_resume():
    agent, kv = _agent([_LOCAL], [_FAR]), _KV()
    res = asyncio.run(run_handle(agent, "navigation.estimate", slots={"destination": "黄鹤楼"},
                                 raw_text="去黄鹤楼要开多久", ctx=_ctx(kv), meta=dict(SZ)))
    assert res.status == "need_slot" and "算到哪一个" in res.speech and not res.actions
    resume = _agent([], [])
    again = asyncio.run(run_handle(resume, "navigation.estimate", slots={"destination": "第二个"},
                                   raw_text="第二个", ctx=_ctx(kv), meta=dict(SZ)))
    assert "小黄鹤楼餐馆" in again.speech and not again.actions      # 选了近处那个：照常估算，不发导航
    assert resume.poi.calls == []                                     # 用存下的坐标，不再重新搜


def test_amap_poi_carries_the_city():
    from agents.navigation.src.providers.amap import AmapPOIProvider
    poi = AmapPOIProvider.__new__(AmapPOIProvider)._poi_from(
        {"id": "B1", "name": "黄鹤楼", "location": "114.302,30.545", "cityname": "武汉市", "type": "风景名胜"})
    assert poi.city == "武汉市"

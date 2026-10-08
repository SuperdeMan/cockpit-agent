"""本地具名目的地不被附属地点顶替（docs/design/2026-10-04-local-destination-ranking.md）。

2026-10-04 云上导航容器测量（深圳南山出发）：具名目的地的近处搜索是高德周边搜索缺省参数（5 km、按距离排），
5 km 内借了名的店天然排第一——大梅沙→「大梅沙顶级推拿」、蛇口港→「深圳蛇口港公安局」、东门→「豪方现代东门食堂」；
「会展中心」名字对不上，全国重搜与模型兜底把人带到北京。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents._sdk.testing import run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.amap import AmapPOIProvider
from agents.navigation.src.providers.base import POI, GeoPoint

HERE = GeoPoint(lat=22.5410, lng=113.9412)
META = {"current_lat": "22.5410", "current_lng": "113.9412"}


class _Poi:
    """按调用形态返回：近处（带 near）/ 本城（region）/ 全国（都不带）。"""

    def __init__(self, near=(), city=(), wide=()):
        self.near, self.city, self.wide, self.calls = list(near), list(city), list(wide), []

    async def search(self, keyword, near=None, **kw):
        self.calls.append({"keyword": keyword, "near": near is not None, **kw})
        if near is not None:
            return list(self.near)
        return list(self.city if kw.get("region") else self.wide)


def _agent(poi):
    agent = NavigationAgent()
    agent.poi = poi

    async def no_landmark(description):
        return []
    agent._landmark_candidates = no_landmark
    return agent


def _find(agent, text, **kw):
    return asyncio.run(agent._find_destination(text, dict(META), near=HERE, **kw))


def test_a_named_destination_is_searched_by_relevance_across_the_city():
    beach = POI(id="n1", name="大梅沙海滨公园", category="风景名胜;公园广场;公园", lat=22.594, lng=114.305,
                distance_km=38.0, city="深圳市")
    agent = _agent(_Poi(near=[beach]))
    _, results = _find(agent, "大梅沙")
    assert results[0].name == "大梅沙海滨公园"
    assert agent.poi.calls[0] == {"keyword": "大梅沙", "near": True, "limit": 3, "page": 1, "meta": META,
                                  "rank": "weight", "radius_m": 50_000}


def test_category_and_nearby_searches_keep_the_nearest_first():
    station = POI(id="n1", name="中国石化加油站(科苑店)", category="汽车服务;加油站;中国石化", lat=22.54, lng=113.94)
    agent = _agent(_Poi(near=[station]))
    _find(agent, "加油站")
    _find(agent, "粤菜馆", strict=False)
    assert all("rank" not in c and "radius_m" not in c for c in agent.poi.calls)


def test_an_unmatched_name_is_looked_up_in_the_current_city_before_nationwide():
    """「会展中心」：近处综合排序第一是「科兴国际会议中心」（名字对不上）⇒ 先在深圳按名字找到「深圳会展中心」，不去全国。"""
    near = [POI(id="n1", name="科兴国际会议中心", lat=22.548, lng=113.943, distance_km=0.9, city="深圳市"),
            POI(id="n2", name="会展中心(地铁站)", lat=22.536, lng=114.055, distance_km=12.0, city="深圳市")]
    hall = POI(id="c1", name="深圳会展中心", category="科教文化服务;会展中心;会展中心", lat=22.531, lng=114.059, city="深圳市")
    beijing = POI(id="w1", name="国家会议中心", lat=40.00, lng=116.39, city="北京市")
    agent = _agent(_Poi(near=near, city=[hall], wide=[beijing]))
    _, results = _find(agent, "会展中心")
    assert results[0].name == "深圳会展中心"
    assert [(c["near"], c.get("region", "")) for c in agent.poi.calls] == [(True, ""), (False, "深圳市")]


def test_the_city_lookup_falls_through_to_nationwide_and_is_skipped_without_a_city():
    far = POI(id="w1", name="黄鹤楼", category="风景名胜;风景名胜;国家级景点", lat=30.545, lng=114.302, city="武汉市")
    local = POI(id="n1", name="小黄鹤楼餐馆", lat=22.60, lng=114.05, distance_km=17.4, city="深圳市")
    agent = _agent(_Poi(near=[local], city=[local], wide=[far]))
    _, results = _find(agent, "黄鹤楼")   # 本城只有借名的餐馆（名字包含却不是它）——包含式放行，这一步就收
    assert results[0].name == "小黄鹤楼餐馆"
    unmatched = POI(id="n2", name="科兴科学园", lat=22.55, lng=113.94, distance_km=1.0)   # 测试桩不带城市
    agent = _agent(_Poi(near=[unmatched], wide=[far]))
    _, results = _find(agent, "黄鹤楼")
    assert results[0].name == "黄鹤楼"
    assert [(c["near"], c.get("region", "")) for c in agent.poi.calls] == [(True, ""), (False, "")]


def test_the_current_city_is_taken_from_the_nearest_result():
    border = POI(id="n1", name="东莞某处", distance_km=30.0, city="东莞市")
    here = POI(id="n2", name="深圳某处", distance_km=2.0, city="深圳市")
    unknown = POI(id="n3", name="没有距离", city="惠州市")
    assert NavigationAgent._nearest_city([border, here, unknown]) == "深圳市"
    assert NavigationAgent._nearest_city([POI(id="x", name="无城市")]) == ""


def test_amap_builds_the_relevance_and_city_parameters():
    seen = []
    provider = AmapPOIProvider.__new__(AmapPOIProvider)

    async def resolve(near, meta):
        return "113.9412,22.5410" if near is not None else None

    async def get(path, params, op, meta):
        seen.append((path, params))
        return {"pois": []}
    provider._resolve_location = resolve
    provider._get = get
    asyncio.run(provider.search("大梅沙", near=HERE, rank="weight", radius_m=50_000))
    asyncio.run(provider.search("会展中心", region="深圳市"))
    asyncio.run(provider.search("加油站", near=HERE))
    asyncio.run(provider.search("故宫"))
    (p1, around), (p2, text), (p3, plain), (p4, nationwide) = seen
    assert p1 == "/v5/place/around" and around["sortrule"] == "weight" and around["radius"] == "50000"
    assert p2 == "/v5/place/text" and text["region"] == "深圳市" and text["city_limit"] == "true"
    assert p3 == "/v5/place/around" and "sortrule" not in plain and "radius" not in plain
    assert p4 == "/v5/place/text" and "region" not in nationwide


def test_a_place_named_only_the_tail_of_the_words_is_not_a_match():
    """A/B（2026-10-04）：半径放大到城市级后，深圳一个就叫「火车站」的点顶掉了「厦门火车站」——地点名只是原话的尾巴，
    缺了最有区分度的「厦门」。原话以地点名打头、或原话 = 该地点所在城市 + 名字，照旧算。"""
    m = NavigationAgent._dest_matches
    assert m("厦门火车站", "火车站") is False
    assert m("厦门火车站", "火车站", "深圳市") is False
    assert m("南京南站高铁站", "南京南站") is True
    assert m("上海外滩", "外滩", "上海市") is True
    assert m("上海外滩", "外滩") is False
    assert m("广州塔", "广州塔(广州地标)") is True        # 正向包含不变


def test_a_city_search_namesake_farther_out_still_checks_for_the_famous_one():
    """A/B（2026-10-04）：「鼓浪屿」在本城搜索里找到「深圳鼓浪屿」（58 km，去掉城市前缀完全同名）——城市级半径外的同名
    不算「这座城的那一个」，外地有以原话打头的本体（厦门鼓浪屿）就问。半径内的「深圳欢乐谷」照旧不问、不多搜。"""
    xiamen = POI(id="w1", name="鼓浪屿风景名胜区", category="风景名胜;风景名胜;国家级景点", lat=24.447, lng=118.067, city="厦门市")
    agent = _agent(_Poi(wide=[xiamen]))
    far_twin = POI(id="c1", name="深圳鼓浪屿", lat=22.60, lng=114.50, city="深圳市")       # 约 58 km
    assert asyncio.run(agent._far_namesake("鼓浪屿", far_twin, HERE, dict(META))) is xiamen
    near_twin = POI(id="n1", name="深圳欢乐谷", lat=22.59, lng=113.98, city="深圳市")     # 约 7 km
    agent = _agent(_Poi(wide=[POI(id="w2", name="北京欢乐谷", lat=39.87, lng=116.49, city="北京市")]))
    assert asyncio.run(agent._far_namesake("欢乐谷", near_twin, HERE, dict(META))) is None
    assert agent.poi.calls == []


class _KeyedPoi:
    """近处 / 本城 / 全国三种搜索各按关键字返回。"""

    def __init__(self, near=None, city=None, wide=None):
        self.near, self.city, self.wide, self.calls, self.limits = near or {}, city or {}, wide or {}, [], []

    async def search(self, keyword, near=None, **kw):
        self.calls.append((keyword, near is not None, kw.get("region", "")))
        self.limits.append(kw.get("limit"))
        table = self.near if near is not None else (self.city if kw.get("region") else self.wide)
        return list(table.get(keyword, []))[:kw.get("limit") or None]


def _guessing_agent(poi, candidates):
    agent = NavigationAgent()
    agent.poi = poi

    async def landmark(description):
        return list(candidates)
    agent._landmark_candidates = landmark
    return agent


def test_a_guessed_name_is_looked_up_in_the_city_first():
    """A/B（2026-10-04）：「山姆会员店」名字对不上（门店叫「山姆会员商店」），模型兜底给出「山姆会员商店」，
    候选搜索不带位置取全国第一——北京石景山店 1941 km。城市里就有前海店。"""
    qianhai = POI(id="n1", name="山姆会员商店(前海店)", category="购物服务;综合市场;仓储超市", lat=22.53, lng=113.90,
                  distance_km=3.5, city="深圳市")
    beijing = POI(id="w1", name="山姆会员商店(北京石景山店)", lat=39.90, lng=116.20, city="北京市")
    poi = _KeyedPoi(near={"山姆会员店": [qianhai], "山姆会员商店": [qianhai]},
                    city={"山姆会员店": [qianhai]}, wide={"山姆会员店": [beijing], "山姆会员商店": [beijing]})
    _, results = asyncio.run(_guessing_agent(poi, ["山姆会员商店"])._find_destination(
        "山姆会员店", dict(META), near=HERE))
    assert results[0].name == "山姆会员商店(前海店)"


def test_a_loose_local_lookalike_does_not_take_the_guessed_name():
    """只认严格对上的本地结果：「厦门站」的近处只有「厦门园(公交站)」（共享「厦门」两个字），照旧去全国找厦门站。"""
    bus_stop = POI(id="n1", name="厦门园(公交站)", category="交通设施服务;公交车站;公交车站相关", lat=22.58, lng=113.99,
                   distance_km=8.0, city="深圳市")
    xiamen = POI(id="w1", name="厦门站", category="交通设施服务;火车站;火车站", lat=24.4686, lng=118.1166, city="厦门市")
    poi = _KeyedPoi(near={"厦门火车站": [bus_stop], "厦门站": [bus_stop]}, wide={"厦门站": [xiamen]})
    _, results = asyncio.run(_guessing_agent(poi, ["厦门站"])._find_destination("厦门火车站", dict(META), near=HERE))
    assert results[0].name == "厦门站"


# ── 带类目词的通称（医院 / 银行 / 超市…）不悄悄跨城（2026-10-04：「儿童医院」→ 北京儿童医院 1942 km）────────────
_CLINIC = POI(id="n1", name="知贝医疗深圳门店", category="医疗保健服务;诊所;诊所", lat=22.55, lng=113.95,
              distance_km=2.3, city="深圳市")
_SZ_CHILDREN = POI(id="c1", name="深圳市儿童医院", category="医疗保健服务;综合医院;三级甲等医院", lat=22.553, lng=114.046,
                   city="深圳市")
_BJ_CHILDREN = POI(id="w1", name="首都医科大学附属北京儿童医院", category="医疗保健服务;专科医院;儿童医院",
                   lat=39.913, lng=116.353, city="北京市")


def _shop(name, category, km):
    return POI(id=name, name=name, category=category, lat=22.5410 + km / 111, lng=113.9412, distance_km=km, city="深圳市")


def test_a_category_name_scans_the_near_results_for_its_kind():
    """真栈近处候选（2026-10-04）：深圳市儿童医院排第 8，前面是诊所、儿童诊所、妇幼保健院、它自己的科室。"""
    near = [_CLINIC, _shop("深圳小米熊儿童诊所", "医疗保健服务;医疗保健服务场所;医疗保健服务场所", 3.7),
            _shop("深圳市南山妇幼保健院", "医疗保健服务;综合医院;三级甲等医院", 6.1),
            _shop("深圳市福田区妇幼保健院(安托山院区)", "医疗保健服务;专科医院;专科医院", 6.4),
            _shop("深圳市儿童医院项目部", "公司企业;公司;公司", 11.5),
            _shop("深圳市儿童医院新生儿科", "医疗保健服务;医疗保健服务场所;医疗保健服务场所", 11.5),
            _shop("深圳市儿童医院风湿免疫科", "医疗保健服务;医疗保健服务场所;医疗保健服务场所", 11.6),
            _SZ_CHILDREN, _shop("深圳市儿童医院门诊大厅", "医疗保健服务;专科医院;专科医院", 11.7)]
    poi = _KeyedPoi(near={"儿童医院": near}, wide={"儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("儿童医院", dict(META), near=HERE))
    assert results[0].name == "深圳市儿童医院"
    assert len(results) == 3                                   # 多取的候选返回前截回 limit：卡片与「等 N 家」不变
    assert poi.calls == [("儿童医院", True, "")] and poi.limits == [10]
    poi.limits.clear()
    asyncio.run(_guessing_agent(poi, [])._find_destination("儿童医院", dict(META), near=HERE, page=2))
    assert poi.limits[0] == 3                                  # 「换一批」翻页按原每页条数取，偏移不错位


def test_a_chain_name_takes_the_nearest_branch():
    """「华润万家超市」最近那家叫「华润万家标超南区」，包含式够不着；主干 + 超市类目接住它。天虹在高德记作「商场」。"""
    near = [_shop("华润万家标超南区(科兴科学园店)", "购物服务;超级市场;华润", 0.8),
            _shop("华润万家(南新店)", "购物服务;超级市场;华润", 2.3)]
    poi = _KeyedPoi(near={"华润万家超市": near}, city={"华润万家超市": [_shop("华润万家", "购物服务;超级市场;华润", 9.1)]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("华润万家超市", dict(META), near=HERE))
    assert results[0].name == "华润万家标超南区(科兴科学园店)" and len(poi.calls) == 1
    near = [_shop("常兴广场(天虹商场南山店)", "购物服务;服装鞋帽皮具店;服装鞋帽皮具店", 2.2),
            _shop("天虹商场(深圳南山常兴店)", "购物服务;商场;购物中心|购物服务;商场;普通商场", 2.2)]
    poi = _KeyedPoi(near={"天虹超市": near})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("天虹超市", dict(META), near=HERE))
    assert results[0].name == "天虹商场(深圳南山常兴店)"


def test_the_kind_check_keeps_other_shops_out_of_a_hospital_name():
    near = [_shop("禾视眼科·近视防控配镜", "购物服务;专卖店;眼镜店", 1.1),
            _shop("深圳爱尔西柚眼科", "医疗保健服务;专科医院;眼科医院", 1.2)]
    poi = _KeyedPoi(near={"眼科医院": near})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("眼科医院", dict(META), near=HERE))
    assert results[0].name == "深圳爱尔西柚眼科"


def test_category_names_search_the_city_only_as_a_last_resort():
    """本城重搜按相关度排，会把连锁店带到远处的大店（A/B：华润万家超市 0.8 km → 9.1 km）：近处扫描接得住的类目查询不走它。
    近处、全国、模型猜名都没对上时才在本城找一次（2026-10-08：「北大医院」的北京大学深圳医院在 11 km 外，5 km 的就近扫描够不着），
    名字 + 类目对得上才算。"""
    near_store = _shop("华润万家标超南区(科兴科学园店)", "购物服务;超级市场;华润", 0.8)
    poi = _KeyedPoi(near={"华润万家超市": [near_store]},
                    city={"华润万家超市": [_shop("华润万家(大冲店)", "购物服务;超级市场;超市", 9.1)]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("华润万家超市", dict(META), near=HERE))
    assert results[0].name == near_store.name and not [call for call in poi.calls if call[2]]
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, city={"儿童医院": [_SZ_CHILDREN]}, wide={"儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("儿童医院", dict(META), near=HERE))
    assert results[0].name == "深圳市儿童医院"
    assert [call for call in poi.calls if call[2]] == [("儿童医院", False, "深圳市")]
    assert poi.calls.index(("儿童医院", False, "深圳市")) > poi.calls.index(("儿童医院", False, ""))   # 全国之后


def test_a_bare_category_takes_the_nearest_result_whatever_its_name():
    """「便利店」最近的那家叫「7-ELEVEn」；「医院」全国第一是北京协和医院（改前真栈：1942 km）。"""
    seven = _shop("7-ELEVEn(海王银河科技大厦店)", "购物服务;便民商店/便利店;便民商店/便利店", 0.1)
    far = POI(id="w1", name="便利店", lat=39.9, lng=116.4, city="北京市")
    poi = _KeyedPoi(near={"便利店": [seven]}, wide={"便利店": [far]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("便利店", dict(META), near=HERE))
    assert results[0].name == seven.name and len(poi.calls) == 1
    station = _shop("南山区医疗集团总部高新社康服务站", "医疗保健服务;综合医院;卫生院", 0.8)
    poi = _KeyedPoi(near={"医院": [station]}, wide={"医院": [POI(id="w2", name="北京协和医院", lat=39.9, lng=116.4,
                                                                city="北京市")]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("医院", dict(META), near=HERE))
    assert results[0].name == station.name


def test_a_category_name_does_not_jump_to_another_city_it_did_not_name():
    # 全国重搜与模型猜名（「北京儿童医院」）都解析到北京：两处都拦
    # 近处只有名字毫不相干的诊所（「知贝医疗深圳门店」之于「儿童医院」）⇒ 也不当目的地：没找到，调用方追问（2026-10-08 起；
    # 此前兜底照样导去，话术报出实际名让用户纠正）。名字沾边的近处结果照旧采信，见下
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, wide={"儿童医院": [_BJ_CHILDREN], "北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京儿童医院"])._find_destination("儿童医院", dict(META), near=HERE))
    assert results == []
    assert ("北京儿童医院", False, "") in poi.calls                 # 猜名确实解析过
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, wide={"北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京儿童医院"])._find_destination("儿童医院", dict(META), near=HERE))
    assert results == []                                            # 只有猜名到了北京
    kids_clinic = _shop("深圳小米熊儿童诊所", "医疗保健服务;医疗保健服务场所;医疗保健服务场所", 3.7)
    poi = _KeyedPoi(near={"儿童医院": [kids_clinic]}, wide={"北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京儿童医院"])._find_destination("儿童医院", dict(META), near=HERE))
    assert [r.name for r in results] == ["深圳小米熊儿童诊所"]      # 沾边（「儿童」）：本地实际结果，话术报出实际名
    poi = _KeyedPoi(near={"北京儿童医院": [_CLINIC]}, wide={"北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("北京儿童医院", dict(META), near=HERE))
    assert results[0].name == "首都医科大学附属北京儿童医院"        # 点了城市：照常跨城


def test_the_cross_city_rule_only_binds_category_names():
    far = POI(id="w1", name="黄鹤楼", lat=30.545, lng=114.302, city="武汉市")
    assert NavigationAgent._cross_city_ok("黄鹤楼", far, HERE) is True
    assert NavigationAgent._cross_city_ok("儿童医院", _BJ_CHILDREN, HERE) is False
    assert NavigationAgent._cross_city_ok("北京市儿童医院", _BJ_CHILDREN, HERE) is True
    assert NavigationAgent._cross_city_ok("儿童医院", _SZ_CHILDREN, HERE) is True      # 本地半径内不受限
    assert NavigationAgent._cross_city_ok("儿童医院", _BJ_CHILDREN, None) is True        # 没定位判不了，不拦


# ── 模型猜名去掉了原话开头的城市（2026-10-04 A/B：「上海外滩」→ 30 km 外的「外滩夜市」）────────────────────────
_NIGHT_MARKET = POI(id="n9", name="外滩夜市", category="餐饮服务;餐饮相关场所", lat=22.70, lng=114.10,
                    distance_km=30.7, city="东莞市")
_BUND = POI(id="w9", name="外滩", category="风景名胜;风景名胜;风景名胜", lat=31.2397, lng=121.4998, city="上海市")


def test_a_guess_that_drops_the_named_city_does_not_take_a_local_namesake():
    poi = _KeyedPoi(near={"外滩": [_NIGHT_MARKET]}, wide={"外滩": [_BUND]})
    _, results = asyncio.run(_guessing_agent(poi, ["外滩"])._find_destination("上海外滩", dict(META), near=HERE))
    assert results[0].name == "外滩"


def test_a_guess_that_drops_the_local_city_still_prefers_the_local_place():
    happy = POI(id="n8", name="欢乐谷", category="风景名胜;风景名胜;风景名胜", lat=22.54, lng=113.98, city="深圳市")
    beijing = POI(id="w8", name="欢乐谷", category="风景名胜;风景名胜;风景名胜", lat=39.87, lng=116.49, city="北京市")
    poi = _KeyedPoi(near={"欢乐谷": [happy]}, wide={"欢乐谷": [beijing]})
    agent = _guessing_agent(poi, ["欢乐谷"])
    assert agent._keeps_dropped_head("深圳欢乐谷", "欢乐谷", happy) is True
    assert agent._keeps_dropped_head("上海外滩", "外滩", _NIGHT_MARKET) is False
    assert agent._keeps_dropped_head("山姆会员店", "山姆会员商店", _NIGHT_MARKET) is True   # 不是后半截：不受此限
    assert agent._keeps_dropped_head("上海外滩观光隧道", "外滩", _NIGHT_MARKET) is True  # 只管去掉开头（城市限定词在前）
    stir_fry = POI(id="n7", name="橘子洲长沙小炒", category="餐饮服务;中餐厅;湖南菜(湘菜)", lat=22.6, lng=113.9,
                   city="深圳市", address="宝安区新安街道")
    assert agent._keeps_dropped_head("长沙橘子洲", "橘子洲", stir_fry) is False        # 名字里的「长沙」是菜系，不算


# ── 估算与导航用同样多的候选做锚词扫描（2026-10-05：「东莞松山湖」估算只取 1 条，能对上的「松山湖」排第二）──────────
def test_an_estimate_scans_as_many_candidates_as_navigation():
    bus_stop = POI(id="n1", name="深圳机场(公交站)", category="交通设施服务;公交车站;公交车站相关", lat=22.64, lng=113.81,
                   city="深圳市")
    scenic = POI(id="w1", name="松山湖风景区", category="风景名胜;风景名胜;国家级景点", lat=22.90, lng=113.89, city="东莞市")
    lake = POI(id="w2", name="松山湖", category="地名地址信息;热点地名;热点地名", lat=22.94, lng=113.90, city="东莞市")
    poi = _KeyedPoi(near={"东莞松山湖": [bus_stop]}, wide={"东莞松山湖": [scenic, lake]})
    name, point, _ = asyncio.run(_guessing_agent(poi, [])._resolve_point_checked("东莞松山湖", None, dict(META)))
    assert name == "松山湖" and point.lat == lake.lat


# ── 「XX海滩」剥两个字取主干（2026-10-05：只剥「滩」时「小梅沙海」配上了「小梅沙海洋世界」）─────────────────
def test_a_beach_name_is_stemmed_without_the_whole_word_beach():
    beach = POI(id="n1", name="小梅沙沙滩", category="风景名胜;风景名胜;海滩", lat=22.60, lng=114.32, city="深圳市")
    resort = POI(id="n2", name="小梅沙海滨乐园", category="体育休闲服务;度假疗养场所;度假村", lat=22.60, lng=114.32, city="深圳市")
    aquarium = POI(id="c1", name="小梅沙海洋世界", category="风景名胜;公园广场;水族馆", lat=22.60, lng=114.33, city="深圳市")
    poi = _KeyedPoi(near={"小梅沙海滩": [beach, resort]}, city={"小梅沙海滩": [aquarium]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("小梅沙海滩", dict(META), near=HERE))
    assert results[0].name == "小梅沙沙滩"
    assert NavigationAgent._category_anchor("上海外滩")[0] == "滩"           # 不带「海 / 沙」的照旧剥「滩」


def test_a_city_plus_core_name_matches_the_official_full_name():
    """「上海第六人民医院」之于上海的「上海交通大学医学院附属第六人民医院」：原话去掉这个地点所在的城市后，剩下的核心名
    （至少 4 个字）含在全称里就算对上。城市必须是它自己的城市；太短的核心名（「中医院」）不收。"""
    m = NavigationAgent._dest_matches
    assert m("上海第六人民医院", "上海交通大学医学院附属第六人民医院(徐汇院区)", "上海市")
    assert m("上海市第六人民医院", "上海交通大学医学院附属第六人民医院(徐汇院区)", "上海市")
    assert m("上海华山医院", "复旦大学附属华山医院", "上海市")
    assert not m("北京第六人民医院", "上海交通大学医学院附属第六人民医院(徐汇院区)", "上海市")   # 不是它的城市
    assert not m("上海第六人民医院", "上海交通大学医学院附属第六人民医院(徐汇院区)", "")        # 不知道它在哪座城
    assert not m("深圳中医院", "深圳市宝安区中医院", "深圳市")      # 核心名「中医院」太短：区里的中医院也含着它
    # 全称在城市与核心名之间夹着区县的是区里的同名机构（A/B：「深圳人民医院」曾落到 2 km 外的南山区人民医院）
    assert not m("深圳人民医院", "深圳市南山区人民医院", "深圳市")
    assert not m("上海人民医院", "上海市浦东新区人民医院", "上海市")
    assert m("深圳人民医院", "深圳市人民医院", "深圳市")


# ── 第八片：口头叫法 + 名字毫不相干的兜底不当目的地（2026-10-08）──────────────────────────────────────
# 云端直调（深圳南山）：「7-11」「711」「KFC」名字对不上本地门店，全国重搜接到北京一个就叫这个名字的点（1925–1944 km）；走到兜底的
# 13 个中文 / 数字说法里 11 个靠高德相关度落对（简称、口头叫法、同音误识别、门牌地址），「北大医院」→ 眼科门诊角膜塑形镜室、
# 「301医院」→ 医疗美容门诊部；15 个纯英文说法全部落对（高德把英文名翻成本地门店）。

_SEVEN = _shop("7-ELEVEn(海王银河科技大厦店)", "购物服务;便民商店/便利店;便民商店/便利店", 0.1)
_BJ_SEVEN = POI(id="w7", name="7-11便利店(大红罗厂街店)", category="购物服务;便民商店/便利店;7-ELEVEn便利店",
                lat=39.93, lng=116.38, city="北京市")
_EYE_ROOM = _shop("华中科技大学协和深圳医院眼科门诊角膜塑形镜室", "医疗保健服务;医疗保健服务场所;医疗保健服务场所", 2.1)
_PKU_SZ = POI(id="c9", name="北京大学深圳医院", category="医疗保健服务;综合医院;三级甲等医院", lat=22.5577, lng=114.0464,
              city="深圳市")
_PKU_BJ = POI(id="w9", name="北京大学第一医院(西城厂桥院区)", category="医疗保健服务;综合医院;三级甲等医院",
              lat=39.93, lng=116.38, city="北京市")


def test_spoken_brand_names_match_the_amap_store_names():
    m = NavigationAgent._dest_matches
    for said in ("7-11", "711", "七十一", "seven eleven", "Seven-Eleven", "7-Eleven便利店"):
        assert m(said, _SEVEN.name, "深圳市"), said
    assert m("KFC", "肯德基(科苑店)")
    assert m("北大深圳医院", "北京大学深圳医院")
    assert NavigationAgent._grounds_to("中石化加油站", _shop("中国石化荔园北加油站", "汽车服务;加油站;中国石化", 0.7),
                                       "加油站", ("加油站",))
    # 两个字的中文简称只认打头：「河北大学」里的「北大」不是北京大学（表里的说法一个都不沾 ⇒ ""）
    assert NavigationAgent._aliased("北大医院") == "北京大学医院"
    assert NavigationAgent._aliased("河北大学") == ""
    assert NavigationAgent._aliased("7eleven便利店") == "7eleven便利店"      # 高德写法本身也算沾表


def test_a_spoken_brand_does_not_jump_to_a_namesake_in_another_city():
    """「7-11」：本地门店叫「7-ELEVEn」，名字对不上 ⇒ 全国重搜接到北京一个就叫「7-11便利店」的点（改前云端直调：1943.7 km）。"""
    for said in ("7-11", "711"):
        poi = _KeyedPoi(near={said: [_SEVEN]}, wide={said: [_BJ_SEVEN]})
        _, results = asyncio.run(_guessing_agent(poi, [])._find_destination(said, dict(META), near=HERE))
        assert results[0].name == _SEVEN.name and len(poi.calls) == 1, (said, poi.calls)
    kfc = _shop("肯德基(科苑店)", "餐饮服务;快餐厅;肯德基", 0.2)
    poi = _KeyedPoi(near={"KFC": [kfc]}, wide={"KFC": [POI(id="w8", name="KFC", lat=39.9, lng=116.4, city="北京市")]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("KFC", dict(META), near=HERE))
    assert results[0].name == kfc.name


def test_latin_names_outside_the_alias_table_keep_their_case():
    """只有表里的说法不分大小写。A/B（2026-10-08）：一般性地不分大小写时「Subway」配上北京的「赛百味 SUBWAY(东方广场店)」（1942 km）、
    「Lawson」配上 18 km 外的「LAWSON罗森」；名字对不上时兜底取的是最近的门店（高德把英文名翻成本地门店）。"""
    near = _shop("赛百味科兴店", "餐饮服务;快餐厅;快餐厅", 0.8)
    far = POI(id="w6", name="赛百味 SUBWAY(东方广场店)", category="餐饮服务;快餐厅;快餐厅", lat=39.91, lng=116.41, city="北京市")
    poi = _KeyedPoi(near={"Subway": [near]}, wide={"Subway": [far]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("Subway", dict(META), near=HERE))
    assert results[0].name == near.name
    nearest = _shop("罗森(科技园文化广场店)", "购物服务;便民商店/便利店;便民商店/便利店", 0.2)
    poi = _KeyedPoi(near={"Lawson": [nearest]},
                    city={"Lawson": [_shop("LAWSON罗森(东门东门町欢乐城店)", "购物服务;便民商店/便利店;便民商店/便利店", 18.2)]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("Lawson", dict(META), near=HERE))
    assert results[0].name == nearest.name


def test_an_abbreviated_hospital_is_found_in_the_city():
    """「北大医院」：5 km 的就近扫描只有一个眼科门诊的诊室，全国第一是北京大学第一医院，模型猜名也给北京那家（2026-10-08 云端直调）。
    本城按相关度第一就是北京大学深圳医院（11.2 km）：「北大」换成「北京大学」后主干对上、类目对上。"""
    poi = _KeyedPoi(near={"北大医院": [_EYE_ROOM]}, city={"北大医院": [_PKU_SZ]},
                    wide={"北大医院": [_PKU_BJ], "北京大学第一医院": [_PKU_BJ]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京大学第一医院"])._find_destination("北大医院", dict(META), near=HERE))
    assert results[0].name == "北京大学深圳医院"


def test_the_fallback_refuses_a_nearby_result_unrelated_to_the_words():
    """「301医院」：近处、本城、全国、猜名都对不上，兜底的近处结果是一家医疗美容门诊部（2026-10-08 云端直调）⇒ 没找到，追问。"""
    beauty = _shop("深圳柏伊美妍医疗美容门诊部", "医疗保健服务;专科医院;整形美容", 2.4)
    city = [_shop("中山大学附属第八医院", "医疗保健服务;综合医院;三级甲等医院", 14.2)]
    poi = _KeyedPoi(near={"301医院": [beauty]}, city={"301医院": city})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("301医院", dict(META), near=HERE))
    assert results == []
    res = asyncio.run(run_handle(_guessing_agent(poi, []), "navigation.navigate_to", slots={"destination": "301医院"},
                                 raw_text="导航去301医院", meta=dict(META)))
    assert res.status == "need_slot" and not res.actions and res.missing_slots == ["destination"]
    assert "301医院" in res.speech and "美妍" not in res.speech


def test_an_unrelated_nearby_result_still_gives_way_to_a_far_namesake():
    """调用方对沾边的近处结果会再看外地有没有以原话打头的本体（`_far_namesake`）；毫不相干的近处结果被兜底拒掉时同样要看。"""
    mall = _shop("南山海岸城购物中心", "购物服务;商场;购物中心", 2.7)
    far = POI(id="w2", name="厦门火车站(地铁站)", category="交通设施服务;地铁站;地铁站", lat=24.4681, lng=118.1162,
              city="厦门市")
    wide = [POI(id="w1", name="厦门站", category="交通设施服务;火车站;火车站", lat=24.4686, lng=118.1166, city="厦门市"), far]
    poi = _KeyedPoi(near={"厦门火车站": [mall]}, wide={"厦门火车站": wide})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("厦门火车站", dict(META), near=HERE))
    assert [r.name for r in results] == ["厦门火车站(地铁站)"]


@pytest.mark.parametrize("said, poi", [
    ("欢乐古", _shop("深圳欢乐谷", "体育休闲服务;休闲场所;游乐场", 4.0)),                          # 同音误识别：打头两个字
    ("南科大", _shop("南方科技大学", "科教文化服务;学校;高等院校", 9.0)),                           # 简称是全称的子序列
    ("中石化加油站", _shop("中国石化荔园北加油站", "汽车服务;加油站;中国石化", 0.7)),
    ("华为体验店", _shop("华为授权体验店(南山大冲)", "购物服务;家电电子卖场;手机销售", 1.1)),
    ("七十一便利店", _shop("7-ELEVEn(缤纷假日豪园店)", "购物服务;便民商店/便利店;7-ELEVEn便利店", 2.7)),   # 口头叫法
    ("苹果店", _shop("Apple授权经销商(顺电深圳大冲万象城店)", "购物服务;购物相关场所;购物相关场所", 1.5)),
    ("Pizza Hut", _shop("必胜客(科苑店)", "餐饮服务;快餐厅;必胜客", 0.2)),                           # 纯拉丁字母：高德翻的，照旧采信
    ("Tesla超充", _shop("特斯拉超级充电站(深圳软件园)", "汽车服务;充电站;专用充电站", 0.5)),          # 去掉类目锚词后是纯拉丁字母
    ("上海长宁阳光小区", _shop("阳光小区", "商务住宅;住宅区;住宅小区", 1.0)),                       # 地点名含在原话里
])
def test_the_fallback_still_takes_nearby_results_that_relate_to_the_words(said, poi):
    assert NavigationAgent._relates(said, poi)


@pytest.mark.parametrize("said, name", [
    ("北大医院", "华中科技大学协和深圳医院眼科门诊角膜塑形镜室"),
    ("301医院", "深圳柏伊美妍医疗美容门诊部"),
    ("第二家", "美宜佳(华富洋大厦店)"),
    ("南科大", "南山区科技园大厦"),              # 南…科…大隔得太开
    ("深圳北大医院", "深圳市眼科医院"),           # 城市前缀不算沾边
])
def test_unrelated_nearby_results_are_named_as_such(said, name):
    assert not NavigationAgent._relates(said, _shop(name, "", 1.0))


def test_a_house_number_relates_through_the_address():
    """「科苑路15号」→ 高德相关度第一是科兴科学园，名字不沾边，地址是「科苑北路15号」（2026-10-08 云端直调）。"""
    park = POI(id="k1", name="南山科兴科学园", address="粤海街道科苑西社区科苑北路15号", category="商务住宅;产业园区;产业园区",
               lat=22.548, lng=113.944, city="深圳市")
    assert NavigationAgent._relates("科苑路15号", park)
    assert not NavigationAgent._relates("科苑路16号", park)


# ── 门牌地址按当前城市地理编码（2026-10-08）────────────────────────────────────────────────────
# 云端直调：「南海大道1088号」关键字检索全国重搜接到海口的「南海大道」（478 km），「深南大道9028号」被模型猜成深圳湾体育中心；
# 高德地理编码对深圳 12 个门牌地址全部给出门址级坐标（0.1–1 km）。


class _AddressPoi(_KeyedPoi):
    """带逆地理编码与门牌地理编码的桩：当前城市深圳；`houses` 按地址给地理编码结果。"""

    def __init__(self, houses=None, city="深圳市", **kw):
        super().__init__(**kw)
        self.houses, self.here_city, self.geocoded = dict(houses or {}), city, []

    async def reverse_geocode(self, lng, lat, meta=None):
        return SimpleNamespace(city=self.here_city, adcode="440305")

    async def geocode_address(self, address, city, meta=None):
        self.geocoded.append((address, city))
        return self.houses.get(address)


def test_a_house_number_is_geocoded_in_the_current_city():
    haikou = POI(id="h1", name="南海大道", lat=20.02, lng=110.33, city="海口市", category="地名地址信息;交通地名;道路名")
    poi = _AddressPoi(houses={"南海大道1088号": {"level": "门址", "location": "113.9258,22.5191",
                                                 "formatted_address": "广东省深圳市南山区南海大道1088号", "city": "深圳市"}},
                      wide={"南海大道1088号": [haikou]})
    _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("南海大道1088号", dict(META), near=HERE))
    assert results[0].name == "南海大道1088号" and abs(results[0].lat - 22.5191) < 1e-6
    assert results[0].address == "广东省深圳市南山区南海大道1088号"
    assert poi.geocoded == [("南海大道1088号", "深圳市")] and not poi.calls       # 不再走关键字检索


def test_a_house_number_falls_back_to_search_unless_it_is_a_house_in_this_city():
    """没有结果、只到道路级、不在当前城市 ⇒ 照旧检索（这里近处那个点的地址沾边，兜底采信）。"""
    park = POI(id="k1", name="南山科兴科学园", address="粤海街道科苑西社区科苑北路15号", category="商务住宅;产业园区;产业园区",
               lat=22.548, lng=113.944, distance_km=0.8, city="深圳市")
    for hit in (None,
                {"level": "道路", "location": "113.94,22.54", "formatted_address": "广东省深圳市南山区科苑路", "city": "深圳市"},
                {"level": "门址", "location": "121.47,31.23", "formatted_address": "上海市科苑路15号", "city": "上海市"}):
        poi = _AddressPoi(houses={"科苑路15号": hit}, near={"科苑路15号": [park]})
        _, results = asyncio.run(_guessing_agent(poi, [])._find_destination("科苑路15号", dict(META), near=HERE))
        assert results and results[0].name == park.name, hit


def test_names_that_only_look_like_numbers_are_not_geocoded():
    for name in ("深圳湾1号", "地铁1号线", "3号门", "科苑路15号科兴科学园"):
        poi = _AddressPoi(houses={}, near={name: [_shop(name, "", 0.5)]})
        asyncio.run(_guessing_agent(poi, [])._find_destination(name, dict(META), near=HERE))
        assert not poi.geocoded, name

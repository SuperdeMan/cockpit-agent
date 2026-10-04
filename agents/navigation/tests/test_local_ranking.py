"""本地具名目的地不被附属地点顶替（docs/design/2026-10-04-local-destination-ranking.md）。

2026-10-04 云上导航容器测量（深圳南山出发）：具名目的地的近处搜索是高德周边搜索缺省参数（5 km、按距离排），
5 km 内借了名的店天然排第一——大梅沙→「大梅沙顶级推拿」、蛇口港→「深圳蛇口港公安局」、东门→「豪方现代东门食堂」；
「会展中心」名字对不上，全国重搜与模型兜底把人带到北京。
"""
from __future__ import annotations

import asyncio

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


def test_category_names_skip_the_city_search():
    """本城重搜按相关度排，会把连锁店带到远处的大店（A/B：华润万家超市 0.8 km → 9.1 km）；类目查询不走它。"""
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, city={"儿童医院": [_SZ_CHILDREN]}, wide={"儿童医院": [_BJ_CHILDREN]})
    asyncio.run(_guessing_agent(poi, [])._find_destination("儿童医院", dict(META), near=HERE))
    assert not [call for call in poi.calls if call[2]]


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
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, wide={"儿童医院": [_BJ_CHILDREN], "北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京儿童医院"])._find_destination("儿童医院", dict(META), near=HERE))
    assert [r.name for r in results] == ["知贝医疗深圳门店"]        # 本地实际结果（话术会报出实际名，用户可纠正）
    assert ("北京儿童医院", False, "") in poi.calls                 # 猜名确实解析过
    poi = _KeyedPoi(near={"儿童医院": [_CLINIC]}, wide={"北京儿童医院": [_BJ_CHILDREN]})
    _, results = asyncio.run(_guessing_agent(poi, ["北京儿童医院"])._find_destination("儿童医院", dict(META), near=HERE))
    assert [r.name for r in results] == ["知贝医疗深圳门店"]        # 只有猜名到了北京
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


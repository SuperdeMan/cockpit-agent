"""navigation 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle。"""
import asyncio
import json
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI, GeoPoint


def test_nearby_search_uses_session_location_coordinates():
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, near=None, **kwargs):
        seen["near"] = near
        return [POI(id="poi-1", name="附近咖啡", lat=39.93, lng=116.42)]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "咖啡"}, raw_text="附近咖啡",
        ctx=make_context(), meta={"current_lat": "39.92", "current_lng": "116.41"}))

    assert res.status == "ok"
    assert seen["near"].lat == 39.92 and seen["near"].lng == 116.41

class _ScriptedPoiProvider:
    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = [] if default is None else default
        self.queries = []

    async def search(self, keyword, **kwargs):
        self.queries.append(keyword)
        return self.responses.get(keyword, self.default)


def _poi(name: str) -> POI:
    return POI(id="landmark-1", name=name, address="深圳市南山区", lat=22.50, lng=113.94)


def _async_return(value):
    async def fake_complete(*args, **kwargs):
        return value
    return fake_complete


def test_search_poi_returns_card():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.search_poi",
        slots={"keyword": "充电站"}, raw_text="附近的充电站"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "poi_list"
    assert len(res.ui_card["items"]) >= 1


def test_search_poi_missing_keyword_asks():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.search_poi", slots={}, raw_text="找个地方"))
    assert res.status == "need_slot"


def test_navigate_to_emits_action():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "首都机场"}, raw_text="导航去首都机场"))
    assert res.status == "ok"
    assert any(a["type"] == "navigate" for a in res.actions)


def test_navigate_to_attaches_granted_current_location_as_origin():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "\u9996\u90fd\u673a\u573a"}, raw_text="\u5bfc\u822a\u53bb\u9996\u90fd\u673a\u573a",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))

    payload = res.actions[0]["payload"]
    assert payload["origin_lat"] == 39.92
    assert payload["origin_lng"] == 116.41


def test_navigate_to_missing_dest_asks():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to", slots={}, raw_text="导航"))
    assert res.status == "need_slot"


def test_navigate_to_prefers_landmark_over_fuzzy_match():
    """视觉地标：高德对描述返回的勉强模糊匹配不得抢占 LLM 解析的正式地标（R1）。

    旧实现先用原描述直搜，命中任意结果即返回——真实高德对“像笋的建筑”会返回 V东滨店
    这类垃圾模糊匹配，导致导航到错误 POI。修复后地标描述优先经 LLM 解析正式名称再验证。
    """
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "深圳外形像笋一样的建筑": [_poi("V东滨店")],   # 高德垃圾模糊匹配
        "华润春笋大厦": [_poi("华润春笋大厦")],
    })
    agent.llm.complete = _async_return('["华润春笋大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳外形像笋一样的建筑"},
        raw_text="导航去深圳外形像笋一样的建筑"))

    assert res.actions[0]["payload"]["destination"] == "华润春笋大厦"
    assert "华润春笋大厦" in agent.poi.queries
    assert res.actions[0]["payload"]["destination"] != "V东滨店"


def test_navigate_to_rejects_landmark_candidate_with_unrelated_poi():
    """高德对非官方名返回的邻近无关 POI（名字对不上）必须被拒，换下一候选（官方名）。

    实测坑：搜俗称『华润春笋大厦』→ 高德返回同位置的『V东滨店』；只有官方名『中国华润大厦』
    才命中楼本身。校验 top 结果名与候选实质匹配后，才不会把 V东滨店当成目的地。
    """
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "华润春笋大厦": [_poi("V东滨店")],        # 名字对不上 → 拒
        "中国华润大厦": [_poi("中国华润大厦")],    # 名字匹配 → 取
    })
    agent.llm.complete = _async_return('["华润春笋大厦","中国华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳外形像笋一样的建筑"},
        raw_text="导航去深圳外形像笋一样的建筑"))

    assert res.actions[0]["payload"]["destination"] == "中国华润大厦"


def test_navigate_to_stop_category_offers_waypoint_choice():
    """导航去X + stop_category 吃饭 → 导航到X + 给餐厅候选(waypoint_choice 卡)让用户二次选择。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [_poi("东方之门")],
        "餐厅": [_poi("餐厅A"), _poi("餐厅B"), _poi("餐厅C")],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "stop_category": "吃饭"},
        raw_text="导航去东方之门，附近找个吃饭的地方"))

    # 导航优先：仍发到目的地的 navigate（不选也能走）
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "东方之门"
    # 出 waypoint_choice 候选卡，带目的地与候选
    assert res.ui_card["type"] == "poi_list" and res.ui_card["purpose"] == "waypoint_choice"
    assert res.ui_card["destination"] == "东方之门"
    assert [i["name"] for i in res.ui_card["items"]] == ["餐厅A", "餐厅B", "餐厅C"]
    assert "顺道去哪家" in res.speech


def test_navigate_to_waypoint_adds_to_navigate_payload():
    """导航去X途经Y（已选）→ navigate.payload.waypoints 带 Y（near X 解析真实坐标）。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [_poi("东方之门")],
        "餐厅B": [POI(id="b", name="餐厅B", address="苏州工业园区", lat=31.32, lng=120.68)],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "waypoint": "餐厅B"},
        raw_text="导航去东方之门途经餐厅B"))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "东方之门"
    assert nav["payload"]["waypoints"][0]["name"] == "餐厅B"
    assert nav["payload"]["waypoints"][0]["lat"] == 31.32
    assert "途经点" in res.speech


def test_navigate_to_waypoint_emits_route_plan_card():
    """目的地+途经点都定后 → 出 route_plan 路线规划卡（出发地→途经点→目的地），不再是 poi_list。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [_poi("东方之门")],
        "餐厅B": [POI(id="b", name="餐厅B", address="苏州工业园区", lat=31.32, lng=120.68)],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "waypoint": "餐厅B"},
        raw_text="导航去东方之门途经餐厅B"))

    assert res.ui_card["type"] == "route_plan"
    assert res.ui_card["destination"] == "东方之门"
    assert res.ui_card["waypoints"][0]["name"] == "餐厅B"
    assert "当前位置" in res.speech and "东方之门" in res.speech


def test_navigate_to_detects_dining_stop_from_raw_text():
    """planner 未填 stop_category（甚至误拆出 food 步）时，导航侧仍从 raw_text『那附近找个餐厅』
    识别 → 出真实餐厅的 waypoint_choice 候选（修『途经餐厅是假数据』）。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "深圳像笋一样的建筑": [_poi("V东滨店")],   # 视觉地标垃圾匹配（应被地标解析绕过）
        "中国华润大厦": [_poi("中国华润大厦")],
        "餐厅": [_poi("真·餐厅A"), _poi("真·餐厅B")],
    })
    agent.llm.complete = _async_return('["中国华润大厦"]')
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳像笋一样的建筑"},
        raw_text="导航去深圳像笋一样的建筑，再帮我在那附近找个餐厅"))

    assert res.ui_card["type"] == "poi_list" and res.ui_card["purpose"] == "waypoint_choice"
    assert res.ui_card["destination"] == "中国华润大厦"
    assert [i["name"] for i in res.ui_card["items"]] == ["真·餐厅A", "真·餐厅B"]
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "中国华润大厦"


def test_navigate_to_waypoint_parsed_from_raw_text_when_slot_absent():
    """planner 未填 waypoint 槽位时，从 raw_text『途经X』兜底解析。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [_poi("东方之门")],
        "肯德基": [POI(id="k", name="肯德基(东方之门店)", address="x", lat=31.3, lng=120.6)],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={},
        raw_text="导航去东方之门途经肯德基"))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["destination"] == "东方之门"
    assert nav["payload"]["waypoints"][0]["name"] == "肯德基(东方之门店)"


def test_search_poi_category_not_hijacked_by_multi_intent_raw_text():
    """多意图原句里的地标不得劫持“找充电桩”子步：不解析地标、不自动导航（R2）。

    云端每个 step 收到的 raw_text 是完整用户原句，旧实现据此把找充电桩改写成导航到地标
    （双 navigate + 卡片串味）。修复后类目关键词搜索如实搜附近、不被整句劫持。
    """
    agent = NavigationAgent()
    called_llm = {"n": 0}

    async def fake_complete(*args, **kwargs):
        called_llm["n"] += 1
        return '["华润春笋大厦"]'

    agent.poi = _ScriptedPoiProvider({
        "充电桩": [_poi("特来电充电站")],
        "华润春笋大厦": [_poi("华润春笋大厦")],
    })
    agent.llm.complete = fake_complete

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "充电桩"},
        raw_text="导航去深圳外形像笋一样的建筑，然后在附近帮我找个充电桩"))

    assert res.status == "ok"
    assert res.ui_card["type"] == "poi_list"
    assert res.ui_card["keyword"] == "充电桩"               # 关键词没被改写成地标
    assert [i["name"] for i in res.ui_card["items"]] == ["特来电充电站"]
    assert res.actions == []                                 # 不自动导航
    assert called_llm["n"] == 0                              # 不触发地标解析
    assert agent.poi.queries == ["充电桩"]                   # 只搜了充电桩


def test_navigate_to_reasks_when_no_landmark_candidate_is_validated():
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[])
    agent.llm.complete = _async_return('["不存在的地标"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "某个像飞船的建筑"}, raw_text="导航到某个像飞船的建筑"))

    assert res.status == "need_slot"
    assert res.actions == []


def test_search_poi_resolves_visual_landmark_from_raw_text_and_navigates():
    """Planner 可能错误抽出普通关键词，导航 Agent 仍应使用原话解析地标。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "笋岗": [],
        "华润大厦": [_poi("华润大厦")],
    })
    agent.llm.complete = _async_return('["华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "笋岗"},
        raw_text="去深圳笋一样的建筑物"))

    assert agent.poi.queries == ["笋岗", "华润大厦"]
    assert res.actions[0]["type"] == "navigate"
    assert res.actions[0]["payload"]["destination"] == "华润大厦"


def test_search_poi_prefers_validated_landmark_over_misparsed_keyword_result():
    """视觉地标描述不能被 Planner 抽出的同名普通 POI 抢占。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "笋岗": [_poi("笋岗地铁站")],
        "中国华润大厦": [_poi("中国华润大厦")],
    })
    agent.llm.complete = _async_return('["中国华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "笋岗"},
        raw_text="去深圳笋一样的建筑物"))

    assert agent.poi.queries == ["笋岗", "中国华润大厦"]
    assert res.actions[0]["payload"]["destination"] == "中国华润大厦"


def test_visual_landmark_detection_does_not_promote_ordinary_navigation():
    assert NavigationAgent._is_visual_landmark_description("导航到上海船型的建筑物")
    assert not NavigationAgent._is_visual_landmark_description("去深圳万象城")


def test_landmark_resolution_passes_original_utterance_to_model():
    """视觉比喻的细节不能被拼接提示词改写后丢失。"""
    agent = NavigationAgent()
    seen = {}

    async def fake_complete(messages, **kwargs):
        seen["messages"] = messages
        return '["中国华润大厦"]'

    agent.llm.complete = fake_complete
    raw = "去深圳笋一样的建筑物"

    candidates = asyncio.run(agent._landmark_candidates(raw))

    assert candidates == ["中国华润大厦"]
    assert seen["messages"][-1] == {"role": "user", "content": raw}


# ── 常用地点（家/公司）──────────────────────────────────────

def test_navigate_to_home_uses_stored_place_without_searching():
    """命中『家』别名且已设置 → 用画像坐标直达，不再搜 POI。"""
    agent = NavigationAgent()
    searched = {"hit": False}

    async def search(*a, **k):
        searched["hit"] = True
        return []

    agent.poi.search = search
    ctx = make_context(context_values={"profile.places": json.dumps({
        "home": {"name": "阳光小区", "address": "上海长宁区某路1号",
                 "lat": 31.21, "lng": 121.40}})})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "家"}, raw_text="导航回家", ctx=ctx))

    assert res.status == "ok"
    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert nav["payload"]["lat"] == 31.21 and nav["payload"]["lng"] == 121.40
    assert searched["hit"] is False
    assert "家" in res.speech


def test_navigate_home_with_stop_category_keeps_coffee_intent():
    """『导航回家，途中找个咖啡店』：到家途中仍给咖啡顺路停靠候选，不丢这层意图。"""
    agent = NavigationAgent()

    async def search(keyword, near=None, **kwargs):
        return [POI(id="c1", name="星巴克", address="x", lat=22.5, lng=113.9),
                POI(id="c2", name="瑞幸咖啡", address="y", lat=22.51, lng=113.91)]

    agent.poi.search = search
    ctx = make_context(context_values={"profile.places": json.dumps({
        "home": {"name": "家小区", "address": "宝安", "lat": 22.57, "lng": 113.85}})})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "家"}, raw_text="导航回家，途中找个咖啡店", ctx=ctx,
        meta={"current_lat": "22.53", "current_lng": "113.94"}))

    assert res.status == "ok"
    assert res.ui_card and res.ui_card.get("purpose") == "waypoint_choice"  # 顺路停靠候选卡
    assert res.ui_card.get("destination") == "家小区"                       # 目的地仍是家
    assert any("咖啡" in it.get("name", "") or "星巴克" in it.get("name", "")
               for it in res.ui_card.get("items", []))
    assert any(a["type"] == "navigate" for a in res.actions)                # 仍导航到家


def test_navigate_to_company_unset_asks_to_set_address():
    """命中『公司』别名但未设置 → NEED_SLOT 二次交互要地址（独立槽 place_address）。"""
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "公司"}, raw_text="导航去公司",
        ctx=make_context(context_values={})))

    assert res.status == "need_slot"
    assert res.missing_slots == ["place_address"]
    assert "公司" in res.speech


def test_navigate_to_resume_sets_company_then_navigates():
    """二次交互续接：destination=公司 + place_address=地址 → 存为公司并导航。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("腾讯滨海大厦")])
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "公司", "place_address": "深圳南山腾讯滨海大厦"},
        raw_text="深圳南山腾讯滨海大厦", ctx=ctx))

    assert res.status == "ok"
    assert any(a["type"] == "navigate" for a in res.actions)
    ctx._memory.upsert_profile.assert_awaited()
    saved = json.loads(ctx._memory.upsert_profile.await_args.args[2])
    assert "company" in saved and saved["company"]["name"] == "腾讯滨海大厦"
    assert "公司" in res.speech


def test_set_place_stores_without_navigating():
    """显式设置：navigation.set_place 只记录、不产出导航动作。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("阳光小区")])
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        agent, "navigation.set_place",
        slots={"place": "家", "address": "上海长宁阳光小区"},
        raw_text="把家设成上海长宁阳光小区", ctx=ctx))

    assert res.status == "ok"
    assert not any(a["type"] == "navigate" for a in res.actions)
    ctx._memory.upsert_profile.assert_awaited()
    assert "家" in res.speech


def test_set_place_parses_alias_and_address_from_raw_text():
    """槽位缺失时从原话『我家在XX』兜底解析别名+地址。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("科技园")])
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        agent, "navigation.set_place",
        slots={}, raw_text="我家在深圳南山科技园", ctx=ctx))

    assert res.status == "ok"
    saved = json.loads(ctx._memory.upsert_profile.await_args.args[2])
    assert "home" in saved


def test_locate_with_gps_reverse_geocodes_current_position():
    """『我现在在哪里』+ GPS → 逆地理编码当前坐标，给出当前所在地址。"""
    agent = NavigationAgent()

    async def rg(lng, lat, **kwargs):
        return GeoPoint(address="广东省深圳市南山区科技园", lat=lat, lng=lng)

    agent.poi.reverse_geocode = rg
    res = asyncio.run(run_handle(
        agent, "navigation.locate", slots={}, raw_text="我现在在哪里",
        meta={"current_lat": "22.54", "current_lng": "113.95"}))

    assert res.status == "ok"
    assert "科技园" in res.speech and "当前" in res.speech


def test_locate_without_gps_is_honest_not_shanghai_mock():
    """无 GPS 时 locate 诚实提示开启定位，绝不回退编造车机 mock（上海）——与天气一致。"""
    ctx = make_context(context_values={"vehicle.location": '{"city": "上海"}'})
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.locate", slots={}, raw_text="我在哪", ctx=ctx))

    assert res.status == "ok"
    assert "上海" not in res.speech     # 不再编造车机 mock 位置
    assert "定位" in res.speech         # 诚实提示开启定位授权


def test_current_position_is_gps_only_no_vehicle_location_fallback():
    """统一定位源：有 GPS 用 GPS；无 GPS 返回 None（不回退 vehicle.location mock）。"""
    agent = NavigationAgent()
    ctx = make_context(context_values={"vehicle.location": '{"city": "上海"}'})
    with_gps = asyncio.run(agent._current_position(
        ctx, {"current_lat": "22.54", "current_lng": "113.95"}))
    assert with_gps is not None and abs(with_gps.lat - 22.54) < 1e-6
    without_gps = asyncio.run(agent._current_position(ctx, {}))
    assert without_gps is None


def test_navigate_to_passes_current_location_as_near():
    """『最近的/附近的粤菜馆』应带当前位置 near，按距离就近解析（issue: 没用当前位置）。"""
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, near=None, **kwargs):
        seen['near'] = near
        return [POI(id='p1', name='粤小馆', address='科技园', lat=22.5, lng=113.9)]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, 'navigation.navigate_to',
        slots={'destination': '最近的粤菜馆'}, raw_text='导航去最近的粤菜馆',
        meta={'current_lat': '22.54', 'current_lng': '113.95'}))

    assert res.status == 'ok'
    assert seen['near'] is not None, 'navigate_to 应把当前位置作为 near 传给 POI 搜索'
    assert abs(seen['near'].lat - 22.54) < 1e-6 and abs(seen['near'].lng - 113.95) < 1e-6


def test_nearest_without_location_asks_to_enable_not_arbitrary_city():
    """『最近的粤菜馆』无定位 → 诚实提示开启定位，不拿任意城市冒充"最近"、不导航。"""
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "最近的粤菜馆"}, raw_text="导航去最近的粤菜馆",
        ctx=make_context(context_values={})))  # 无 GPS

    assert res.status == "ok"
    assert not any(a["type"] == "navigate" for a in res.actions)
    assert "定位" in res.speech


def test_nearest_with_location_strips_proximity_and_searches_keyword_near():
    """有定位时『附近的粤菜馆』剥掉就近词、按当前位置周边搜类目（而非整句当 POI 名搜空）。"""
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, near=None, **kwargs):
        seen["keyword"] = keyword
        seen["near"] = near
        return [POI(id="p1", name="粤小馆", address="南山", lat=22.54, lng=113.94)]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "附近的粤菜馆"}, raw_text="导航去附近的粤菜馆",
        meta={"current_lat": "22.5447", "current_lng": "113.9447"}))

    assert res.status == "ok"
    assert seen["keyword"] == "粤菜馆", f"应剥掉就近前缀，实际搜的是 {seen['keyword']!r}"
    assert seen["near"] is not None and abs(seen["near"].lat - 22.5447) < 1e-6


def test_nearest_returns_destination_choices_not_waypoints():
    """『附近的粤菜馆』给目的地候选(plain poi_list)，不自动导航、不当顺路途经点。
    回归：之前会自动选一家作目的地、把其余当『途经点』，用户『第N个』落成 waypoint。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("粤菜A"), _poi("粤菜B"), _poi("粤菜C")])
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "附近的粤菜馆"}, raw_text="导航去附近的粤菜馆",
        meta={"current_lat": "22.54", "current_lng": "113.95"}))

    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "poi_list"
    assert res.ui_card.get("purpose") is None         # 不是 waypoint_choice/dest_choice
    assert not res.actions                              # 不自动导航
    assert "第几个" in res.speech or "哪一家" in res.speech


def test_nearest_huanyipi_passes_next_page():
    """『换一批』：续问带 meta.poi_page → 翻页取下一批不同候选（不再返回原结果）。"""
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, near=None, page=1, **kwargs):
        seen["page"] = page
        return [POI(id=f"p{page}", name=f"粤菜店{page}", address="x", lat=22.5, lng=113.9)]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "附近的粤菜馆"}, raw_text="导航去附近的粤菜馆",
        meta={"current_lat": "22.54", "current_lng": "113.95", "poi_page": "2"}))

    assert res.status == "ok"
    assert seen["page"] == 2


def test_specific_destination_without_location_still_navigates():
    """非就近的具体目的地无定位仍正常导航（不被就近门槛误伤）。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("北京南站")])
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "北京南站"}, raw_text="导航去北京南站",
        ctx=make_context(context_values={})))

    assert res.status == "ok"
    assert any(a["type"] == "navigate" for a in res.actions)


def test_non_alias_destination_unaffected_by_places():
    """非别名目的地零回归：不读画像、走常规 POI 解析。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("首都机场")])
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "首都机场"}, raw_text="导航去首都机场", ctx=ctx))

    assert res.status == "ok"
    assert any(a["type"] == "navigate" for a in res.actions)
    ctx._memory.upsert_profile.assert_not_awaited()

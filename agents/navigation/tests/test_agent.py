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


def test_search_poi_treats_highest_as_sort_not_numeric_rating():
    """LLM may express a superlative in rating_min; it must not crash float()."""
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen.update(kwargs)
        return [
            POI(id="low", name="普通川菜", rating=4.1, lat=22.51, lng=113.91),
            POI(id="high", name="高分川菜", rating=4.8, lat=22.52, lng=113.92),
        ]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent,
        "navigation.search_poi",
        slots={"keyword": "川菜馆", "category": "餐饮", "rating_min": "最高"},
        raw_text="找一家附近评分最高的川菜馆，直接导航过去",
    ))

    assert res.status == "ok"
    assert seen["rating_min"] == 0
    assert [item["id"] for item in res.data["items"]] == ["high", "low"]


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


def test_navigate_to_corrects_planner_landmark_misguess():
    """Planner 有时把视觉地标（"像笋的建筑"）错猜成具体楼名（京基100）写进 dest 槽位、绕过地标解析。
    原话是地标描述、dest 却不含造型词时，用原话重解析 + 高德校验，用官方名覆盖臆断（真机漏例）。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "深圳 京基100": [_poi("京基100大厦")],       # planner 臆断的错误楼（不修正就导航到这）
        "中国华润大厦": [_poi("中国华润大厦")],       # 原话地标校验命中的官方名
    })
    agent.llm.complete = _async_return('["中国华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳 京基100"},        # planner 已臆断成具体楼名
        raw_text="我想去深圳那个像笋一样的地方"))       # 原话是视觉地标描述

    assert res.actions[0]["payload"]["destination"] == "中国华润大厦"
    assert res.actions[0]["payload"]["destination"] != "京基100大厦"


def test_navigate_to_no_landmark_correction_for_plain_dest():
    """普通目的地（原话无造型词）不触发修正、不调 LLM。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({"北京南站": [_poi("北京南站")]})
    calls = {"n": 0}

    async def counting(*a, **k):
        calls["n"] += 1
        return "[]"

    agent.llm.complete = counting
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "北京南站"}, raw_text="导航去北京南站"))

    assert res.actions[0]["payload"]["destination"] == "北京南站"
    assert calls["n"] == 0


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


# ── R1（旅程 B3-2/A2-4/B1-2）：就近弱匹配不得顶掉知名地标 / 裸城市名走行政中心 ──

def test_dest_matches_strictness():
    """包含式强校验：共享城市名两字（广州/海滨）不算匹配——landmark.name_matches
    的 2 字公共子串规则对用户直报目的地太松，是 R1 五例同族的判定缺口。"""
    m = NavigationAgent._dest_matches
    assert m("广州塔", "广州塔(广州地标)") is True
    assert m("宝安国际机场", "深圳宝安国际机场") is True
    assert m("东部华侨城", "东部华侨城大门") is True
    assert m("广州塔", "广州仄仄科技有限公司") is False
    assert m("大梅沙海滨公园", "红树林海滨生态公园") is False
    assert m("宝安国际机场", "北环大道入口") is False


def test_navigate_mismatch_retries_without_near_bias():
    """带 near 偏置搜出就近弱匹配（广州仄仄科技）→ 去偏置全国重搜取真地标（R1）。"""
    agent = NavigationAgent()
    calls = []

    class _BiasedPoi:
        async def search(self, keyword, near=None, limit=3, page=1, meta=None, **kw):
            calls.append((keyword, near is not None))
            if near is not None:
                return [POI(id="x", name="广州仄仄科技有限公司",
                            address="深南大道10128号", lat=22.54, lng=113.95)]
            return [POI(id="gzta", name="广州塔", address="广州市海珠区",
                        lat=23.106, lng=113.324)]

    agent.poi = _BiasedPoi()
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "广州塔"},
        raw_text="导航去广州塔",
        meta={"current_lat": "22.53", "current_lng": "113.95"}))

    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and nav[0]["payload"]["destination"] == "广州塔"
    assert "仄仄" not in res.speech
    assert (("广州塔", True) in calls and ("广州塔", False) in calls), calls


def test_navigate_bare_city_goes_admin_center():
    """「导航去惠州」：geocode level=市 → 导航行政中心，不吃就近「惠州出口」（R1）。"""
    agent = NavigationAgent()

    class _CityPoi:
        async def search(self, keyword, near=None, **kw):
            return [POI(id="exit", name="惠州出口", address="深圳市盐田区",
                        lat=22.55, lng=114.30)]

        async def geocode_level(self, address, meta=None):
            assert address == "惠州"
            return "市", "114.416,23.111"

    agent.poi = _CityPoi()
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "惠州"},
        raw_text="导航去惠州",
        meta={"current_lat": "22.53", "current_lng": "113.95"}))

    assert res.status == "ok"
    assert "惠州出口" not in res.speech
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 23.111) < 1e-6


def test_range_advisory_low_battery_long_trip():
    """车辆接地 advisory（旅程 B3-2）：续航盖不住本程（含 15% 余量）→ 话术主动提示补能；
    充足/缺数据不打扰（fail-open）。"""
    adv = NavigationAgent._range_advisory
    assert "补能" in adv(114.2, {"vehicle_battery": "15"})     # 15%→75km 盖不住 114km
    assert adv(47.7, {"vehicle_battery": "80"}) == ""          # 充足
    assert adv(114.2, {}) == ""                                # 无电量数据
    assert adv(0, {"vehicle_battery": "15"}) == ""             # 无里程
    assert adv(114.2, {"vehicle_battery": "abc"}) == ""        # 脏数据


def test_navigate_writes_remindable_eta():
    """R7（旅程 A2-4）：导航成功按 ETA 写 REMINDABLE_ACTIVE——「到之前一刻钟提醒我打电话」
    由 reminder 消费（事件时刻-提前量），不再反问时间。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[_poi("深圳宝安国际机场")])

    async def get_route(o, d, meta=None, **kw):
        return {"distance_km": 26.1, "duration_min": 41.0}

    agent.poi.get_route = get_route
    # make_context 的 shared_state 是 AsyncMock 不真存——钉进内存 dict 才能断言写入
    kv = {}
    ctx = make_context()

    async def _save(key, value):
        kv[key] = value
        return True

    ctx.save_shared_state = _save
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "宝安国际机场"},
        raw_text="导航去宝安国际机场", ctx=ctx,
        meta={"current_lat": "22.53", "current_lng": "113.95"}))
    assert res.status == "ok"

    d = kv.get("remindable_active") or {}
    assert d.get("source") == "navigation"
    items = d.get("items") or []
    assert items and "宝安国际机场" in items[0]["title"]
    assert items[0]["fire_at"] > d.get("ts", 0)          # ETA 在未来


# ── M0a-1 数据真实性：运行期真实源失败 → 诚实降级，绝不回退 mock 假数据 ──────────
# 契约（架构 §9.5 铁律③ + R9）：诚实降级话术用 OK 返回（FAILED 话术会被聚合器吞成
# 裸「抱歉，处理失败」）；不出假卡；mock 兜底 provider 不得在运行期被咨询。

def _boom(*a, **kw):
    from agents._sdk.http import ProviderError
    raise ProviderError("amap 5xx")


def test_no_runtime_mock_fallback_field():
    """mock 兜底 provider 字段已随 §9.5 铁律③整改移除——结构上不可能运行期回退 mock。"""
    assert not hasattr(NavigationAgent(), "_fallback")


def test_search_poi_outage_degrades_honestly_no_mock():
    """poi.search 运行期失败 → OK + 诚实话术，无假 POI 卡，mock 不被咨询。"""
    agent = NavigationAgent()

    async def boom(*a, **kw):
        _boom()

    agent.poi.search = boom

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "充电站"},
        raw_text="附近找个充电站"))
    assert res.status == "ok"
    assert res.ui_card is None                       # 没有假列表
    assert not (res.data or {}).get("items")
    assert "暂时" in res.speech and "充电站" in res.speech


def test_reverse_geocode_outage_degrades_honestly_no_mock():
    """reverse_geocode 运行期失败 → 诚实说解析不了，不编 mock 地址。"""
    agent = NavigationAgent()

    async def boom(*a, **kw):
        _boom()

    agent.poi.reverse_geocode = boom

    res = asyncio.run(run_handle(
        agent, "navigation.reverse_geocode",
        slots={"lng": "113.95", "lat": "22.53"}, raw_text="这是什么位置"))
    assert res.status == "ok"
    assert "暂时" in res.speech
    assert (res.data or {}).get("address") in ("", None)   # 不给假地址


def test_locate_outage_degrades_honestly_no_mock():
    """「我在哪」定位反查失败 → 诚实降级，不拿 mock 地址冒充。"""
    agent = NavigationAgent()

    async def boom(*a, **kw):
        _boom()

    agent.poi.reverse_geocode = boom

    res = asyncio.run(run_handle(
        agent, "navigation.locate", slots={}, raw_text="我现在在哪",
        meta={"current_lat": "22.53", "current_lng": "113.95"}))
    assert res.status == "ok"
    assert "暂时" in res.speech
    assert (res.data or {}).get("address") in ("", None)


def test_poi_detail_outage_degrades_honestly_no_mock():
    """poi_detail 运行期失败 → 诚实降级，不出 mock 假详情卡。"""
    agent = NavigationAgent()

    async def boom(*a, **kw):
        _boom()

    agent.poi.poi_detail = boom

    res = asyncio.run(run_handle(
        agent, "navigation.poi_detail", slots={"poi_id": "B0FFG12345"},
        raw_text="看下第一个的详情"))
    assert res.status == "ok"
    assert res.ui_card is None
    assert "暂时" in res.speech


# ─── EVA 二轮批 B：时间约束(G1) / 沿途候选(G2) / 路线策略(G11) / 多途经点(G9) ───

from runtime.clock import epoch_at, local_dt
from agents._sdk.shared_state import REMINDABLE_ACTIVE
from agents.navigation.src.agent import _parse_arrive_by, _route_strategy


def _wall(ts):
    """epoch → 业务时区墙钟五元组。

    ⚠ 不用 `time.localtime`：它按**宿主本地时**解释，而本机恰好是 UTC+8，于是
    「容器 TZ=UTC 整体偏 8 小时」这族缺陷在本地永远不红，只在 CI（UTC runner）
    才暴露——被测代码走的是 `runtime.clock`，尺子也必须走同一个墙钟。
    """
    d = local_dt(ts)
    return (d.year, d.month, d.day, d.hour, d.minute)


class _RoutePoiProvider(_ScriptedPoiProvider):
    """带 get_route 的脚本化 provider：记录 strategy/waypoints/polyline 调用与搜索 kwargs。"""

    def __init__(self, responses=None, default=None, duration_min=30, points=None):
        super().__init__(responses, default)
        self.route_calls = []
        self.search_kwargs = []
        self.duration_min = duration_min
        self.points = points

    async def search(self, keyword, **kwargs):
        self.queries.append(keyword)
        self.search_kwargs.append(kwargs)
        return self.responses.get(keyword, self.default)

    async def get_route(self, origin, destination, meta=None, with_polyline=False,
                        waypoints=None, strategy=""):
        self.route_calls.append({"with_polyline": with_polyline,
                                 "waypoints": waypoints, "strategy": strategy})
        route = {"distance_km": 20.0,
                 "duration_min": self.duration_min + 10 * len(waypoints or []),
                 "steps": []}
        if with_polyline and self.points is not None:
            route["points"] = self.points
        return route


def test_parse_arrive_by_rules():
    """「五点前到」解析：裸 1-11 点取未来最近一次；段位/HH:MM/两位数字时直取。"""
    now = epoch_at(2026, 8, 14, 14, 0)
    assert _wall(_parse_arrive_by("5点", now_ts=now)) == (2026, 8, 14, 17, 0)
    now_late = epoch_at(2026, 8, 14, 20, 0)
    assert _wall(_parse_arrive_by("5点", now_ts=now_late)) == (2026, 8, 15, 5, 0)
    assert _wall(_parse_arrive_by("下午5点半", now_ts=now)) == (2026, 8, 14, 17, 30)
    assert _wall(_parse_arrive_by("17:00", now_ts=now)) == (2026, 8, 14, 17, 0)
    # 两位数字时刻：「23点」不得被单字符类错拆成「3点」（自埋缺陷回归）
    assert _wall(_parse_arrive_by("23点", now_ts=now)) == (2026, 8, 14, 23, 0)
    assert _parse_arrive_by("尽快", now_ts=now) is None


def test_route_strategy_mapping():
    """G11：路线偏好 → 高德 strategy；「风景」诚实降档为不走高速并说明。"""
    assert _route_strategy("不走高速")[0] == "6"
    assert _route_strategy("避开拥堵")[0] == "4"
    assert _route_strategy("不走高速，避堵，少收费")[0] == "9"
    s, note = _route_strategy("带我走一条风景好一点的路回家")
    assert s == "6" and "景观优先" in note
    assert _route_strategy("导航去公司") == ("", "")


def test_navigate_arrive_by_eta_judgment_and_departure_remindable(monkeypatch):
    """G1：「五点前到」→ ETA/时限判定进话术与卡片；REMINDABLE 增「出发前往」反向事件。"""
    import agents.navigation.src.agent as nav_mod
    fixed_now = epoch_at(2026, 8, 14, 14, 0)
    monkeypatch.setattr(nav_mod.time, "time", lambda: fixed_now)

    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider(
        {"实验小学": [POI(id="x", name="实验小学", address="市区", lat=31.30, lng=121.50)]},
        duration_min=30)
    ctx = make_context()
    captured = {}

    async def cap(key, value):
        captured[key] = value
        return True

    ctx.save_shared_state = cap
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "实验小学", "arrive_by": "5点"},
        raw_text="去接孩子，五点前要到实验小学", ctx=ctx,
        meta={"current_lat": "31.2", "current_lng": "121.4"}))

    assert "17:00" in res.speech and "早约" in res.speech       # 14:30 到 vs 17:00 时限
    assert res.data["margin_min"] == 150
    assert res.ui_card["eta_ts"] and res.ui_card["arrive_by_ts"]
    items = captured[REMINDABLE_ACTIVE]["items"]
    assert items[0]["title"] == "出发前往实验小学"
    assert _wall(items[0]["fire_at"]) == (2026, 8, 14, 16, 30)  # 时限-路程
    assert items[1]["title"] == "到达实验小学"


def test_stop_choice_searches_along_route_not_destination():
    """G2：顺路候选锚点 = 路线 45% 里程采样点（真沿途），不再是目的地附近；话术如实说。"""
    points = [{"lng": 120.0, "lat": 31.0, "cum_km": 5.0},
              {"lng": 120.3, "lat": 31.1, "cum_km": 10.0},
              {"lng": 120.6, "lat": 31.2, "cum_km": 18.0}]
    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider({
        "东方之门": [POI(id="d", name="东方之门", address="苏州", lat=31.32, lng=120.70)],
        "咖啡": [POI(id="c", name="沿途咖啡", address="路上", lat=31.10, lng=120.30)],
    }, points=points)
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "stop_category": "咖啡"},
        raw_text="导航去东方之门，路上买杯咖啡",
        meta={"current_lat": "31.0", "current_lng": "119.9"}))

    coffee_near = [k.get("near") for q, k in zip(agent.poi.queries, agent.poi.search_kwargs)
                   if q == "咖啡"]
    # distance 20km × 45% = 9km → 首个 cum_km≥9 的采样点 (120.3, 31.1)，不是目的地 (120.70)
    assert coffee_near and round(coffee_near[0].lat, 2) == 31.10
    assert round(coffee_near[0].lng, 2) == 120.30
    assert "沿途" in res.speech
    assert res.ui_card["purpose"] == "waypoint_choice"


def test_stop_choice_falls_back_to_destination_without_route():
    """G2 回落：拿不到路线几何（无定位）→ 仍按目的地附近搜，话术不冒充「沿途」。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [POI(id="d", name="东方之门", address="苏州", lat=31.32, lng=120.70)],
        "咖啡": [POI(id="c", name="门前咖啡", address="苏州", lat=31.32, lng=120.69)],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "stop_category": "咖啡"},
        raw_text="导航去东方之门，路上买杯咖啡"))
    assert "目的地附近" in res.speech and "沿途" not in res.speech.split("目的地附近")[0]
    assert res.ui_card["purpose"] == "waypoint_choice"


def test_navigate_multi_waypoints_preserved_in_order():
    """G9：「途经肯德基和星巴克」→ 两个途经点都进 payload/卡片，保用户口述序。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "东方之门": [POI(id="d", name="东方之门", address="苏州", lat=31.32, lng=120.70)],
        "肯德基": [POI(id="k", name="肯德基(园区店)", address="a", lat=31.30, lng=120.60)],
        "星巴克": [POI(id="s", name="星巴克(湖东店)", address="b", lat=31.31, lng=120.65)],
    })
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "东方之门", "waypoint": "肯德基和星巴克"},
        raw_text="导航去东方之门，途经肯德基和星巴克"))

    nav = next(a for a in res.actions if a["type"] == "navigate")
    assert [w["name"] for w in nav["payload"]["waypoints"]] == \
        ["肯德基(园区店)", "星巴克(湖东店)"]
    assert res.ui_card["type"] == "route_plan"
    assert [w["name"] for w in res.ui_card["waypoints"]] == \
        ["肯德基(园区店)", "星巴克(湖东店)"]


def test_route_pref_maps_to_amap_strategy():
    """G11：「不走高速」→ get_route(strategy="6")，话术如实报偏好已应用。"""
    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider(
        {"虹桥机场": [POI(id="a", name="虹桥机场", address="上海", lat=31.19, lng=121.33)]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "虹桥机场", "route_pref": "不走高速"},
        raw_text="不走高速送我去虹桥机场",
        meta={"current_lat": "31.2", "current_lng": "121.4"}))

    assert any(c["strategy"] == "6" for c in agent.poi.route_calls)
    assert "避开高速" in res.speech


# ─── EVA 二轮批 C（G6）：路线偏好记忆消费 + 历史轨迹落情景记忆 ───

def test_route_pref_from_memory_when_not_stated():
    """本轮没说偏好 → 记忆里的 route.avoid_highway 生效并如实说明来源。
    这是 route.* 谓词的第一个消费出口（此前抽取 prompt 教了、全仓零消费方）。"""
    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider(
        {"虹桥机场": [POI(id="a", name="虹桥机场", address="上海", lat=31.19, lng=121.33)]})
    ctx = make_context()
    # polarity=dislike 是真栈抽取的实际形态（「不要走高速」被合理标成不喜欢）：
    # route.* 的方向编码在谓词名里，消费侧**不得**按极性过滤（2026-08-14 真栈
    # B2 实锤：首版按 dislike 排除，把偏好挡在门外）。
    ctx._memory.recall.return_value = [
        {"text": "导航都不要走高速", "predicate": "route.avoid_highway",
         "polarity": "dislike", "scope": "profile.route"}]
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "虹桥机场"}, raw_text="导航去虹桥机场", ctx=ctx,
        meta={"current_lat": "31.2", "current_lng": "121.4"}))

    assert any(c["strategy"] == "6" for c in agent.poi.route_calls)
    assert "记得您平时不走高速" in res.speech


def test_route_pref_slot_beats_memory():
    """本轮明说「避堵」时记忆偏好让位（用户当下说的永远优先）。"""
    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider(
        {"虹桥机场": [POI(id="a", name="虹桥机场", address="上海", lat=31.19, lng=121.33)]})
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "以后导航别走高速", "predicate": "route.avoid_highway"}]
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "虹桥机场", "route_pref": "避堵"},
        raw_text="避开拥堵去虹桥机场", ctx=ctx,
        meta={"current_lat": "31.2", "current_lng": "121.4"}))

    assert any(c["strategy"] == "4" for c in agent.poi.route_calls)
    assert not any(c["strategy"] == "6" for c in agent.poi.route_calls)


def test_navigate_writes_episodic_place_memory():
    """导航成功落一条 episodic 轨迹（「上次去的那个地方」的数据源）。"""
    agent = NavigationAgent()
    agent.poi = _RoutePoiProvider(
        {"深圳湾公园": [POI(id="p", name="深圳湾公园", address="南山", lat=22.52, lng=113.94)]})
    ctx = make_context()
    captured = []

    async def cap(text, **kw):
        captured.append((text, kw))
        return True

    ctx.remember = cap
    asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳湾公园"}, raw_text="导航去深圳湾公园", ctx=ctx,
        meta={"current_lat": "22.5", "current_lng": "113.9"}))

    hits = [(t, kw) for t, kw in captured if "导航去过深圳湾公园" in t]
    assert hits, f"episodic 轨迹未写入：{captured}"
    _, kw = hits[0]
    assert kw["kind"] == "episodic" and kw["scope"] == "episodic.place"
    assert kw["value"]["name"] == "深圳湾公园" and kw["value"]["lat"] == 22.52


def test_search_poi_auto_navigate_also_writes_episodic_trail():
    """G6 轨迹写入的挂点覆盖 search_poi 自动导航分支（挂点枚举教训第三次应验：
    真栈「圆圆的湖→滴水湖」走这条路径，批 C 首版只挂 _route_plan_to 整条漏写）。"""
    agent = NavigationAgent()

    async def search(keyword, **kwargs):
        return [POI(id="d", name="滴水湖", address="临港", lat=30.90, lng=121.93)]

    agent.poi.search = search
    ctx = make_context()
    captured = []

    async def cap(text, **kw):
        captured.append((text, kw))
        return True

    ctx.remember = cap
    asyncio.run(run_handle(
        agent, "navigation.search_poi",
        slots={"keyword": "滴水湖"}, raw_text="带我去滴水湖", ctx=ctx,
        meta={"current_lat": "31.2", "current_lng": "121.4"}))

    hits = [(t, kw) for t, kw in captured if "导航去过滴水湖" in t]
    assert hits and hits[0][1]["kind"] == "episodic"
    assert hits[0][1]["scope"] == "episodic.place"

"""nearby（周边发现）契约测试。"""
import asyncio

from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.nearby.src.agent import NearbyAgent


def test_manifest_consistent():
    assert assert_manifest_consistent(NearbyAgent()) is True


def test_search_returns_place_list_card():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"cuisine": "川菜"}, raw_text="附近的川菜馆"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_list"
    assert res.ui_card["items"]                       # 有结果
    assert "lat" in res.data["items"][0]              # 结构化结果供「第N个」handoff


def test_search_incorporates_recalled_taste_preference():
    """餐饮搜索前 ctx.recall 取学到的口味偏好并体现在话术（精确读取走 predicate_prefix）。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户不吃辣", "scope": "profile.taste",
         "predicate": "taste.spicy", "confidence": 0.9}]
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"cuisine": "川菜"}, raw_text="找家川菜馆", ctx=ctx))
    assert res.status == "ok"
    assert "不吃辣" in res.speech                       # 召回偏好进了话术
    assert ctx._memory.recall.call_args.kwargs.get("predicate_prefix") == "taste."  # 精确读取


def test_search_uses_session_location_when_user_did_not_name_an_area():
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        seen.update(kwargs)
        return []

    agent.place.search = search
    asyncio.run(run_handle(
        agent, "nearby.search", slots={"cuisine": "川菜"}, raw_text="附近川菜",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))
    near = seen["near"]
    assert near is not None
    assert abs(near.lat - 39.92) < 1e-6 and abs(near.lng - 116.41) < 1e-6


def test_search_non_food_category_no_taste():
    """多类目：附近的酒店 → place_list；非餐饮不注入口味画像。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户不吃辣", "predicate": "taste.spicy"}]
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"category": "酒店"}, raw_text="附近有什么酒店", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_list"
    assert "不吃辣" not in res.speech                    # 非餐饮不带口味


def test_detail_returns_place_detail_card():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.detail",
        slots={"name": "蜀香源"}, raw_text="蜀香源怎么样"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_detail"
    assert res.ui_card.get("tel") or res.ui_card.get("open_today")  # 富字段


def test_detail_missing_target_asks():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.detail", slots={}, raw_text="看看详情"))
    assert res.status == "need_slot"


def test_order_requires_confirm():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order",
        slots={"name": "蜀香源川菜馆", "datetime": "今晚19:00", "party_size": "2"},
        raw_text="在这家订今晚7点两位"))
    assert res.status == "need_confirm"
    assert any(a["require_confirm"] for a in res.actions)


def test_order_missing_target_asks():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order", slots={}, raw_text="点单"))
    assert res.status == "need_slot"


def test_order_confirmed_is_honest_not_fake_booking():
    """预留桩：确认后诚实告知未接入、给电话+导航兜底，不假装『已订好』。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order",
        slots={"name": "蜀香源川菜馆"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert "接入中" in res.speech
    assert "订好" not in res.speech and "已预订" not in res.speech


def _capture_search():
    """返回 (agent, seen)：monkeypatch place.search 捕获透传给 provider 的参数。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kw):
        seen["keyword"] = keyword
        seen.update(kw)
        return []

    agent.place.search = search
    return agent, seen


def test_search_facility_keyword_stripped_from_whole_sentence():
    """route_hint 把整句灌进 keyword（停车场）：agent 剥壳成干净类目词 + 认出类目。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "附近的停车场"}, raw_text="附近的停车场"))
    assert seen["keyword"] == "停车场"
    assert seen["category"] == "停车"


def test_search_facility_charging_keyword():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "附近的充电站"}, raw_text="附近哪里有充电站"))
    assert seen["keyword"] == "充电站"


def test_search_price_parsed_from_raw_text_when_planner_missed_slot():
    """价位兜底：planner 没填 price_max，agent 从原话『一百以内』解析出 100。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="人均一百以内的火锅"))
    assert seen["price_max"] == 100.0


def test_search_sort_parsed_from_raw_text():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="附近评分高的火锅"))
    assert seen["sort"] == "rating"


def test_search_facility_keyword_strips_query_verbs():
    """『帮我查一查附近的停车场』→ 关键词剥成『停车场』（修『为您找到1家查查停车场』）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "帮我查一查附近的停车场"},
                           raw_text="帮我查一查附近的停车场"))
    assert seen["keyword"] == "停车场"


def test_search_price_band_from_left_right():
    """『人均一百左右』→ 区间 [约60,约140]（下限剔掉太便宜的 18/30，修『左右只当上限』）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="附近人均一百左右的餐厅"))
    assert seen["price_min"] == 60.0 and seen["price_max"] == 140.0


def test_search_price_within_is_upper_bound_only():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="人均一百以内的火锅"))
    assert seen["price_min"] == 0.0 and seen["price_max"] == 100.0


def test_search_open_now_parsed_from_raw_text():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="附近现在营业的火锅"))
    assert seen["open_now"] is True


def test_search_raw_price_band_overrides_llm_price_max_slot():
    """LLM 把『一百』填进 price_max 槽，但原话是『左右』→ 用原话区间(带下限)，不被纯上限盖过。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"price_max": "100"}, raw_text="附近人均一百左右的餐厅"))
    assert seen["price_min"] == 60.0 and seen["price_max"] == 140.0

"""food-ordering 契约测试。"""
import asyncio

from agents._sdk.testing import make_context, run_handle
from agents.food_ordering.src.agent import FoodOrderingAgent


def test_search_incorporates_recalled_taste_preference():
    """记忆重构 P2-2：点餐前 ctx.recall 取学到的口味偏好并体现在话术（精确读取走 predicate_prefix）。"""
    agent = FoodOrderingAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户不吃辣", "scope": "profile.taste",
         "predicate": "taste.spicy", "confidence": 0.9}]
    res = asyncio.run(run_handle(agent, "food.search_restaurant",
                                 slots={"cuisine": "川菜"}, raw_text="找家川菜馆", ctx=ctx))
    assert res.status == "ok"
    assert "不吃辣" in res.speech                       # 召回偏好进了话术
    assert ctx._memory.recall.call_args.kwargs.get("predicate_prefix") == "taste."  # 精确读取


def test_search_returns_card():
    res = asyncio.run(run_handle(
        FoodOrderingAgent(), "food.search_restaurant",
        slots={"cuisine": "川菜"}, raw_text="找家川菜馆"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "restaurant_list"


def test_search_uses_session_location_when_user_did_not_name_an_area():
    agent = FoodOrderingAgent()
    seen = {}

    async def search(**kwargs):
        seen.update(kwargs)
        return []

    agent.restaurant.search = search
    asyncio.run(run_handle(
        agent, "food.search_restaurant", slots={"cuisine": "川菜"}, raw_text="附近川菜",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))

    assert seen["location"] == "116.410000,39.920000"


def test_reserve_requires_confirm():
    res = asyncio.run(run_handle(
        FoodOrderingAgent(), "food.reserve",
        slots={"restaurant_name": "川菜·名店1", "datetime": "今晚19:00", "party_size": "2"},
        raw_text="订今晚7点两位"))
    assert res.status == "need_confirm"
    assert any(a["require_confirm"] for a in res.actions)


def test_reserve_missing_restaurant_asks():
    res = asyncio.run(run_handle(
        FoodOrderingAgent(), "food.reserve", slots={}, raw_text="订位"))
    assert res.status == "need_slot"


def test_reserve_confirmed_books():
    """F1 确认闭环：带 confirmed 标记时真正下单，不再追问。"""
    res = asyncio.run(run_handle(
        FoodOrderingAgent(), "food.reserve",
        slots={"restaurant_name": "川菜·名店1", "datetime": "今晚19:00", "party_size": "2"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert "订好" in res.speech
    assert res.ui_card["type"] == "reservation"

"""food-ordering 契约测试。"""
import asyncio

from agents._sdk.testing import run_handle
from agents.food_ordering.src.agent import FoodOrderingAgent


def test_search_returns_card():
    res = asyncio.run(run_handle(
        FoodOrderingAgent(), "food.search_restaurant",
        slots={"cuisine": "川菜"}, raw_text="找家川菜馆"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "restaurant_list"


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

"""info 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle（走 mock provider）。"""
import asyncio

from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents.info.src.agent import InfoAgent


def test_weather_with_city_returns_card():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={"city": "北京"}, raw_text="北京天气"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "weather"
    assert res.ui_card["city"]
    assert res.speech


def test_weather_uses_vehicle_location_when_no_city():
    ctx = make_context(context_values={"vehicle.location": "上海市"})
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={}, raw_text="天气怎么样", ctx=ctx))
    assert res.status == "ok"
    assert "上海" in res.ui_card["city"]


def test_weather_missing_city_asks():
    ctx = make_context(context_values={})  # 无车辆位置
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={}, raw_text="天气", ctx=ctx))
    assert res.status == "need_slot"
    assert "city" in res.missing_slots


def test_manifest_consistent():
    assert assert_manifest_consistent(InfoAgent()) is True

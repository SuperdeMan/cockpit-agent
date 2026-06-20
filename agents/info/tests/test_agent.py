"""info 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle（走 mock provider）。"""
import asyncio

from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents.info.src.agent import InfoAgent


# ── 天气 ──────────────────────────────────────────────────

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


# ── 天气预报 ─────────────────────────────────────────────

def test_forecast_with_city():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.forecast", slots={"city": "北京"}, raw_text="北京明天天气"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "forecast"
    assert len(res.ui_card["days"]) > 0
    assert "℃" in res.speech


def test_forecast_missing_city_asks():
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        InfoAgent(), "info.forecast", slots={}, raw_text="明天天气怎么样", ctx=ctx))
    assert res.status == "need_slot"


# ── 天气预警 ─────────────────────────────────────────────

def test_alerts_no_warning():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.alerts", slots={"city": "北京"}, raw_text="有没有天气预警"))
    assert res.status == "ok"
    assert "没有" in res.speech  # mock 返回空列表


# ── 生活指数 ─────────────────────────────────────────────

def test_indices_returns_list():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.indices", slots={"city": "北京"}, raw_text="今天适合运动吗"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "life_indices"
    assert len(res.ui_card["items"]) > 0


# ── 联网搜索 ─────────────────────────────────────────────

def test_search_returns_results():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "search_list"
    assert len(res.ui_card["items"]) > 0


def test_search_missing_query_asks():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={}, raw_text="搜一下"))
    assert res.status == "need_slot"
    assert "query" in res.missing_slots


# ── 新闻 ─────────────────────────────────────────────────

def test_news_returns_headlines():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={}, raw_text="今天有什么新闻"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "news_list"
    assert len(res.ui_card["items"]) > 0


def test_news_with_topic():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))
    assert res.status == "ok"
    assert "科技" in res.speech


# ── 股票 ─────────────────────────────────────────────────

def test_stock_returns_quote():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.stock", slots={"symbol": "茅台"}, raw_text="茅台股价"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "stock_quote"
    assert res.ui_card["price"]


def test_stock_missing_symbol_asks():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.stock", slots={}, raw_text="查一下股票"))
    assert res.status == "need_slot"
    assert "symbol" in res.missing_slots


# ── 未知意图 ─────────────────────────────────────────────

def test_unknown_intent_failed():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.unknown", slots={}, raw_text="未知"))
    assert res.status == "failed"


# ── manifest ─────────────────────────────────────────────

def test_manifest_consistent():
    assert assert_manifest_consistent(InfoAgent()) is True

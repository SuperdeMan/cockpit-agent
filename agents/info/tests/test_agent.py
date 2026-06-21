"""info 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle（走 mock provider）。"""
import asyncio
from datetime import datetime

from agents._sdk.http import ProviderError
from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents.info.src.agent import InfoAgent
from agents.info.src.providers.base import SearchResult


async def _llm_unavailable(*args, **kwargs):
    raise RuntimeError("LLM gateway unavailable")


async def _llm_numbered_answer(*args, **kwargs):
    return "1. 第一条关键结论。\n2. 第二条补充结论。"


# ── 天气 ──────────────────────────────────────────────────

class _SearchSpy:
    def __init__(self):
        self.queries = []

    async def search(self, query, **kwargs):
        self.queries.append(query)
        return [SearchResult(title="赛程", snippet="6月20日有比赛", source="fixture")]


class _UnavailableStockProvider:
    async def quote(self, *args, **kwargs):
        raise ProviderError("upstream unavailable")

    async def history(self, *args, **kwargs):
        raise AssertionError("history must not run after quote failure")


class _UnavailableSearchProvider:
    async def search(self, *args, **kwargs):
        raise ProviderError("upstream unavailable")


def test_weather_with_city_returns_card():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={"city": "北京"}, raw_text="北京天气"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "weather"
    assert res.ui_card["city"]
    assert res.speech


def test_weather_card_contains_overview_sections():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={"city": "北京"}, raw_text="北京天气"))

    assert len(res.ui_card["forecast"]) == 3
    assert res.ui_card["air_quality"]["aqi"]
    assert res.ui_card["indices"]
    assert "visibility" in res.ui_card


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


# ── 空气质量 ─────────────────────────────────────────────

def test_air_quality_returns_card():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.air_quality", slots={"city": "北京"}, raw_text="空气质量怎么样"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "air_quality"
    assert res.ui_card["aqi"]
    assert "PM" in res.speech or "空气" in res.speech


def test_air_quality_missing_city_asks():
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        InfoAgent(), "info.air_quality", slots={}, raw_text="空气好吗", ctx=ctx))
    assert res.status == "need_slot"


# ── 联网搜索 ─────────────────────────────────────────────

def test_search_returns_results():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))
    assert res.status == "ok"
    # ws2: LLM 成功用 search_answer，失败退化为 search_list（两种都接受）
    assert res.ui_card and res.ui_card["type"] in ("search_answer", "search_list")
    assert len(res.ui_card["items"]) > 0


def test_search_fallback_returns_a_brief_not_a_numbered_result_dump():
    agent = InfoAgent()
    agent.llm.complete = _llm_unavailable
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))

    # ws2 search-news-redesign: LLM 失败时退化为 search_list（用 "summary" 字段）
    assert res.ui_card["type"] == "search_list"
    assert res.ui_card["summary"] == res.speech
    assert "为您搜索到" not in res.speech
    assert "1." not in res.speech


def test_search_flattens_numbered_llm_answer_into_a_spoken_brief():
    agent = InfoAgent()
    agent.llm.complete = _llm_numbered_answer
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))

    assert "1." not in res.speech
    assert "2." not in res.speech
    assert "第一条关键结论" in res.speech


def test_search_missing_query_asks():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={}, raw_text="搜一下"))
    assert res.status == "need_slot"
    assert "query" in res.missing_slots


# ── 新闻 ─────────────────────────────────────────────────

def test_tonight_schedule_search_includes_current_date():
    agent = InfoAgent()
    search = _SearchSpy()
    agent.search = search
    agent.llm.complete = _llm_numbered_answer
    query = "今晚世界杯有哪一些赛程"

    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": query}, raw_text=query))

    today = datetime.now().strftime("%Y年%m月%d日")
    assert search.queries == [f"{query} {today} 当日赛程"]
    assert "第一条关键" in res.speech


def test_live_search_failure_does_not_fall_back_to_fabricated_results():
    agent = InfoAgent()
    agent.search = _UnavailableSearchProvider()

    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "今晚世界杯赛程"}, raw_text="今晚世界杯赛程"))

    assert res.status == "failed"
    assert res.ui_card is None
    assert "联网检索暂时不可用" in res.speech


def test_tonight_schedule_summary_receives_current_date_context():
    agent = InfoAgent()
    agent.search = _SearchSpy()
    seen = {}

    async def summary_llm(messages, **kwargs):
        seen["prompt"] = messages[-1]["content"]
        return "今晚有比赛。"

    agent.llm.complete = summary_llm
    query = "今晚世界杯赛程"
    asyncio.run(run_handle(
        agent, "info.search", slots={"query": query}, raw_text=query))

    today = datetime.now().strftime("%Y年%m月%d日")
    assert today in seen["prompt"]
    assert "实时赛程" in seen["prompt"]
    assert "按时间列出" in seen["prompt"]


def test_news_returns_headlines():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={}, raw_text="今天有什么新闻"))
    assert res.status == "ok"
    # ws2: LLM 成功用 news_digest，失败退化为 news_list（两种都接受）
    assert res.ui_card and res.ui_card["type"] in ("news_digest", "news_list")
    assert len(res.ui_card["items"]) > 0


def test_news_with_topic():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))
    assert res.status == "ok"
    assert "科技" in res.speech


def test_news_fallback_returns_summary_not_numbered_headlines():
    agent = InfoAgent()
    agent.llm.complete = _llm_unavailable
    res = asyncio.run(run_handle(
        agent, "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))

    assert res.ui_card["summary"] == res.speech
    assert "1." not in res.speech
    assert "热点新闻" not in res.speech


# ── 股票 ─────────────────────────────────────────────────

def test_stock_returns_quote():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.stock", slots={"symbol": "茅台"}, raw_text="茅台股价"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "stock_quote"
    assert res.ui_card["price"]
    assert len(res.ui_card["candles"]) >= 2


def test_stock_missing_symbol_asks():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.stock", slots={}, raw_text="查一下股票"))
    assert res.status == "need_slot"
    assert "symbol" in res.missing_slots


# ── 未知意图 ─────────────────────────────────────────────

def test_stock_provider_failure_does_not_render_a_mock_kline():
    agent = InfoAgent()
    agent.stock = _UnavailableStockProvider()
    agent._stock_eastmoney = None  # 禁用东方财富 fallback，确保测试主路径失败

    res = asyncio.run(run_handle(
        agent, "info.stock", slots={"symbol": "贵州茅台"}, raw_text="贵州茅台的股票"))

    assert res.status == "failed"
    assert res.ui_card is None
    assert "没有找到" in res.speech or "暂时无法获取" in res.speech


def test_unknown_intent_failed():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.unknown", slots={}, raw_text="未知"))
    assert res.status == "failed"


# ── manifest ─────────────────────────────────────────────

def test_manifest_consistent():
    assert assert_manifest_consistent(InfoAgent()) is True

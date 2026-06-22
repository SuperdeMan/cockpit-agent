"""info 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle（走 mock provider）。"""
import asyncio
from datetime import datetime

from agents._sdk.http import ProviderError
from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents.info.src.agent import InfoAgent, _shanghai_now, _plan_search
from agents.info.src.providers.base import SearchResult
from agents.info.src.providers.mock import MockWeatherProvider


async def _llm_unavailable(*args, **kwargs):
    raise RuntimeError("LLM gateway unavailable")


async def _llm_numbered_answer(*args, **kwargs):
    return "1. 第一条关键结论。\n2. 第二条补充结论。"


# ── 天气 ──────────────────────────────────────────────────

class _SearchSpy:
    def __init__(self):
        self.queries = []
        self.kwargs = []

    async def search(self, query, **kwargs):
        self.queries.append(query)
        self.kwargs.append(kwargs)
        return [SearchResult(title="赛程", snippet="6月20日有比赛", source="fixture",
                             content="资料正文：今晚有若干场比赛。")]


class _UnavailableStockProvider:
    async def quote(self, *args, **kwargs):
        raise ProviderError("upstream unavailable")

    async def history(self, *args, **kwargs):
        raise AssertionError("history must not run after quote failure")


class _UnavailableSearchProvider:
    async def search(self, *args, **kwargs):
        raise ProviderError("upstream unavailable")


class _UnavailableWeatherProvider:
    async def overview(self, *args, **kwargs):
        raise ProviderError("upstream unavailable")


class _LocationResolver:
    async def reverse(self, lng, lat, meta=None):
        assert (lng, lat) == (116.41, 39.92)
        return "北京市朝阳区"


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
    # PoC 阶段不再使用 vehicle.location 的 mock 默认值
    # 没有定位且没有指定城市时，应该返回 NEED_SLOT
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={}, raw_text="天气怎么样", ctx=ctx))
    assert res.status == "need_slot"
    assert "city" in res.missing_slots


def test_weather_uses_session_location_coordinates_before_vehicle_city():
    agent = InfoAgent()
    agent.location_resolver = _LocationResolver()
    res = asyncio.run(run_handle(
        agent, "info.weather", slots={}, raw_text="今天天气怎么样",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))
    assert res.status == "ok"
    assert res.ui_card["city"] == "北京市朝阳区"
    assert "116.410000" not in res.speech


def test_weather_never_shows_raw_coordinates_when_reverse_geocoding_is_unavailable():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={}, raw_text="这里天气怎么样",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))

    assert res.ui_card["city"] == "当前位置"
    assert "116.410000" not in res.speech


def test_mock_weather_varies_by_requested_city():
    provider = MockWeatherProvider()
    beijing = asyncio.run(provider.overview("北京"))
    shenzhen = asyncio.run(provider.overview("深圳"))

    assert (beijing.now.temp, beijing.now.text, beijing.now.humidity) != (
        shenzhen.now.temp, shenzhen.now.text, shenzhen.now.humidity)


def test_weather_missing_city_asks():
    ctx = make_context(context_values={})  # 无车辆位置
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={}, raw_text="天气", ctx=ctx))
    assert res.status == "need_slot"
    assert "city" in res.missing_slots


def test_weather_provider_failure_is_honest_not_mock():
    """真实天气 provider 失败时诚实报错，绝不 fallback mock 编出假天气（如无效城市）。"""
    agent = InfoAgent()
    agent.weather = _UnavailableWeatherProvider()
    res = asyncio.run(run_handle(
        agent, "info.weather", slots={"city": "当前未知的"}, raw_text="当前未知的天气"))
    assert res.status == "failed"
    assert res.ui_card is None
    assert "没查到" in res.speech
    assert "小雨" not in res.speech and "气温" not in res.speech  # 不编造


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

def test_search_returns_evidence_card_without_repeating_the_answer():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "search_result"
    assert len(res.ui_card["sources"]) > 0
    # 卡片只给证据，不复读气泡结论（无 answer/summary 字段）→ 消除重复
    assert "answer" not in res.ui_card and "summary" not in res.ui_card


def test_search_fallback_is_an_honest_brief_not_a_numbered_dump():
    agent = InfoAgent()
    agent.llm.complete = _llm_unavailable
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))

    # LLM 不可用：仍是 search_result 证据卡，置信度 low，不复读、不罗列编号
    assert res.ui_card["type"] == "search_result"
    assert res.ui_card["confidence"] == "low"
    assert "answer" not in res.ui_card and "summary" not in res.ui_card
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
    assert res.ui_card["type"] == "search_result"


def test_search_missing_query_asks():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={}, raw_text="搜一下"))
    assert res.status == "need_slot"
    assert "query" in res.missing_slots


def test_plan_search_sets_recency_window_and_news_category():
    assert _plan_search("今天世界杯结果")[0] == 2
    assert _plan_search("最近有什么大事")[0] == 7
    assert _plan_search("解释一下相对论")[0] == 0
    assert _plan_search("今天有什么新闻")[1] == "news"
    assert _plan_search("解释一下相对论")[1] == ""


def test_realtime_query_keeps_natural_phrasing_and_adds_recency():
    agent = InfoAgent()
    search = _SearchSpy()
    agent.search = search
    agent.llm.complete = _llm_numbered_answer
    # 非赛事的时效类查询（赛事类会被 _maybe_sports 路由到结构化源）
    query = "今天的台风最新消息"

    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": query}, raw_text=query))

    # 自然语言查询原样下发（不再硬拼日期/「当日赛程」），改用时效窗口
    assert search.queries == [query]
    assert search.kwargs[-1].get("recency_days") == 2
    assert "第一条关键" in res.speech


def test_live_search_failure_does_not_fall_back_to_fabricated_results():
    agent = InfoAgent()
    agent.search = _UnavailableSearchProvider()

    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "今晚的台风路径"}, raw_text="今晚的台风路径"))

    assert res.status == "failed"
    assert res.ui_card is None
    assert "联网检索暂时不可用" in res.speech


def test_grounded_synthesis_demands_abstention_not_fabrication():
    """接地合成的 prompt 必须要求「无依据即弃权」，且不得保留旧的「逼答」指令。"""
    agent = InfoAgent()
    agent.search = _SearchSpy()
    seen = {}

    async def synth_llm(messages, **kwargs):
        seen["system"] = messages[0]["content"]
        seen["prompt"] = messages[-1]["content"]
        return ('{"answer": "未能从检索到的资料中确认台风的实时路径。", '
                '"key_points": [], "confidence": "low", "used_sources": []}')

    agent.llm.complete = synth_llm
    query = "今天的台风最新情况"
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": query}, raw_text=query))

    today = f"{_shanghai_now():%Y年%m月%d日}"
    assert today in seen["prompt"]
    assert "禁止编造" in seen["prompt"]
    assert "未能从检索到的资料中确认" in seen["prompt"]
    assert "宁可说没有也绝不编造" in seen["system"]
    assert "不要轻易说" not in seen["prompt"]      # 旧「逼答」指令已移除
    # 合成结果如实透出：诚实弃权 + low 置信度，不编造
    assert res.speech == "未能从检索到的资料中确认台风的实时路径。"
    assert res.ui_card["confidence"] == "low"
    assert res.ui_card["type"] == "search_result"


# ── 赛事 ─────────────────────────────────────────────────

class _SportsStub:
    def __init__(self, fixtures):
        self._fixtures = fixtures
        self.calls = []

    async def fixtures(self, **kwargs):
        self.calls.append(kwargs)
        return self._fixtures


class _FailSports:
    async def fixtures(self, **kwargs):
        raise ProviderError("upstream down")


def _fx(**kw):
    from agents.info.src.providers.base import SportsFixture
    return SportsFixture(**kw)


def test_sports_query_routes_to_structured_data_not_search():
    agent = InfoAgent()
    agent.search = _UnavailableSearchProvider()  # 若误走通用搜索会失败，反证路由生效
    agent.sports = _SportsStub([
        _fx(league="FIFA 世界杯", league_id=1, home="巴西", away="海地", home_goals="3",
            away_goals="0", status="finished", status_text="已结束"),
        _fx(league="FIFA 世界杯", league_id=1, home="美国", away="澳大利亚",
            status="scheduled", status_text="未开赛"),
        # 同日其它联赛——验证客户端按 league_id 过滤（不应混入世界杯结果）
        _fx(league="Premier League", league_id=39, home="甲", away="乙",
            home_goals="1", away_goals="1", status="finished", status_text="已结束"),
    ])
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "今天世界杯赛程及结果"},
        raw_text="今天世界杯赛程及结果"))

    assert res.status == "ok"
    assert res.ui_card["type"] == "sports_scores"
    assert "巴西 3-0 海地" in res.speech
    assert "已结束1场" in res.speech and "未开赛1场" in res.speech
    assert len(res.ui_card["fixtures"]) == 2
    # 真实结构化：已结束有比分，未开赛无比分（不编造）
    assert res.ui_card["fixtures"][0]["score"] == "3-0"
    assert res.ui_card["fixtures"][1]["score"] == ""


def test_sports_routes_only_with_competition_and_intent_word():
    agent = InfoAgent()
    agent.sports = _SportsStub([])
    # 有「西甲」但无赛事意图词 → 不路由
    assert asyncio.run(agent._maybe_sports("西甲是什么意思", meta=None)) is None
    # 有意图词但无已知赛事 → 不路由
    assert asyncio.run(agent._maybe_sports("今天有什么比赛", meta=None)) is None
    # 两者都有 → 路由
    res = asyncio.run(agent._maybe_sports("英超今天赛程", meta=None))
    assert res is not None and res.ui_card["type"] == "sports_scores"


def test_sports_empty_is_honest_no_fabrication():
    agent = InfoAgent()
    agent.sports = _SportsStub([])
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯比分"}, raw_text="世界杯比分"))
    assert res.status == "ok"
    assert "没有查询到" in res.speech
    assert res.ui_card["type"] == "sports_scores"
    assert res.ui_card["fixtures"] == []


def test_sports_provider_failure_falls_back_to_search_then_honest_failure():
    agent = InfoAgent()
    agent.sports = _FailSports()
    agent.search = _UnavailableSearchProvider()  # 赛事失败回落搜索，搜索也不可用 → 诚实失败
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "世界杯比分"}, raw_text="世界杯比分"))
    assert res.status == "failed"
    assert "联网检索暂时不可用" in res.speech


def test_sports_uses_raw_text_for_date_when_slot_query_is_cleaned():
    """planner 清洗 slots 可能丢「明天」；按日期查应以 raw_text 为准（修实测 bug）。"""
    from datetime import timedelta
    agent = InfoAgent()
    spy = _SportsStub([])
    agent.sports = spy
    asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯赛程"},   # slot 已丢"明天"
        raw_text="明天世界杯有哪些赛程"))
    tomorrow = (_shanghai_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert spy.calls[-1].get("date") == tomorrow


def test_sports_followup_combines_query_slot_and_raw_text():
    """跟进句「明天的呢」：赛事名来自 planner 解析的 query 槽位，日期来自 raw_text；
    组合识别后应路由到快 sports（而非落慢搜索导致体感卡死）。"""
    from datetime import timedelta
    agent = InfoAgent()
    spy = _SportsStub([])
    agent.sports = spy
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "世界杯 赛程"}, raw_text="明天的呢"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "sports_scores"
    tomorrow = (_shanghai_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert spy.calls[-1].get("date") == tomorrow


# ── 新闻 ─────────────────────────────────────────────────


def test_news_returns_headlines():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={}, raw_text="今天有什么新闻"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "news_brief"
    assert len(res.ui_card["items"]) > 0
    assert "summary" not in res.ui_card  # 卡片不复读气泡结论


def test_news_with_topic():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))
    assert res.status == "ok"
    assert "科技" in res.speech


def test_news_fallback_speaks_briefing_card_has_no_summary_dup():
    agent = InfoAgent()
    agent.llm.complete = _llm_unavailable
    res = asyncio.run(run_handle(
        agent, "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))

    assert res.ui_card["type"] == "news_brief"
    assert "科技" in res.speech                                  # 兜底 head 含话题
    # 卡片只放可点开来源、不复述摘要 → 不与 TTS 语音重复
    assert all("summary" not in it for it in res.ui_card["items"])


def test_news_speaks_distilled_briefing_with_clickable_source_card():
    """座舱看新闻=TTS 播报：语音含总览+逐条一句话提炼；卡片只给可点开来源（不复述摘要）。"""
    agent = InfoAgent()

    async def news_llm(messages, **kwargs):
        return ('{"overview":"今日多条要闻速览","summaries":'
                '{"1":"甲事件的一句话","2":"乙事件的一句话"}}')

    class _ExaNews:
        async def search(self, query, **kwargs):
            return [
                SearchResult(title="新闻一|财经栏目", url="https://e.com/1", snippet="正文一",
                             source="e.com", published="2026-06-22T08:00:00Z", content="正文一"),
                SearchResult(title="新闻二", url="https://e.com/2", snippet="正文二",
                             source="e.com", published="2026-06-22T07:00:00Z", content="正文二"),
            ]

    agent.llm.complete = news_llm
    agent.search = _ExaNews()
    res = asyncio.run(run_handle(
        agent, "info.news", slots={}, raw_text="今天有哪些值得关注的新闻"))

    assert res.status == "ok" and res.ui_card["type"] == "news_brief"
    # 语音=总览 + 逐条一句话提炼（TTS 播报，听完即可）
    assert "今日多条要闻速览" in res.speech
    assert "甲事件的一句话" in res.speech and "乙事件的一句话" in res.speech
    assert "1." in res.speech and "2." in res.speech
    # 卡片=可点开来源（标题清理掉栏目尾巴 + url），不含摘要 → 不与语音重复
    its = res.ui_card["items"]
    assert its[0]["url"] == "https://e.com/1"
    assert its[0]["title"] == "新闻一"
    assert all("summary" not in it for it in its)


def test_news_dedups_repeated_titles():
    items = [{"title": "今日热点", "source": "a", "publish_time": "", "snippet": "x"},
             {"title": "今日热点", "source": "b", "publish_time": "", "snippet": "y"},
             {"title": "另一条", "source": "c", "publish_time": "", "snippet": "z"}]
    out = InfoAgent._dedup_news(items)
    assert [n["title"] for n in out] == ["今日热点", "另一条"]


def test_news_prefers_exa_full_content_over_news_provider():
    agent = InfoAgent()

    class _ExaNews:
        async def search(self, query, **kwargs):
            return [SearchResult(title="Exa新闻A", url="https://e.com/a", snippet="摘要A",
                                 source="e.com", published="2026-06-22T08:00:00Z",
                                 content="正文A")]

    class _NewsShouldNotRun:
        async def headlines(self, **kwargs):
            raise AssertionError("Exa 有结果时不应回落新闻 provider")

    agent.search = _ExaNews()
    agent.news = _NewsShouldNotRun()
    res = asyncio.run(run_handle(agent, "info.news", slots={}, raw_text="今天有什么新闻"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "news_brief"
    assert res.ui_card["items"][0]["title"] == "Exa新闻A"


def test_news_junk_filter_drops_index_and_error_pages():
    j = InfoAgent._is_junk_news
    assert j("新闻中心首页", "https://news.sina.com.cn/", "...") is True       # 首页标题
    assert j("Yahoo新聞", "https://tw.news.yahoo.com/x", "您的浏览器版本过低") is True  # 错误页正文
    assert j("某媒体", "https://e.com/", "正文") is True                       # 纯域名根
    assert j("正常新闻标题", "https://e.com/a/123", "正文内容") is False        # 正常文章保留
    assert j("正常标题", "", "serpapi 摘要无 url") is False                    # 无 url 不误删


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

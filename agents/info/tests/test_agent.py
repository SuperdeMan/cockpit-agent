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
    def __init__(self, fixtures=None, events=None, scorers=None):
        self._fixtures = fixtures or []
        self._events = events or []
        self._scorers = scorers          # list | dict{season: list|Exception} | None
        self.calls = []
        self.events_calls = []
        self.scorers_calls = []

    async def fixtures(self, **kwargs):
        self.calls.append(kwargs)
        return self._fixtures

    async def events(self, fixture_id, meta=None):
        self.events_calls.append(fixture_id)
        return self._events

    async def top_scorers(self, league, season, meta=None):
        self.scorers_calls.append((league, season))
        s = self._scorers
        if isinstance(s, dict):
            v = s.get(season)
            if v is None:
                return []                # 未脚本赛季 → 空（触发继续回退）
            if isinstance(v, Exception):
                raise v
            return v
        return s or []


class _FailSports:
    async def fixtures(self, **kwargs):
        raise ProviderError("upstream down")


def _fx(**kw):
    from agents.info.src.providers.base import SportsFixture
    return SportsFixture(**kw)


def _goal(**kw):
    from agents.info.src.providers.base import GoalEvent
    return GoalEvent(**kw)


def _scorer(**kw):
    from agents.info.src.providers.base import TopScorer
    return TopScorer(**kw)


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


def test_sports_fixtures_carry_country_flags():
    """赛事卡每支球队带国旗 emoji（后端权威注入，队名映射、mock/降级也有）。"""
    from agents.info.src.providers.sports_apifootball import flag_for
    assert flag_for("巴西") == "🇧🇷" and flag_for("阿根廷") == "🇦🇷" and flag_for("日本") == "🇯🇵"
    assert flag_for("未知队") == ""        # 未命中 → 空（不编造）
    agent = InfoAgent()
    agent.sports = _SportsStub([
        _fx(league="FIFA 世界杯", league_id=1, home="巴西", away="海地", home_goals="3",
            away_goals="0", status="finished", status_text="已结束"),
    ])
    res = asyncio.run(run_handle(
        agent, "info.search", slots={"query": "今天世界杯赛程"}, raw_text="今天世界杯赛程"))
    fx = res.ui_card["fixtures"][0]
    assert fx["home_flag"] == "🇧🇷" and fx["away_flag"] == "🇭🇹"


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


def test_pick_fixture_ordinal_and_team():
    """『第N场/首场/最后一场/队名』→ 定位具体某场；纯比分查询不定位。"""
    fxs = [_fx(home="阿根廷", away="奥地利"), _fx(home="法国", away="伊拉克"),
           _fx(home="挪威", away="塞内加尔")]
    assert InfoAgent._pick_fixture("第一场", fxs).home == "阿根廷"
    assert InfoAgent._pick_fixture("第二场怎么样", fxs).home == "法国"
    assert InfoAgent._pick_fixture("最后一场", fxs).home == "挪威"
    assert InfoAgent._pick_fixture("伊拉克那场谁赢了", fxs).away == "伊拉克"
    assert InfoAgent._pick_fixture("世界杯比分", fxs) is None


def test_sports_match_detail_by_ordinal_reports_scorer():
    """『第一场是谁进的球』→ 定位首场、拉进球事件、语音报射手与分钟、卡片只剩该场带进球。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(
        [_fx(league="FIFA 世界杯", league_id=1, home="阿根廷", away="奥地利",
             home_goals="1", away_goals="0", status="live", status_text="中场",
             elapsed="45", fixture_id=1489399, home_id=26, away_id=775),
         _fx(league="FIFA 世界杯", league_id=1, home="法国", away="伊拉克",
             status="scheduled", status_text="未开赛", fixture_id=2)],
        events=[_goal(minute="38", team_id=26, player="L. Messi", detail="进球")])
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"},
        raw_text="第一场比赛是谁进的球"))

    assert res.status == "ok"
    assert res.ui_card["type"] == "sports_scores"
    assert len(res.ui_card["fixtures"]) == 1                     # 只剩被选中的那场
    assert res.ui_card["fixtures"][0]["home"] == "阿根廷"
    goals = res.ui_card["fixtures"][0]["goals"]
    assert goals[0]["player"] == "L. Messi" and goals[0]["team"] == "home"
    assert "L. Messi" in res.speech and "38" in res.speech       # 语音含射手与分钟
    assert agent.sports.events_calls == [1489399]               # 用 fixture_id 查事件


def test_sports_followup_resolves_league_from_history():
    """跟进句不带联赛名（『第一场是谁进的球』）→ 从对话历史回填『世界杯』再定位。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(
        [_fx(league="FIFA 世界杯", league_id=1, home="阿根廷", away="奥地利",
             home_goals="1", away_goals="0", status="live", status_text="中场",
             fixture_id=100, home_id=26, away_id=775)],
        events=[_goal(minute="38", team_id=26, player="L. Messi", detail="进球")])
    ctx = make_context(history=[
        {"role": "user", "text": "帮我查一下当前世界杯的赛况"},
        {"role": "assistant", "text": "今天FIFA世界杯共4场比赛…"},
    ])
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={}, raw_text="第一场是谁进的球", ctx=ctx))

    assert res.status == "ok"
    assert res.ui_card["fixtures"][0]["home"] == "阿根廷"
    assert "L. Messi" in res.speech


def test_sports_list_request_with_team_stays_list():
    """带队名但属『列全部』诉求（有哪些/还有）→ 仍列表，不误入单场进球详情。"""
    agent = InfoAgent()
    agent.sports = _SportsStub([
        _fx(league="FIFA 世界杯", league_id=1, home="阿根廷", away="奥地利",
            home_goals="1", away_goals="0", status="live", status_text="中场", fixture_id=1),
        _fx(league="FIFA 世界杯", league_id=1, home="法国", away="伊拉克",
            status="scheduled", status_text="未开赛", fixture_id=2)])
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"},
        raw_text="世界杯今天除了阿根廷还有哪些场"))

    assert len(res.ui_card["fixtures"]) == 2     # 列表
    assert agent.sports.events_calls == []        # 没去查进球


def test_sports_match_detail_no_goal_yet_is_honest():
    """选中的比赛 0-0 进行中 → 诚实『目前还没有进球』，不编造。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(
        [_fx(league="FIFA 世界杯", league_id=1, home="德国", away="日本",
             home_goals="0", away_goals="0", status="live", status_text="上半场",
             elapsed="20", fixture_id=5, home_id=1, away_id=2)],
        events=[])
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"},
        raw_text="第一场进球了吗"))
    assert "还没有进球" in res.speech
    assert res.ui_card["fixtures"][0]["goals"] == []


def test_season_candidates_world_cup_and_league():
    from datetime import datetime
    from agents.info.src.agent import _season_candidates
    assert _season_candidates(1, datetime(2026, 6, 23)) == [2026, 2022]   # 世界杯每4年
    assert _season_candidates(39, datetime(2026, 6, 23))[0] == 2025       # 上半年→上一年


def test_scorers_request_routes_to_topscorers_not_fixtures():
    """『世界杯射手榜』→ 走射手榜（sports_scorers 卡），不取赛程、不答非所问。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(
        fixtures=[_fx(league="FIFA 世界杯", league_id=1, home="阿根廷", away="奥地利")],
        scorers={2022: [_scorer(rank=1, player="Kylian Mbappé", team="法国", goals=8),
                        _scorer(rank=2, player="L. Messi", team="阿根廷", goals=7)]})
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"}, raw_text="世界杯射手榜"))

    assert res.status == "ok"
    assert res.ui_card["type"] == "sports_scorers"
    assert agent.sports.calls == []                       # 没取赛程
    assert res.ui_card["scorers"][0]["player"] == "Kylian Mbappé"
    assert "Kylian Mbappé" in res.speech and "8球" in res.speech


def test_scorers_falls_back_to_accessible_season_with_label():
    """本届赛季被免费档挡 → 回退最近可用赛季(2022)并明确标注。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(scorers={
        2026: ProviderError("Free plans do not have access to this season"),
        2022: [_scorer(rank=1, player="Kylian Mbappé", team="法国", goals=8)]})
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"}, raw_text="世界杯射手榜"))

    assert res.status == "ok"
    assert "2022赛季" in res.speech and res.ui_card["season"] == "2022赛季"
    assert (1, 2026) in agent.sports.scorers_calls and (1, 2022) in agent.sports.scorers_calls


def test_scorers_all_unavailable_is_honest():
    """所有可用赛季都取不到 → 诚实说获取不到，不编造、不退化成赛程。"""
    agent = InfoAgent()
    agent.sports = _SportsStub(scorers={})   # 任何赛季都空
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"}, raw_text="世界杯射手榜"))
    assert res.status == "failed"
    assert "射手榜" in res.speech


def test_is_fresh_sensitive_marks_rankings():
    from agents.info.src.agent import _is_fresh_sensitive
    assert _is_fresh_sensitive("世界杯历史总射手榜")
    assert _is_fresh_sensitive("英超积分榜排名")
    assert not _is_fresh_sensitive("讲个笑话")


def test_alltime_scorers_routes_to_search_not_season_topscorers():
    """『世界杯总射手榜』(历史/累计)→ 通用搜索接地合成真实历史榜，不调按赛季的 topscorers。"""
    from agents.info.src.providers.base import SearchResult
    agent = InfoAgent()
    agent.sports = _SportsStub(scorers={2022: [_scorer(rank=1, player="X", team="Y", goals=9)]})
    captured = {}

    class _Search:
        async def search(self, query, **kw):
            captured["query"] = query
            return [SearchResult(title="世界杯历史射手榜", url="http://x",
                                 snippet="克洛泽16球居首", source="x",
                                 content="克洛泽16球、罗纳尔多15球、盖德穆勒14球")]

    agent.search = _Search()
    res = asyncio.run(run_handle(
        agent, "info.sports", slots={"query": "世界杯"},
        raw_text="世界杯的总射手榜帮我查下"))

    assert res.status == "ok"
    assert agent.sports.scorers_calls == []                 # 没调按赛季的 topscorers
    assert "历史" in captured.get("query", "")              # 改写成历史总射手榜再搜
    assert "射手" in captured.get("query", "")


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


def test_news_fallback_card_includes_summary():
    agent = InfoAgent()
    agent.llm.complete = _llm_unavailable
    res = asyncio.run(run_handle(
        agent, "info.news", slots={"topic": "科技"}, raw_text="科技新闻"))

    assert res.ui_card["type"] == "news_brief"
    assert "科技" in res.speech                                  # 兜底 head 含话题
    # 卡片每条带一句话摘要（车机一屏可扫读，对症「卡片看不到摘要」）；LLM 兜底时用首句兜底
    assert all("summary" in it for it in res.ui_card["items"])
    assert "summary" not in res.ui_card                          # 仅 item 级摘要，卡片顶层不复读


def test_news_speaks_distilled_briefing_with_clickable_source_card():
    """座舱看新闻=TTS 播报总览+逐条提炼；卡片同样带标题+一句话摘要+可点开来源（车机一屏可读）。"""
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

    class _NoProvider:                       # 新闻 provider 返回空 → 无 topic 时回落 Exa（本测专测 Exa 路）
        async def headlines(self, **kwargs):
            return []

    agent.llm.complete = news_llm
    agent.search = _ExaNews()
    agent.news = _NoProvider()
    res = asyncio.run(run_handle(
        agent, "info.news", slots={}, raw_text="今天有哪些值得关注的新闻"))

    assert res.status == "ok" and res.ui_card["type"] == "news_brief"
    # 语音=总览 + 逐条一句话提炼（TTS 播报，听完即可）
    assert "今日多条要闻速览" in res.speech
    assert "甲事件的一句话" in res.speech and "乙事件的一句话" in res.speech
    assert "1." in res.speech and "2." in res.speech
    # 卡片=标题(清理栏目尾巴)+url+一句话摘要（车机一屏可扫读，与语音一致）
    its = res.ui_card["items"]
    assert its[0]["url"] == "https://e.com/1"
    assert its[0]["title"] == "新闻一"
    assert its[0]["summary"] == "甲事件的一句话" and its[1]["summary"] == "乙事件的一句话"


def test_news_dedups_repeated_titles():
    items = [{"title": "今日热点", "source": "a", "publish_time": "", "snippet": "x"},
             {"title": "今日热点", "source": "b", "publish_time": "", "snippet": "y"},
             {"title": "另一条", "source": "c", "publish_time": "", "snippet": "z"}]
    out = InfoAgent._dedup_news(items)
    assert [n["title"] for n in out] == ["今日热点", "另一条"]


def test_news_topic_prefers_exa_full_content():
    """话题新闻优先 Exa（返回全文利于逐条摘要）；Exa 有结果不回落新闻 provider。"""
    agent = InfoAgent()

    class _ExaNews:
        async def search(self, query, **kwargs):
            return [SearchResult(title="Exa新闻A", url="https://e.com/a", snippet="英伟达发布新品",
                                 source="e.com", published="2026-06-22T08:00:00Z",
                                 content="正文A")]

    class _NewsShouldNotRun:
        async def headlines(self, **kwargs):
            raise AssertionError("话题新闻 Exa 有结果时不应回落新闻 provider")

    agent.search = _ExaNews()
    agent.news = _NewsShouldNotRun()
    res = asyncio.run(run_handle(agent, "info.news", slots={"topic": "英伟达"}, raw_text="英伟达新闻"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "news_brief"
    assert res.ui_card["items"][0]["title"] == "Exa新闻A"


def test_news_uses_llm_simplified_title():
    """LLM 返回的简体标题（繁→简）用于卡片显示（台/港源转简体），无 LLM 标题则退回原标题。"""
    agent = InfoAgent()

    async def news_llm(messages, **kwargs):
        return ('{"overview":"今日要闻","titles":{"1":"货轮遭袭 长荣海运：人员均安"},"summaries":{}}')

    class _ExaNews:
        async def search(self, query, **kwargs):
            return [SearchResult(title="貨輪遭襲 長榮海運：人員均安 ｜ 公視新聞網 PNN",
                                 url="https://e.com/1", snippet="正文", source="e.com",
                                 published="", content="正文")]

    class _NoProvider:
        async def headlines(self, **kwargs):
            return []

    agent.llm.complete = news_llm
    agent.search = _ExaNews()
    agent.news = _NoProvider()
    res = asyncio.run(run_handle(agent, "info.news", slots={}, raw_text="今天有哪些值得关注的新闻"))
    assert res.ui_card["items"][0]["title"] == "货轮遭袭 长荣海运：人员均安"   # 简体、无繁体、无来源尾巴


def test_news_general_falls_back_to_provider_when_exa_empty():
    """综合要闻 Exa 空时回落 SerpApi 新闻源（多来源头条），时效过滤+去重后成卡。"""
    agent = InfoAgent()

    class _EmptyExa:
        async def search(self, query, **kwargs):
            return []

    class _Provider:
        async def headlines(self, topic="", limit=5, meta=None):
            from agents.info.src.providers.base import NewsItem
            return [NewsItem(title="头条一", summary="要闻一详情更具体", source="新华网",
                             publish_time="", url="https://news.cn/1"),
                    NewsItem(title="头条二", summary="要闻二详情更具体", source="人民网",
                             publish_time="", url="https://people.com.cn/2")]

    agent.search = _EmptyExa()
    agent.news = _Provider()
    res = asyncio.run(run_handle(agent, "info.news", slots={}, raw_text="今天有哪些值得关注的新闻"))
    assert res.status == "ok" and res.ui_card["type"] == "news_brief"
    titles = [it["title"] for it in res.ui_card["items"]]
    assert "头条一" in titles and "头条二" in titles
    assert len({it["source"] for it in res.ui_card["items"]}) >= 2   # 多来源


def test_news_junk_filter_drops_index_and_error_pages():
    j = InfoAgent._is_junk_news
    assert j("新闻中心首页", "https://news.sina.com.cn/", "...") is True       # 首页标题
    assert j("Yahoo新聞", "https://tw.news.yahoo.com/x", "您的浏览器版本过低") is True  # 错误页正文
    assert j("某媒体", "https://e.com/", "正文") is True                       # 纯域名根
    assert j("正常新闻标题", "https://e.com/a/123", "正文内容") is False        # 正常文章保留
    assert j("正常标题", "", "serpapi 摘要无 url") is False                    # 无 url 不误删
    assert j("即時", "https://x.com/news", "") is True                       # 栏目/版块名=版块页
    assert j("最新消息", "https://news.un.org/zh", "") is True
    assert j("國際", "https://cna.com.tw/x", "") is True
    assert j("头条一", "https://e.com/a/1", "正文") is False                  # 非精确栏目名的短标题保留


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


def test_extract_news_subject_from_complex_query():
    """复杂多意图句里漏抽 topic 时，从原句兜底提取新闻主体。"""
    from agents.info.src.agent import _extract_news_subject
    assert _extract_news_subject("查一下今天英伟达最新消息、股价，以及对汽车智能座舱行业有没有影响") == "英伟达"
    assert _extract_news_subject("帮我看看苹果的新闻") == "苹果"
    assert _extract_news_subject("看看小米最新动态") == "小米"
    # 泛新闻/疑问句不强行提主体 → 空（交"今日值得关注"默认 → 走综合新闻 provider 而非话题 Exa）
    assert _extract_news_subject("今天有什么新闻") == ""
    assert _extract_news_subject("今天有哪些值得关注的新闻") == ""   # 子串含"哪些/值得关注"→泛新闻
    assert _extract_news_subject("最近有什么热点") == ""
    assert _extract_news_subject("讲个笑话") == ""

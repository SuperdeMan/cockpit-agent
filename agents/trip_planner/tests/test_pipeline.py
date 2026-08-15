"""行程流水线单测（P0）—— propose/ground/solve/narrate。

用 FakePOI（实现 search/get_route）+ AsyncMock llm 驱动确定性断言，不起 gRPC、不打真实高德。
"""
import asyncio
from unittest.mock import AsyncMock

from agents.navigation.src.providers.base import POI
from agents.trip_planner.src import pipeline
from agents.trip_planner.src.models import Trip, Day, Stop


def _poi(name, lat=30.0, lng=120.0, rating=4.5) -> POI:
    return POI(id=f"id_{name}", name=name, address=f"{name}地址",
               lat=lat, lng=lng, rating=rating)


class FakePOI:
    """最小 POIProvider：按关键词子串返回 POI；get_route 返回可配置路线。"""
    def __init__(self, search_map=None, route=None, default=None):
        self.search_map = search_map or {}
        self.route = route or {"distance_km": 5.0, "duration_min": 12, "points": []}
        self.default = default
        self.calls = []

    async def search(self, keyword, near=None, category="", rating_min=0,
                     limit=5, page=1, meta=None):
        self.calls.append(keyword)
        for k, v in self.search_map.items():
            if k in keyword:
                return v[:limit]
        return (self.default(keyword)[:limit] if self.default else [])

    async def get_route(self, origin, destination, meta=None,
                        with_polyline=False, waypoints=None):
        return self.route


# ─── propose ───

def test_propose_restricts_to_pool():
    """LLM 只能从池里选名字，列表外的幻觉名被丢弃。"""
    pool = ["西湖", "灵隐寺", "宋城"]
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=(
        '{"days":[{"day_index":1,"theme":"湖光","stops":'
        '[{"name":"西湖","type":"attraction"},{"name":"不存在的仙境","type":"attraction"}]}]}'))
    sk = asyncio.run(pipeline.propose(llm, "杭州", "1", "", pool, "杭州一日游"))
    names = [s["name"] for s in sk["days"][0]["stops"]]
    assert "西湖" in names
    assert "不存在的仙境" not in names


def test_propose_fallback_on_bad_json():
    """LLM 输出非 JSON → 确定性兜底分配，按天不空。"""
    pool = ["西湖", "灵隐寺", "宋城", "千岛湖"]
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="抱歉我说点别的")
    sk = asyncio.run(pipeline.propose(llm, "杭州", "2", "带老人", pool, ""))
    assert len(sk["days"]) == 2
    assert all(d["stops"] for d in sk["days"])


def test_propose_llm_exception_fallback():
    pool = ["西湖", "灵隐寺"]
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))
    sk = asyncio.run(pipeline.propose(llm, "杭州", "1", "", pool, ""))
    assert sk["days"] and sk["days"][0]["stops"]


# ─── ground ───

def test_ground_uses_pool_coords_no_research():
    """骨架名命中池内 → 直接复用池 POI 坐标，不再触发搜索。"""
    pool = [_poi("西湖", lat=30.25, lng=120.15)]
    sk = {"days": [{"day_index": 1, "theme": "",
                    "stops": [{"name": "西湖", "type": "attraction"}]}]}
    prov = FakePOI()
    trip = asyncio.run(pipeline.ground(prov, sk, pool, {}, dest="杭州"))
    s = trip.itinerary[0].stops[0]
    assert s.grounded and s.poi["lat"] == 30.25
    assert prov.calls == []      # 池内命中，零搜索


def test_ground_rejects_mismatched_name():
    """搜索返回「挂错名的非空结果」（高德对俗称返回邻近无关 POI）→ 该 stop 不接地。"""
    sk = {"days": [{"day_index": 1,
                    "stops": [{"name": "天坛公园", "type": "attraction"}]}]}
    prov = FakePOI(default=lambda kw: [_poi("V东滨店", lat=22.5, lng=114.0)])
    trip = asyncio.run(pipeline.ground(prov, sk, [], {}, dest="北京"))
    s = trip.itinerary[0].stops[0]
    assert not s.grounded and s.poi is None


# ─── solve ───

def test_solve_weaves_charging_into_leg():
    """长途超续航 → leg 带按 SoC 接地的充电站；leg 距离/时长来自真实路线。"""
    trip = Trip(destination="远途", days=1)
    d = Day(day_index=1, stops=[
        Stop(stop_id="s1", name="A", grounded=True,
             poi={"name": "A", "lat": 30.0, "lng": 120.0}, dwell_min=60),
        Stop(stop_id="s2", name="B", grounded=True,
             poi={"name": "B", "lat": 31.0, "lng": 121.0}, dwell_min=60)])
    trip.itinerary = [d]
    points = [{"lat": 30 + i * 0.01, "lng": 120, "cum_km": i * 20} for i in range(60)]
    route = {"distance_km": 1180.0, "duration_min": 600, "points": points}
    prov = FakePOI(search_map={"充电站": [_poi("沿途充电站", 30.5, 120.5)]}, route=route)
    out = asyncio.run(pipeline.solve(prov, trip, 50, {},
                                     full_range_km=500, day_cap_min=100000))
    leg = out.itinerary[0].legs[0]
    assert leg.distance_km == 1180.0 and leg.drive_min == 600
    assert leg.charging_stops and leg.charging_stops[0]["name"] == "沿途充电站"
    assert leg.soc_before == 50


def test_solve_sufficient_range_no_charge():
    """续航足够（短途）→ leg 无充电点。"""
    trip = Trip(destination="近郊", days=1)
    d = Day(day_index=1, stops=[
        Stop(stop_id="s1", name="A", grounded=True, poi={"name": "A", "lat": 30, "lng": 120}),
        Stop(stop_id="s2", name="B", grounded=True, poi={"name": "B", "lat": 30.1, "lng": 120.1})])
    trip.itinerary = [d]
    prov = FakePOI(route={"distance_km": 12.0, "duration_min": 20, "points": []})
    out = asyncio.run(pipeline.solve(prov, trip, 80, {}, full_range_km=500))
    assert out.itinerary[0].legs[0].charging_stops == []


def test_solve_weaves_charging_from_current_location_to_first_stop():
    """The deterministic solver includes the inbound road-trip leg."""
    trip = Trip(destination="杭州", days=1)
    trip.itinerary = [Day(day_index=1, stops=[
        Stop(
            stop_id="s1",
            name="西湖",
            grounded=True,
            poi={"name": "西湖", "lat": 30.25, "lng": 120.16},
        ),
    ])]
    points = [
        {"lat": 22.53 + i * 0.1, "lng": 113.95, "cum_km": i * 20}
        for i in range(60)
    ]
    provider = FakePOI(
        search_map={"充电站": [_poi("沿途充电站", 24.5, 114.0)]},
        route={"distance_km": 1180.0, "duration_min": 780, "points": points},
    )

    out = asyncio.run(pipeline.solve(
        provider,
        trip,
        30,
        {"current_lat": "22.5333", "current_lng": "113.9505"},
        full_range_km=500,
        day_cap_min=100000,
    ))

    inbound = out.itinerary[0].legs[0]
    assert inbound.from_stop_id == "__origin__"
    assert inbound.to_stop_id == "s1"
    assert inbound.distance_km == 1180.0
    assert inbound.charging_stops[0]["name"] == "沿途充电站"
    assert out.ev["start_soc"] == 30


def test_solve_reflow_day_cap():
    """单日（驾驶+游览）超上限 → 尾部 stop 顺延次日。"""
    trip = Trip(destination="X", days=1)
    d = Day(day_index=1, stops=[
        Stop(stop_id=f"s{i}", name=f"P{i}", grounded=True,
             poi={"name": f"P{i}", "lat": 30 + i * 0.01, "lng": 120}, dwell_min=120)
        for i in range(4)])
    trip.itinerary = [d]
    prov = FakePOI(route={"distance_km": 5.0, "duration_min": 60, "points": []})
    out = asyncio.run(pipeline.solve(prov, trip, 80, {},
                                     full_range_km=500, day_cap_min=300))
    assert len(out.itinerary) >= 2
    assert len(out.itinerary[0].stops) < 4
    # 顺延后 day_index 连续重排
    assert [dy.day_index for dy in out.itinerary] == list(range(1, len(out.itinerary) + 1))


# ─── narrate ───

def test_narrate_outputs_speech_and_card():
    trip = Trip(destination="杭州", days=2)
    d1 = Day(day_index=1, stops=[
        Stop(stop_id="s1", name="西湖", grounded=True,
             poi={"name": "西湖", "lat": 30.25, "lng": 120.15})])
    trip.itinerary = [d1]
    speech, card = pipeline.narrate(trip)
    assert "杭州" in speech and "西湖" in speech
    assert card["type"] == "trip_itinerary"
    assert card["itinerary"][0]["stops"][0]["name"] == "西湖"


# ── #3 天气联动 ──
from datetime import datetime as _dt
from agents.info.src.providers.base import ForecastDay as _FD


class _FakeWeather:
    def __init__(self, days): self._days = days
    async def forecast(self, city="", days=3, meta=None): return self._days


def test_start_offset():
    mon = _dt(2026, 7, 6)  # 周一
    assert pipeline._start_offset("明天去", mon) == 1
    assert pipeline._start_offset("后天", mon) == 2
    assert pipeline._start_offset("这周末去珠海玩两天", mon) == 5   # 周一→周六
    assert pipeline._start_offset("下周末去", mon) == 12
    assert pipeline._start_offset("周日去", mon) == 6
    assert pipeline._start_offset("去珠海玩两天", mon) == 0        # 无时间词默认今天


def test_weather_hint():
    h = pipeline._weather_hint([{"text": "晴", "temp_low": "24", "temp_high": "30"}, None])
    assert "第1天晴 24-30℃" in h and "室内" in h
    assert pipeline._weather_hint([None, None]) == ""


def test_plan_weather_align_and_out_of_window():
    fc = [_FD(date="2026-07-06", text_day="晴", temp_high="30", temp_low="24"),
          _FD(date="2026-07-07", text_day="阵雨", temp_high="27", temp_low="22")]
    # 明天 offset=1 → day1=forecast[1]（阵雨），day2=forecast[2]（超窗→None）
    w = asyncio.run(pipeline.plan_weather(_FakeWeather(fc), "杭州", "明天去杭州玩两天", 2, {}))
    assert w[0]["text"] == "阵雨" and w[0]["temp_high"] == "27"
    assert w[1] is None


def test_plan_weather_no_provider_or_error():
    assert asyncio.run(pipeline.plan_weather(None, "杭州", "", 2, {})) == [None, None]

    class _Boom:
        async def forecast(self, **k): raise RuntimeError("no key")
    assert asyncio.run(pipeline.plan_weather(_Boom(), "杭州", "明天", 2, {})) == [None, None]


def test_no_mock_fallback_field():
    """M0a 铁律③回归锁（navigation/charging 同款，trip-planner 曾漏网）：
    运行期 mock 回退已结构性根除——`_fallback` 字段不存在即无法悄悄复活。
    2026-07-25 badcase「红军长征路线图×4」正是假 POI 充数一族的产物形态。"""
    import inspect
    from agents.trip_planner.src import pipeline as pl
    import agents.trip_planner.src.agent as agent_mod
    text = inspect.getsource(agent_mod)
    assert "MockPOIProvider" not in text, "agent 不得再引用 MockPOIProvider"
    assert "self._fallback" not in text
    ptext = inspect.getsource(pl)
    assert "fallback.search" not in ptext, "pipeline 不得保留 provider 级 mock 回退"


# ─── G4 主题检索步 ───

def test_theme_pool_grounds_candidates_and_rejects_mismatch():
    """LLM 提议的候选逐个高德验证：接得到的入池，挂错名的非空结果被拒。"""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value='["河坊街", "假想仙境阁"]')
    prov = FakePOI(search_map={
        "杭州河坊街": [_poi("河坊街")],
        # 「假想仙境阁」搜出别的东西 → name_matches 拒
        "假想仙境阁": [_poi("某某大厦")],
        "杭州假想仙境阁": [_poi("某某大厦")],
    })
    pool, stats = asyncio.run(pipeline.build_theme_pool(llm, prov, "太平年", "杭州", {}))
    assert [p.name for p in pool] == ["河坊街"]
    # E3：接地命中率是**读数**（提议 2 / 接地 1），降级本身仍是设计
    assert stats == {"proposed": 2, "grounded": 1}


def test_theme_pool_llm_failure_degrades_to_empty():
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))
    pool, stats = asyncio.run(pipeline.build_theme_pool(llm, FakePOI(), "太平年", "杭州", {}))
    assert pool == [] and stats.get("llm_failed") is True


def test_theme_pool_bad_json_degrades_to_empty():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="这个主题我不了解")
    pool, stats = asyncio.run(pipeline.build_theme_pool(llm, FakePOI(), "冷门剧", "杭州", {}))
    # LLM 答了但一个名字都没给 → proposed=0（与「LLM 挂了」是两种状态，别混着读）
    assert pool == [] and stats == {"proposed": 0, "grounded": 0}


def test_theme_pool_city_prefix_first():
    """「{城市}{名}」优先——多义名（鼓楼）不带城市必然接错。"""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value='["鼓楼"]')
    prov = FakePOI(search_map={"杭州鼓楼": [_poi("鼓楼")],
                               "鼓楼": [_poi("北京鼓楼")]})
    pool, _ = asyncio.run(pipeline.build_theme_pool(llm, prov, "某剧", "杭州", {}))
    assert prov.calls[0] == "杭州鼓楼"
    assert [p.name for p in pool] == ["鼓楼"]


def test_theme_hint_and_narrate_theme():
    assert "《太平年》" in pipeline.theme_hint("太平年", ["河坊街"])
    assert pipeline.theme_hint("", []) == ""
    trip = Trip(destination="杭州", days=1, theme="太平年", itinerary=[
        Day(day_index=1, stops=[Stop(stop_id="s1", name="河坊街",
                                     poi={"lat": 30.2, "lng": 120.1},
                                     grounded=True)])])
    speech, card = pipeline.narrate(trip)
    assert "按《太平年》主题" in speech
    assert card["theme"] == "太平年"


# ─── G9 多城市 ───

def test_multi_city_skeleton_parses_and_converges_city():
    """骨架 day 的 city 收敛到城市集内；列表外城市名置空由均摊兜底。"""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=(
        '{"days":['
        '{"day_index":1,"city":"杭州","stops":[{"name":"西湖","type":"attraction"}]},'
        '{"day_index":2,"city":"火星","stops":[{"name":"拙政园","type":"attraction"}]}]}'))
    sk = asyncio.run(pipeline.propose(
        llm, "杭州、苏州", "2", "", ["西湖", "拙政园"],
        cities=["杭州", "苏州"],
        pool_by_city={"杭州": [_poi("西湖")], "苏州": [_poi("拙政园")]}))
    assert sk["days"][0]["city"] == "杭州"
    assert sk["days"][1]["city"] == "苏州"      # 「火星」被拒 → 按序均摊兜底


def test_multi_city_fallback_splits_days_by_city():
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))
    sk = asyncio.run(pipeline.propose(
        llm, "杭州、苏州", "4", "", ["西湖", "拙政园"],
        cities=["杭州", "苏州"],
        pool_by_city={"杭州": [_poi("西湖"), _poi("灵隐寺")],
                      "苏州": [_poi("拙政园"), _poi("虎丘")]}))
    assert len(sk["days"]) == 4
    assert [d["city"] for d in sk["days"]] == ["杭州", "杭州", "苏州", "苏州"]
    assert all(d["stops"] for d in sk["days"])


def test_multi_city_ground_uses_city_pool():
    """接地按 day.city 的城池取坐标（同名 POI 两城并存时不拿错城的）。"""
    hz = _poi("鼓楼", lat=30.25, lng=120.15)
    sz = _poi("鼓楼", lat=31.30, lng=120.60)
    sk = {"days": [
        {"day_index": 1, "city": "苏州", "stops": [{"name": "鼓楼", "type": "attraction"}]}]}
    trip = asyncio.run(pipeline.ground(
        FakePOI(), sk, [hz, sz], {}, dest="杭州、苏州",
        cities=["杭州", "苏州"],
        pool_by_city={"杭州": [hz], "苏州": [sz]}))
    stop = trip.itinerary[0].stops[0]
    assert stop.grounded and stop.poi["lat"] == 31.30
    assert trip.itinerary[0].city == "苏州"
    assert trip.cities == ["杭州", "苏州"]


def test_solve_builds_cross_day_leg_and_soc_continuity():
    """跨天衔接 leg：前一天末站→当天首站建段，SoC 递推跨天连续。"""
    d1 = Day(day_index=1, stops=[
        Stop(stop_id="s1", name="西湖", grounded=True,
             poi={"lat": 30.25, "lng": 120.15})])
    d2 = Day(day_index=2, stops=[
        Stop(stop_id="s2", name="拙政园", grounded=True,
             poi={"lat": 31.32, "lng": 120.63})])
    trip = Trip(destination="杭州、苏州", days=2, itinerary=[d1, d2])
    prov = FakePOI(route={"distance_km": 160.0, "duration_min": 120, "points": []})
    solved = asyncio.run(pipeline.solve(prov, trip, 80.0, {}))
    legs2 = solved.itinerary[1].legs
    assert legs2 and legs2[0].from_stop_id == "s1" and legs2[0].to_stop_id == "s2"
    # SoC 连续：160km/500km 满续航 → 掉 32 个百分点
    assert legs2[0].soc_before == 80
    assert legs2[0].soc_after == 48


def test_narrate_marks_city_per_day():
    trip = Trip(destination="杭州、苏州", days=2, cities=["杭州", "苏州"], itinerary=[
        Day(day_index=1, city="杭州", stops=[
            Stop(stop_id="s1", name="西湖", grounded=True,
                 poi={"lat": 30.2, "lng": 120.1})]),
        Day(day_index=2, city="苏州", stops=[
            Stop(stop_id="s2", name="拙政园", grounded=True,
                 poi={"lat": 31.3, "lng": 120.6})])])
    speech, card = pipeline.narrate(trip)
    assert "第1天（杭州）" in speech and "第2天（苏州）" in speech
    assert card["cities"] == ["杭州", "苏州"]


def test_solve_overflow_day_inherits_city():
    """顺延新建的天继承前一天的 city（真栈首验：「玩三天」顺延成 4 天第 4 天无城标）。"""
    stops = [Stop(stop_id=f"s{i}", name=f"点{i}", grounded=True, dwell_min=300,
                  poi={"lat": 31.30 + i * 0.01, "lng": 120.60}) for i in range(3)]
    trip = Trip(destination="杭州、苏州", days=1, cities=["杭州", "苏州"],
                itinerary=[Day(day_index=1, city="苏州", stops=stops)])
    prov = FakePOI(route={"distance_km": 5.0, "duration_min": 20, "points": []})
    solved = asyncio.run(pipeline.solve(prov, trip, 80.0, {}))
    assert len(solved.itinerary) >= 2
    assert solved.itinerary[1].city == "苏州"


# ─── P2 用户点名 POI 入池（EVA 遗留卡）───

def test_must_visit_direct_ground_and_city_assignment():
    """直搜接地 + 按坐标就近归城（东方之门→苏州、灵山大佛→无锡）。"""
    llm = AsyncMock()
    prov = FakePOI(search_map={
        "东方之门": [_poi("东方之门", lat=31.32, lng=120.68)],
        "灵山大佛": [_poi("灵山大佛", lat=31.43, lng=120.09)]})
    pool_by_city = {
        "苏州": [_poi("金鸡湖", lat=31.31, lng=120.71)],
        "无锡": [_poi("鼋头渚", lat=31.53, lng=120.22)]}
    pairs = asyncio.run(pipeline.ground_must_visit(
        llm, prov, ["东方之门", "灵山大佛"], ["苏州", "无锡"], "苏州、无锡", {},
        pool_by_city=pool_by_city))
    assert [(c, p.name) for c, p in pairs] == [
        ("苏州", "东方之门"), ("无锡", "灵山大佛")]
    llm.complete.assert_not_called()          # 直搜命中不烧 LLM


def test_must_visit_nickname_via_landmark_llm():
    """俗称（大秋裤）直搜失败 → landmark_candidates 解析官方名再搜。"""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value='["东方之门"]')
    prov = FakePOI(search_map={
        "东方之门": [_poi("东方之门", lat=31.32, lng=120.68)]})
    pairs = asyncio.run(pipeline.ground_must_visit(
        llm, prov, ["大秋裤"], ["苏州"], "苏州", {},
        pool_by_city={"苏州": [_poi("金鸡湖", lat=31.31, lng=120.71)]}))
    assert [(c, p.name) for c, p in pairs] == [("苏州", "东方之门")]


def test_must_visit_ungroundable_dropped():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="[]")
    pairs = asyncio.run(pipeline.ground_must_visit(
        llm, FakePOI(), ["不存在的仙境"], ["苏州"], "苏州", {},
        pool_by_city={}))
    assert pairs == []                        # 接不到丢弃，不臆造


def test_must_visit_hint_and_post_insert():
    """骨架漏排的必去点确定性补插进归属城首日；已在行程的不重复插。"""
    dp = _poi("东方之门", lat=31.32, lng=120.68)
    pairs = [("苏州", dp), ("无锡", _poi("灵山大佛", lat=31.43, lng=120.09))]
    assert "东方之门（苏州）" in pipeline.must_visit_hint(pairs)

    trip = Trip(destination="苏州、无锡", days=2, cities=["苏州", "无锡"], itinerary=[
        Day(day_index=1, city="苏州", stops=[
            Stop(stop_id="s1", name="东方之门", grounded=True,
                 poi={"lat": 31.32, "lng": 120.68})]),
        Day(day_index=2, city="无锡", stops=[
            Stop(stop_id="s2", name="鼋头渚", grounded=True,
                 poi={"lat": 31.53, "lng": 120.22})])])
    pipeline.ensure_must_visit_in_itinerary(trip, pairs)
    d1_names = [s.name for s in trip.itinerary[0].stops]
    d2_names = [s.name for s in trip.itinerary[1].stops]
    assert d1_names == ["东方之门"]           # 已在行程 → 不重复
    assert "灵山大佛" in d2_names             # 漏排 → 补进无锡那天
    assert trip.itinerary[1].stops[-1].source == "user"


# ── E3：多城行程的确定性归城校正 ──────────────────────────────────
# 坐标取真实量级：苏州(31.30,120.60) / 南京(32.06,118.79) 相距约 170km；
# 苏州 vs 无锡(31.49,120.31) 约 33km（交界处样本，用来验「差得不够明显就不搬」）。
_SUZHOU, _NANJING, _WUXI = (31.30, 120.60), (32.06, 118.79), (31.49, 120.31)


def _stop(name, latlng):
    return Stop(stop_id=name, name=name, grounded=True,
                poi={"name": name, "lat": latlng[0], "lng": latlng[1]})


def _multi_city_trip(days):
    t = Trip(destination="苏州、南京", days=len(days), cities=["苏州", "南京"])
    t.itinerary = days
    return t


def _pools():
    return {"苏州": [_poi("苏州园林", *_SUZHOU)], "南京": [_poi("夫子庙", *_NANJING)]}


def test_stop_placed_in_the_wrong_city_day_is_moved_back():
    """P2 的补插只管「漏排」——这条管「排错天」：东方之门排进了南京那天。"""
    d1 = Day(day_index=1, city="苏州", stops=[_stop("苏州园林", _SUZHOU)])
    d2 = Day(day_index=2, city="南京",
             stops=[_stop("夫子庙", _NANJING), _stop("东方之门", _SUZHOU)])
    trip = _multi_city_trip([d1, d2])

    moved = pipeline.correct_stop_cities(trip, _pools())

    assert [m["name"] for m in moved] == ["东方之门"]
    assert [s.name for s in d1.stops] == ["苏州园林", "东方之门"]
    assert [s.name for s in d2.stops] == ["夫子庙"]


def test_border_city_stop_is_not_shuffled():
    """交界处（苏州/无锡 33km）差得不够明显 → 不搬。来回搬比不搬更糟。"""
    d1 = Day(day_index=1, city="苏州", stops=[_stop("太湖边某点", _WUXI)])
    trip = Trip(destination="苏州、无锡", days=1, cities=["苏州", "无锡"])
    trip.itinerary = [d1]

    moved = pipeline.correct_stop_cities(
        trip, {"苏州": [_poi("苏州园林", *_SUZHOU)], "无锡": [_poi("鼋头渚", *_WUXI)]})

    assert moved == [] and [s.name for s in d1.stops] == ["太湖边某点"]


def test_single_city_trip_is_untouched():
    """单城行程零影响（cities <2 直接返回）。"""
    d1 = Day(day_index=1, stops=[_stop("西湖", (30.24, 120.15))])
    trip = Trip(destination="杭州", days=1)
    trip.itinerary = [d1]

    assert pipeline.correct_stop_cities(trip, {"杭州": [_poi("西湖", 30.24, 120.15)]}) == []
    assert [s.name for s in d1.stops] == ["西湖"]


def test_ungrounded_stop_has_no_coordinates_to_judge_by():
    """未接地的 stop 没有坐标——不猜、不动（同 ground 的「不臆造」纪律）。"""
    d2 = Day(day_index=2, city="南京", stops=[Stop(stop_id="x", name="某个没接到的点")])
    trip = _multi_city_trip([Day(day_index=1, city="苏州", stops=[]), d2])

    assert pipeline.correct_stop_cities(trip, _pools()) == []
    assert [s.name for s in d2.stops] == ["某个没接到的点"]


def test_correction_needs_two_city_centers():
    """池里算不出两个质心时不做判定（宁可不搬也不按半个坐标系搬）。"""
    d2 = Day(day_index=2, city="南京", stops=[_stop("东方之门", _SUZHOU)])
    trip = _multi_city_trip([Day(day_index=1, city="苏州", stops=[]), d2])

    assert pipeline.correct_stop_cities(trip, {"苏州": [_poi("苏州园林", *_SUZHOU)]}) == []


def test_moved_stop_lands_on_the_first_day_of_its_city():
    """搬到归属城的**首日**（与补插同一落点规则，两条纪律行为一致）。"""
    d1 = Day(day_index=1, city="苏州", stops=[])
    d2 = Day(day_index=2, city="苏州", stops=[])
    d3 = Day(day_index=3, city="南京", stops=[_stop("平江路", _SUZHOU)])
    trip = _multi_city_trip([d1, d2, d3])

    pipeline.correct_stop_cities(trip, _pools())

    assert [s.name for s in d1.stops] == ["平江路"] and d2.stops == [] and d3.stops == []


def test_correct_stop_cities_reports_distances_for_observability():
    """搬动记录带两侧距离——「搬了什么、凭什么搬」要能复核，不能只留一个结果。"""
    d2 = Day(day_index=2, city="南京", stops=[_stop("东方之门", _SUZHOU)])
    trip = _multi_city_trip([Day(day_index=1, city="苏州", stops=[]), d2])

    moved = pipeline.correct_stop_cities(trip, _pools())

    assert moved[0]["from"] == "南京" and moved[0]["to"] == "苏州"
    assert moved[0]["km_from"] > moved[0]["km_to"] * 1.5


def test_reflow_never_pushes_stops_into_another_citys_day():
    """E3：溢出的停靠点不得挤进**下一座城**那天——真栈六城实测，无锡那天溢出的
    三个点被 insert(0) 进了南京那天，归城校正刚归好的城当场又乱。"""
    trip = Trip(destination="无锡、南京", days=2, cities=["无锡", "南京"])
    wuxi = Day(day_index=1, city="无锡", stops=[
        Stop(stop_id=f"w{i}", name=f"无锡{i}", grounded=True,
             poi={"name": f"无锡{i}", "lat": 31.49 + i * 0.01, "lng": 120.31},
             dwell_min=120) for i in range(4)])
    nanjing = Day(day_index=2, city="南京", stops=[
        Stop(stop_id="n1", name="夫子庙", grounded=True,
             poi={"name": "夫子庙", "lat": 32.06, "lng": 118.79}, dwell_min=120)])
    trip.itinerary = [wuxi, nanjing]
    prov = FakePOI(route={"distance_km": 5.0, "duration_min": 60, "points": []})

    out = asyncio.run(pipeline.solve(prov, trip, 80, {},
                                     full_range_km=500, day_cap_min=300))

    for day in out.itinerary:
        for s in day.stops:
            assert s.name.startswith("无锡") == (day.city == "无锡"), \
                f"{s.name} 落在 {day.city} 那天"
    assert [d.city for d in out.itinerary][-1] == "南京"      # 南京仍在最后
    assert [d.day_index for d in out.itinerary] == list(range(1, len(out.itinerary) + 1))


def test_reflow_behaviour_unchanged_for_single_city():
    """单城行程 city 全空 → 判据短路，顺延行为逐字照旧（新建天追加在末尾）。"""
    trip = Trip(destination="X", days=1)
    trip.itinerary = [Day(day_index=1, stops=[
        Stop(stop_id=f"s{i}", name=f"P{i}", grounded=True,
             poi={"name": f"P{i}", "lat": 30 + i * 0.01, "lng": 120}, dwell_min=120)
        for i in range(4)])]
    prov = FakePOI(route={"distance_km": 5.0, "duration_min": 60, "points": []})

    out = asyncio.run(pipeline.solve(prov, trip, 80, {},
                                     full_range_km=500, day_cap_min=300))

    assert len(out.itinerary) >= 2 and all(d.city == "" for d in out.itinerary)
    assert [s.name for d in out.itinerary for s in d.stops] == ["P0", "P1", "P2", "P3"]

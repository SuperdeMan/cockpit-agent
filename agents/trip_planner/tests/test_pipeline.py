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
    pool = asyncio.run(pipeline.build_theme_pool(llm, prov, "太平年", "杭州", {}))
    assert [p.name for p in pool] == ["河坊街"]


def test_theme_pool_llm_failure_degrades_to_empty():
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))
    pool = asyncio.run(pipeline.build_theme_pool(llm, FakePOI(), "太平年", "杭州", {}))
    assert pool == []


def test_theme_pool_bad_json_degrades_to_empty():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="这个主题我不了解")
    pool = asyncio.run(pipeline.build_theme_pool(llm, FakePOI(), "冷门剧", "杭州", {}))
    assert pool == []


def test_theme_pool_city_prefix_first():
    """「{城市}{名}」优先——多义名（鼓楼）不带城市必然接错。"""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value='["鼓楼"]')
    prov = FakePOI(search_map={"杭州鼓楼": [_poi("鼓楼")],
                               "鼓楼": [_poi("北京鼓楼")]})
    pool = asyncio.run(pipeline.build_theme_pool(llm, prov, "某剧", "杭州", {}))
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

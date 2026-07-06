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
    trip = asyncio.run(pipeline.ground(prov, prov, sk, pool, {}, dest="杭州"))
    s = trip.itinerary[0].stops[0]
    assert s.grounded and s.poi["lat"] == 30.25
    assert prov.calls == []      # 池内命中，零搜索


def test_ground_rejects_mismatched_name():
    """搜索返回「挂错名的非空结果」（高德对俗称返回邻近无关 POI）→ 该 stop 不接地。"""
    sk = {"days": [{"day_index": 1,
                    "stops": [{"name": "天坛公园", "type": "attraction"}]}]}
    prov = FakePOI(default=lambda kw: [_poi("V东滨店", lat=22.5, lng=114.0)])
    trip = asyncio.run(pipeline.ground(prov, prov, sk, [], {}, dest="北京"))
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
    out = asyncio.run(pipeline.solve(prov, prov, trip, 50, {},
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
    out = asyncio.run(pipeline.solve(prov, prov, trip, 80, {}, full_range_km=500))
    assert out.itinerary[0].legs[0].charging_stops == []


def test_solve_reflow_day_cap():
    """单日（驾驶+游览）超上限 → 尾部 stop 顺延次日。"""
    trip = Trip(destination="X", days=1)
    d = Day(day_index=1, stops=[
        Stop(stop_id=f"s{i}", name=f"P{i}", grounded=True,
             poi={"name": f"P{i}", "lat": 30 + i * 0.01, "lng": 120}, dwell_min=120)
        for i in range(4)])
    trip.itinerary = [d]
    prov = FakePOI(route={"distance_km": 5.0, "duration_min": 60, "points": []})
    out = asyncio.run(pipeline.solve(prov, prov, trip, 80, {},
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

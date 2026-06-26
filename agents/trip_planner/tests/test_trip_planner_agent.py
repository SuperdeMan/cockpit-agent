"""trip-planner 契约测试（P0 重构后）。

新返回结构：trip.plan 走流水线产出结构化 `trip_itinerary` 卡 + NEED_CONFIRM；
确认轮收尾出第一站 poi_list；修改只动指定天。用 FakePOI + AsyncMock llm 驱动，
持久化经 make_context 的 context_values 注入（profile.trip_active）。
"""
import asyncio
import json
from unittest.mock import AsyncMock

from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.navigation.src.providers.base import POI
from agents.trip_planner.src.agent import TripPlannerAgent
from agents.trip_planner.src.models import Trip, Day, Stop


def _poi(name, lat=30.0, lng=120.0, rating=4.5) -> POI:
    return POI(id=f"id_{name}", name=name, address=f"{name}地址",
               lat=lat, lng=lng, rating=rating)


class FakePOI:
    def __init__(self, search_map=None, route=None, default=None):
        self.search_map = search_map or {}
        self.route = route or {"distance_km": 5.0, "duration_min": 12, "points": []}
        self.default = default

    async def search(self, keyword, near=None, category="", rating_min=0,
                     limit=5, page=1, meta=None):
        for k, v in self.search_map.items():
            if k in keyword:
                return v[:limit]
        return (self.default(keyword)[:limit] if self.default else [])

    async def get_route(self, origin, destination, meta=None,
                        with_polyline=False, waypoints=None):
        return self.route


def _persisted_ctx(trip: Trip):
    return make_context(context_values={
        "profile.trip_active": json.dumps(trip.to_dict(), ensure_ascii=False)})


# ─── 用例 ───

def test_missing_destination_returns_need_slot():
    res = asyncio.run(run_handle(
        TripPlannerAgent(), "trip.plan", slots={}, raw_text="帮我规划行程"))
    assert res.status == "need_slot"
    assert "目的地" in res.speech or "去哪里" in res.speech


def test_plan_happy_path_returns_trip_itinerary():
    """全槽位 happy path：流水线产出结构化 trip_itinerary 卡 + NEED_CONFIRM，景点接地真实 POI。"""
    agent = TripPlannerAgent()
    fake = FakePOI(search_map={
        "景点": [_poi("西湖", 30.25, 120.15), _poi("灵隐寺", 30.24, 120.10),
               _poi("宋城", 30.18, 120.10)],
        "美食": [_poi("楼外楼", 30.25, 120.14)],
        "充电站": [_poi("充电A", 30.2, 120.1)]})
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(return_value=(
        '{"days":[{"day_index":1,"theme":"湖光山色","stops":'
        '[{"name":"西湖","type":"attraction"},{"name":"灵隐寺","type":"attraction"}]}]}'))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={"destination": "杭州", "days": "1"},
        raw_text="杭州一日游"))
    assert res.status == "need_confirm"
    assert res.ui_card and res.ui_card["type"] == "trip_itinerary"
    assert "杭州" in res.speech
    stops = res.ui_card["itinerary"][0]["stops"]
    assert any(s["name"] == "西湖" and s["grounded"] for s in stops)


def test_plan_llm_failure_falls_back_to_deterministic():
    """propose LLM 失败 → 确定性兜底分配池内景点，仍出 trip_itinerary + NEED_CONFIRM。"""
    agent = TripPlannerAgent()
    fake = FakePOI(search_map={"景点": [_poi("西湖"), _poi("灵隐寺")]})
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={"destination": "杭州", "days": "1"},
        raw_text="杭州一日游"))
    assert res.status == "need_confirm"
    assert res.ui_card["type"] == "trip_itinerary"


def test_confirmed_finalizes_with_first_stop_poi_list():
    """确认轮（meta.confirmed=true）收尾返回 OK，出第一站 poi_list，绝不再调 LLM/再 NEED_CONFIRM。"""
    agent = TripPlannerAgent()
    trip = Trip(destination="杭州", days=2)
    trip.itinerary = [Day(day_index=1, stops=[Stop(
        stop_id="s1", name="西湖", grounded=True,
        poi={"id": "x", "name": "西湖", "address": "西湖区", "lat": 30.25,
             "lng": 120.15, "rating": 4.7})])]
    ctx = _persisted_ctx(trip)
    agent.poi = FakePOI(default=lambda kw: [_poi(f"{kw}东门", 30.25, 120.15),
                                            _poi(f"{kw}南门", 30.24, 120.15)])
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={"destination": "杭州", "days": "2"},
        raw_text="确认", meta={"confirmed": "true"}, ctx=ctx))
    assert res.status == "ok"
    assert res.status != "need_confirm"
    assert "已确认" in res.speech and "杭州" in res.speech
    assert res.ui_card and res.ui_card["type"] == "poi_list"
    assert res.ui_card.get("purpose") is None      # plain poi_list → HMI「第N个」即导航
    assert any("西湖" in i["name"] for i in res.ui_card["items"])


def test_modify_only_changes_target_day():
    """改第三天：第一、二天结构化原样保留（不漂移），仅第三天换内容。"""
    agent = TripPlannerAgent()
    trip = Trip(destination="北京", days=3)
    for i, nm in enumerate(["颐和园", "故宫", "奥林匹克公园"], start=1):
        trip.itinerary.append(Day(day_index=i, stops=[Stop(
            stop_id=f"s{i}", name=nm, grounded=True,
            poi={"id": nm, "name": nm, "address": "addr",
                 "lat": 39.9 + i * 0.01, "lng": 116.3, "rating": 4.6})]))
    ctx = _persisted_ctx(trip)
    fake = FakePOI(search_map={"景点": [_poi("798艺术区", 39.98, 116.49)],
                               "充电站": [_poi("充电A")]})
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(return_value=(
        '{"days":[{"day_index":1,"stops":[{"name":"798艺术区","type":"attraction"}]}]}'))

    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第三天换成798"},
        raw_text="第三天换成798", ctx=ctx))
    assert res.status == "need_confirm"
    days = res.ui_card["itinerary"]
    assert days[0]["stops"][0]["name"] == "颐和园"   # 第一天不变
    assert days[1]["stops"][0]["name"] == "故宫"     # 第二天不变
    assert days[2]["stops"][0]["name"] == "798艺术区"  # 第三天改了


def test_modify_confirmed_resume_finalizes():
    """改行程确认轮同样收尾、不再 NEED_CONFIRM。"""
    agent = TripPlannerAgent()
    trip = Trip(destination="北京", days=2)
    trip.itinerary = [Day(day_index=1, stops=[Stop(
        stop_id="s1", name="故宫", grounded=True,
        poi={"name": "故宫", "address": "a", "lat": 39.9, "lng": 116.3})])]
    ctx = _persisted_ctx(trip)
    agent.poi = FakePOI(default=lambda kw: [_poi(f"{kw}入口")])
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第二天换成颐和园"},
        raw_text="确认", meta={"confirmed": "true"}, ctx=ctx))
    assert res.status == "ok"
    assert "已确认" in res.speech


def test_modify_without_prior_trip_asks_to_plan():
    """无正在规划的行程时改行程 → 引导先规划（NEED_SLOT），不瞎编。"""
    agent = TripPlannerAgent()
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第二天换成宋城"},
        raw_text="第二天换成宋城"))
    assert res.status == "need_slot"


def test_manifest_consistency():
    assert assert_manifest_consistent(TripPlannerAgent()) is True

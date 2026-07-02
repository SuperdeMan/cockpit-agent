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


def test_plan_extracts_dest_from_raw_text_when_slots_empty():
    """R2.1：route_hint(append) 注入的 trip.plan 步 slots 为空——Agent 从 raw_text 抽取
    目的地/天数/偏好（extract.py），照常走流水线出 trip_itinerary，不再 NEED_SLOT。"""
    agent = TripPlannerAgent()
    fake = FakePOI(search_map={
        "景点": [_poi("西湖", 30.25, 120.15), _poi("灵隐寺", 30.24, 120.10)]})
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(return_value=(
        '{"days":[{"day_index":1,"theme":"湖光","stops":[{"name":"西湖","type":"attraction"}]}]}'))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={}, raw_text="周末去杭州两天，带老人，不要太累"))
    assert res.status == "need_confirm"
    assert res.ui_card and res.ui_card["type"] == "trip_itinerary"
    assert "杭州" in res.speech


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


# ─── P1: trip.navigate 每stop可导航 + 下一站 ───

def _trip_2days() -> Trip:
    trip = Trip(destination="杭州", days=2)
    trip.itinerary = [
        Day(day_index=1, stops=[Stop(stop_id="s1", name="西湖", grounded=True,
            poi={"name": "西湖", "address": "西湖区", "lat": 30.25, "lng": 120.15})]),
        Day(day_index=2, stops=[Stop(stop_id="s2", name="灵隐寺", grounded=True,
            poi={"name": "灵隐寺", "address": "灵隐", "lat": 30.24, "lng": 120.10})]),
    ]
    return trip


def test_navigate_to_day_stop_by_name():
    """『导航去第二天的灵隐寺』→ 取行程里那一站发 navigate 动作。"""
    agent = TripPlannerAgent()
    ctx = _persisted_ctx(_trip_2days())
    res = asyncio.run(run_handle(
        agent, "trip.navigate", slots={}, raw_text="导航去第二天的灵隐寺", ctx=ctx))
    assert res.status == "ok"
    navs = [a for a in res.actions if a["type"] == "navigate"]
    assert navs and navs[0]["payload"]["destination"] == "灵隐寺"
    assert navs[0]["payload"]["lat"] == 30.24


def test_navigate_next_from_start():
    """『下一站』初始游标 → 行程第一站。"""
    agent = TripPlannerAgent()
    res = asyncio.run(run_handle(
        agent, "trip.navigate", slots={}, raw_text="下一站", ctx=_persisted_ctx(_trip_2days())))
    navs = [a for a in res.actions if a["type"] == "navigate"]
    assert navs and navs[0]["payload"]["destination"] == "西湖"


def test_navigate_next_advances_by_cursor():
    """游标在第一天第一站 → 『下一站』给第二天的灵隐寺。"""
    agent = TripPlannerAgent()
    trip = _trip_2days()
    trip.cursor = {"day_index": 1, "stop_index": 0}
    res = asyncio.run(run_handle(
        agent, "trip.navigate", slots={}, raw_text="下一站", ctx=_persisted_ctx(trip)))
    navs = [a for a in res.actions if a["type"] == "navigate"]
    assert navs and navs[0]["payload"]["destination"] == "灵隐寺"


def test_navigate_next_at_end():
    """游标在末站 → 『下一站』提示没有下一站、不发导航。"""
    agent = TripPlannerAgent()
    trip = _trip_2days()
    trip.cursor = {"day_index": 2, "stop_index": 0}
    res = asyncio.run(run_handle(
        agent, "trip.navigate", slots={}, raw_text="下一站", ctx=_persisted_ctx(trip)))
    assert "最后一站" in res.speech
    assert not [a for a in res.actions if a["type"] == "navigate"]


def test_navigate_without_trip_asks_to_plan():
    agent = TripPlannerAgent()
    res = asyncio.run(run_handle(
        agent, "trip.navigate", slots={}, raw_text="下一站"))
    assert res.status == "need_slot"


# ─── P1: trip.modify 结构化 edit-op（加/删具体停靠点）───

def _trip_3days_beijing() -> Trip:
    trip = Trip(destination="北京", days=3)
    for i, nm in enumerate(["颐和园", "故宫", "奥林匹克公园"], start=1):
        trip.itinerary.append(Day(day_index=i, stops=[Stop(
            stop_id=f"s{i}", name=nm, grounded=True,
            poi={"name": nm, "address": "addr", "lat": 39.9 + i * 0.01, "lng": 116.3})]))
    return trip


def test_modify_remove_stop_keeps_others():
    """『删掉第三天的奥林匹克公园』→ 结构化删除该站、不调 LLM、其余保留。"""
    agent = TripPlannerAgent()
    fake = FakePOI()
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(side_effect=AssertionError("结构化删除不应调 LLM"))
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "删掉第三天的奥林匹克公园"},
        raw_text="删掉第三天的奥林匹克公园", ctx=_persisted_ctx(_trip_3days_beijing())))
    assert res.status == "need_confirm"
    names = [s["name"] for d in res.ui_card["itinerary"] for s in d["stops"]]
    assert "奥林匹克公园" not in names
    assert "颐和园" in names and "故宫" in names


def test_modify_add_stop_grounds_and_appends():
    """『加一个宋城』→ 接地真实 POI 并加入行程、不整程重规划、原有保留。"""
    agent = TripPlannerAgent()
    fake = FakePOI(default=lambda kw: [_poi("宋城", 30.18, 120.10)])
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(side_effect=AssertionError("结构化加站不应调 LLM"))
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "加一个宋城"},
        raw_text="加一个宋城", ctx=_persisted_ctx(_trip_2days())))
    assert res.status == "need_confirm"
    names = [s["name"] for d in res.ui_card["itinerary"] for s in d["stops"]]
    assert "宋城" in names
    assert "西湖" in names and "灵隐寺" in names


# ─── P2: trip.status / trip.reschedule（在途编排）───

def test_status_reports_progress():
    """『行程到哪了』→ 报当前站/下一站/剩余站数（只读，不改行程）。"""
    agent = TripPlannerAgent()
    trip = _trip_2days()
    trip.cursor = {"day_index": 1, "stop_index": 0}     # 已到第一站
    res = asyncio.run(run_handle(
        agent, "trip.status", slots={}, raw_text="行程到哪了", ctx=_persisted_ctx(trip)))
    assert res.status == "ok"
    assert "灵隐寺" in res.speech and "1站" in res.speech       # 下一站 + 还剩1站
    assert res.ui_card["type"] == "trip_itinerary"


def test_status_without_trip():
    agent = TripPlannerAgent()
    res = asyncio.run(run_handle(agent, "trip.status", slots={}, raw_text="行程到哪了"))
    assert res.status == "ok"
    assert "还没有规划" in res.speech


def test_reschedule_trims_trailing_stops():
    """『时间不够了』→ 每个剩余天砍掉尾部一站，NEED_CONFIRM。"""
    agent = TripPlannerAgent()
    fake = FakePOI()
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(side_effect=AssertionError("精简不应调 LLM"))
    trip = Trip(destination="北京", days=2)
    trip.itinerary = [
        Day(day_index=1, stops=[Stop(stop_id=f"a{i}", name=n, grounded=True,
            poi={"name": n, "lat": 39.9 + i * 0.01, "lng": 116.3})
            for i, n in enumerate(["颐和园", "故宫", "天坛"])]),
        Day(day_index=2, stops=[Stop(stop_id=f"b{i}", name=n, grounded=True,
            poi={"name": n, "lat": 40.0 + i * 0.01, "lng": 116.4})
            for i, n in enumerate(["长城", "明十三陵"])]),
    ]
    res = asyncio.run(run_handle(
        agent, "trip.reschedule", slots={"hint": "时间不够了"},
        raw_text="时间不够了", ctx=_persisted_ctx(trip)))
    assert res.status == "need_confirm"
    names = [s["name"] for d in res.ui_card["itinerary"] for s in d["stops"]]
    assert "天坛" not in names and "明十三陵" not in names      # 各天尾站被砍
    assert "颐和园" in names and "长城" in names


def test_reschedule_early_return_drops_last_day():
    """『想提前回家』→ 删掉最后一天。"""
    agent = TripPlannerAgent()
    fake = FakePOI()
    agent.poi = fake
    agent._fallback = fake
    res = asyncio.run(run_handle(
        agent, "trip.reschedule", slots={"hint": "想提前回家"},
        raw_text="想提前回家", ctx=_persisted_ctx(_trip_3days_beijing())))
    assert res.status == "need_confirm"
    assert len(res.ui_card["itinerary"]) == 2                  # 三天 → 两天


def test_modify_day_dedup_excludes_other_days():
    """改某天时排除其它天已用景点：LLM 想把第二天又选成第一天的西湖 → 被去重排除。"""
    agent = TripPlannerAgent()
    trip = Trip(destination="杭州", days=2)
    trip.itinerary = [
        Day(day_index=1, stops=[Stop(stop_id="s1", name="西湖", grounded=True,
            poi={"name": "西湖", "lat": 30.25, "lng": 120.15})]),
        Day(day_index=2, stops=[Stop(stop_id="s2", name="灵隐寺", grounded=True,
            poi={"name": "灵隐寺", "lat": 30.24, "lng": 120.10})]),
    ]
    fake = FakePOI(search_map={"景点": [
        _poi("西湖", 30.25, 120.15), _poi("灵隐寺", 30.24, 120.10), _poi("宋城", 30.18, 120.10)]})
    agent.poi = fake
    agent._fallback = fake
    # LLM 偏要选第一天已有的「西湖」，应被跨天去重挡掉
    agent.llm.complete = AsyncMock(return_value=(
        '{"days":[{"day_index":1,"stops":[{"name":"西湖","type":"attraction"}]}]}'))
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第二天换一个"},
        raw_text="第二天换一个", ctx=_persisted_ctx(trip)))
    assert res.status == "need_confirm"
    days = res.ui_card["itinerary"]
    assert days[0]["stops"][0]["name"] == "西湖"               # 第一天不变
    day2_names = [s["name"] for s in days[1]["stops"]]
    assert "西湖" not in day2_names                            # 第二天不再撞第一天的西湖


def test_modify_replace_specific_stop_changes_it():
    """『第一天第二站调整下』→ 换掉那个具体停靠点（挑池里没用过的），根治整天重规划又挑回原样的 no-op。"""
    agent = TripPlannerAgent()
    trip = Trip(destination="杭州", days=1)
    trip.itinerary = [Day(day_index=1, stops=[
        Stop(stop_id="s1", name="西湖", grounded=True,
             poi={"name": "西湖", "lat": 30.25, "lng": 120.15}),
        Stop(stop_id="s2", name="灵隐寺", grounded=True,
             poi={"name": "灵隐寺", "lat": 30.24, "lng": 120.10})])]
    fake = FakePOI(search_map={"景点": [
        _poi("西湖", 30.25, 120.15), _poi("灵隐寺", 30.24, 120.10),
        _poi("宋城", 30.18, 120.10)]})
    agent.poi = fake
    agent._fallback = fake
    agent.llm.complete = AsyncMock(side_effect=AssertionError("替换具体站不应整天重规划调 LLM"))
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第一天第二站调整下"},
        raw_text="第一天第二站调整下", ctx=_persisted_ctx(trip)))
    assert res.status == "need_confirm"
    stops = [s["name"] for s in res.ui_card["itinerary"][0]["stops"]]
    assert stops[0] == "西湖"                       # 第一站不变
    assert stops[1] != "灵隐寺" and stops[1] == "宋城"  # 第二站换成池里没用过的


def test_manifest_consistency():
    assert assert_manifest_consistent(TripPlannerAgent()) is True

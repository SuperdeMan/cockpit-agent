"""road-safety Agent 契约测试。

覆盖：safety.driving_advice / safety.weather_alert / safety.road_condition。
验证 NEED_SLOT、协作降级、只建议不控车。
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from runtime.clock import epoch_at
from agents._sdk import AgentResult
from agents._sdk.testing import make_context, run_handle
from agents.road_safety.src.agent import RoadSafetyAgent


def test_driving_advice_without_destination_gives_general_advice():
    """safety.driving_advice 无目的地 → 一般性出行建议，不再反问目的地
    （badcase 11db5215：多步 plan 里 NEED_SLOT 追问会吞掉并行天气步的答案）。"""
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(
        status="ok", speech="深圳市南山区当前小雨，气温33℃。"))
    ctx = make_context()
    res = asyncio.run(run_handle(
        agent, "safety.driving_advice",
        slots={}, raw_text="今天天气怎么样，适合出行吗", ctx=ctx))
    assert res.status == "ok"
    assert "减速慢行" in res.speech and "小雨" in res.speech


def test_driving_advice_general_weather_unavailable():
    """无目的地且天气协作失败 → 仍给保守建议，不 NEED_SLOT 不报错。"""
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="failed", speech=""))
    res = asyncio.run(run_handle(
        agent, "safety.driving_advice", slots={}, raw_text="适合出行吗",
        ctx=make_context()))
    assert res.status == "ok"
    assert "减速慢行" in res.speech


def test_weather_alert_needs_city():
    """safety.weather_alert 无城市 → NEED_SLOT"""
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.weather_alert",
        slots={}, raw_text="有天气预警吗", ctx=ctx))
    assert res.status == "need_slot"
    assert "city" in res.missing_slots


def test_weather_alert_never_falls_back_to_the_mock_vehicle_location():
    """C9-A（真栈 info T4）：深圳问了三轮之后，「现在有影响开车的天气预警吗」
    答出「**上海**当前有1条天气预警」——那个上海逐字来自 `memory/store.py` 的
    mock 车辆位置 `{"city":"上海","road":"延安高架"}`，本方法是全仓**最后一条**
    还留着 `ctx.fetch("vehicle.location")` 回退的天气路径。

    ⚠ 上一条用例（`test_weather_alert_needs_city`）用的是**空 context**，
    恰好绕开了回退分支——**它一直是绿的**。这一条把 mock 值真的放进 context：
    宁可诚实问一句城市，也不拿 mock 位置冒充「当前位置」。
    """
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="ok", speech="上海当前有1条天气预警"))
    ctx = make_context(context_values={
        "vehicle.location": '{"lat": 31.23, "lng": 121.47, "city": "上海", "road": "延安高架"}'})
    res = asyncio.run(run_handle(
        agent, "safety.weather_alert", slots={},
        raw_text="现在有影响开车的天气预警吗", ctx=ctx))

    assert res.status == "need_slot"
    assert "city" in res.missing_slots
    assert "上海" not in (res.speech or "")
    assert agent._agents.calls == [], "拿不到城市就不该去查——查了就会把 mock 说成事实"


def test_weather_alert_uses_this_turn_gps_before_asking():
    """有本轮 GPS 时用坐标（与 info `_resolve_city` 逐字同格 `lng,lat`），不问城市。
    诚实降级的顺序是「本轮定位 → 问一句」，**中间没有 mock 这一档**。"""
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="ok", speech="当前位置暂无生效预警"))
    res = asyncio.run(run_handle(
        agent, "safety.weather_alert", slots={},
        raw_text="现在有影响开车的天气预警吗", ctx=make_context(),
        meta={"current_lat": "22.54", "current_lng": "114.06"}))

    assert res.status == "ok"
    assert agent._agents.calls == [("info", "info.alerts", {"city": "114.060000,22.540000"})]


def test_gps_derived_locator_never_reaches_the_user_speech():
    """**查询用的定位串与说给人听的地名是两个东西。**

    真栈实测（2026-08-28 部署后第一趟迷你集）：用户听到
    「**113.941200,22.541000**当前没有生效的天气预警。」——C9-A 把这条路径从
    「mock 城市名」换成了 `lng,lat` 坐标串交给 provider，而兜底话术还在直接念
    那个值。C9-D 在 info 那侧挡住了同一个形态（`_display_city` /
    `_is_coordinate_label`），**偏偏漏了 C9-A 自己改的这个文件**。

    判据：⇒ **新造一条数据通路时，要把它流向的每一个出口都走一遍。**
    """
    agent = RoadSafetyAgent()
    # info 这一跳没给出话术 ⇒ 走本方法自己的兜底句，那句正是出事的地方。
    agent._agents = _FakeAgents(AgentResult(status="ok", speech=""))
    res = asyncio.run(run_handle(
        agent, "safety.weather_alert", slots={},
        raw_text="现在有影响开车的天气预警吗", ctx=make_context(),
        meta={"current_lat": "22.541", "current_lng": "113.9412"}))

    assert res.status == "ok"
    assert "113.9412" not in res.speech and "22.541" not in res.speech
    assert res.speech.startswith("当前位置")
    # provider 那一跳仍然必须拿到坐标——**挡的是出口，不是数据本身**。
    assert agent._agents.calls == [
        ("info", "info.alerts", {"city": "113.941200,22.541000"})]


def test_user_named_city_is_still_spoken_verbatim():
    """误伤对照：用户点名了城市就照原样念，别把它也换成「当前位置」。"""
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="ok", speech=""))
    res = asyncio.run(run_handle(
        agent, "safety.weather_alert", slots={"city": "深圳"},
        raw_text="深圳有预警吗", ctx=make_context()))
    assert res.speech == "深圳当前没有生效的天气预警。"


def test_road_condition_needs_route():
    """safety.road_condition 无路线 → NEED_SLOT"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.road_condition",
        slots={}, raw_text="路况怎么样", ctx=ctx))
    assert res.status == "need_slot"
    assert "route" in res.missing_slots


def test_driving_advice_with_collaboration():
    """safety.driving_advice 有目的地 → 尝试协作（降级不影响返回）"""
    ctx = make_context(context_values={"vehicle.speed": "60", "vehicle.battery": "72%"})
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.driving_advice",
        slots={"destination": "上海"}, raw_text="开车去上海安全吗", ctx=ctx))
    # 协作可能失败（无 info agent），但 LLM 仍应给出建议
    assert res.status == "ok"
    assert res.speech  # 应有安全建议


def test_unsupported_intent():
    """不支持的意图 → FAILED"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.unknown",
        slots={}, raw_text="xxx", ctx=ctx))
    assert res.status == "failed"


# ── 响应式主动播报（设计 §3.3 场景2：NATS 订阅 + 30 分钟节流）────────────

class _FakeAgents:
    """假的跨 Agent 客户端：返回预置 info.alerts 结果，记录调用。"""
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def call(self, agent_id, intent, slots, ctx=None, timeout=None):
        self.calls.append((agent_id, intent, slots))
        return self._result


def _msg(changes):
    """伪造 NATS 消息：data 为 vehicle.state.changed 事件 JSON。"""
    import json
    data = json.dumps({"changes": changes}).encode()
    return type("Msg", (), {"data": data})()


def test_proactive_disabled_without_nats(monkeypatch):
    """无 NATS_URL → on_start 静默禁用，不订阅、不抛异常。"""
    monkeypatch.delenv("NATS_URL", raising=False)
    agent = RoadSafetyAgent()
    asyncio.run(agent.on_start())
    assert agent._nc is None


def test_evaluate_hazard_detects_alert():
    """info.alerts 有预警 → 返回含安全建议的播报话术；无预警/失败 → None。"""
    agent = RoadSafetyAgent()

    agent._agents = _FakeAgents(AgentResult(
        status="ok", speech="上海当前有2条天气预警：暴雨黄色预警（黄色级）。请注意防范。"))
    advisory = asyncio.run(agent._evaluate_hazard("上海"))
    assert advisory and "天气预警" in advisory and "降低车速" in advisory

    agent._agents = _FakeAgents(AgentResult(status="ok", speech="上海当前没有生效的天气预警。"))
    assert asyncio.run(agent._evaluate_hazard("上海")) is None

    agent._agents = _FakeAgents(AgentResult(status="failed", speech=""))
    assert asyncio.run(agent._evaluate_hazard("上海")) is None


def test_throttle_suppresses_repeat_within_window():
    """同类提示 30 分钟内不重复：记录后窗口内 _should_broadcast 为 False。"""
    agent = RoadSafetyAgent()
    now = float(epoch_at(2026, 6, 21, 14, 0))  # 下午（非夜间，业务时区）
    assert agent._should_broadcast("weather_safety", now) is True
    agent._last_broadcast["weather_safety"] = now
    assert agent._should_broadcast("weather_safety", now + 600) is False      # 10 分钟内
    assert agent._should_broadcast("weather_safety", now + 1801) is True      # 超 30 分钟


def test_night_uses_longer_throttle_window():
    """夜间降频：白天 30 分钟可再播，夜间需 60 分钟。"""
    agent = RoadSafetyAgent()
    night = float(epoch_at(2026, 6, 21, 23, 0))
    assert agent._is_night(night) is True
    agent._last_broadcast["weather_safety"] = night
    assert agent._should_broadcast("weather_safety", night + 1801) is False   # 夜间 30 分钟仍抑制
    assert agent._should_broadcast("weather_safety", night + 3601) is True    # 超 60 分钟


def test_state_event_broadcasts_once_then_throttled(monkeypatch):
    """两次 location 变更事件：命中预警只主动播报一次，第二次被节流。"""
    agent = RoadSafetyAgent()

    async def fake_hazard(city):
        return f"{city}有天气预警，建议降低车速。"
    monkeypatch.setattr(agent, "_evaluate_hazard", fake_hazard)

    published = []

    async def fake_publish(advisory_type, speech):
        published.append((advisory_type, speech))
    monkeypatch.setattr(agent, "_publish_proactive", fake_publish)

    async def run():
        await agent._on_state_event(_msg([{"key": "location", "new": "杭州"}]))
        await agent._on_state_event(_msg([{"key": "location", "new": "杭州"}]))
    asyncio.run(run())

    assert len(published) == 1                       # 第二次被 30 分钟节流
    assert published[0][0] == "weather_safety"


def test_state_event_ignores_non_location_change(monkeypatch):
    """非 location 变更（如车速）不触发预警查询/播报。"""
    agent = RoadSafetyAgent()
    called = {"hazard": False}

    async def fake_hazard(city):
        called["hazard"] = True
        return None
    monkeypatch.setattr(agent, "_evaluate_hazard", fake_hazard)

    asyncio.run(agent._on_state_event(_msg([{"key": "speed_kmh", "new": 60}])))
    assert called["hazard"] is False


# ── 批 7 ②：safety.road_condition 接真路况（不再拿「X 路况」搜 POI）──────────────────────
# 修前：`navigation.search_poi keyword="去宝安机场的路况 路况"`，空结果原样播「为您找到 0 个…推荐前三个：。
# 需要导航过去吗？」；非空是几个名字带「路况」的 POI。名字存在能力不可达。现在归一 route 槽后调
# navigation 的内部意图 `route_traffic`，话术 / 卡原样转发，拥堵时补一句安全提示。

import json
import time

from agents.road_safety.src.agent import route_target


@pytest.mark.parametrize("text, expected", [
    ("去宝安机场的路况", ("destination", "宝安机场")),
    ("到深圳北站堵不堵", ("destination", "深圳北站")),
    ("前往东方之门的路", ("destination", "东方之门")),
    ("宝安机场", ("destination", "宝安机场")),
    ("深南大道路况", ("road", "深南大道")),
    ("京港澳高速堵吗", ("road", "京港澳高速")),
    ("北环大道", ("road", "北环大道")),
    ("G4 堵不堵", ("road", "G4")),
    ("去深南大道的路况", ("destination", "深南大道")),     # 明说「去」就是到那儿的一路
    ("路况怎么样", ("active", "")),
    ("高速堵车吗", ("active", "")),
    ("前面堵不堵", ("active", "")),
    ("这条路", ("active", "")),
    ("", ("active", "")),
])
def test_route_target_normalises_the_route_slot(text, expected):
    assert route_target(text) == expected


def _traffic_result(**data):
    payload = {"destination": "深圳宝安国际机场", "distance_km": 35.2, "duration_min": 42,
               "traffic": {"expedite_km": 30.0, "slow_km": 3.0, "congested_km": 0.3,
                           "blocked_km": 0.0, "unknown_km": 1.0},
               "traffic_source": "route_tmcs", "traffic_lookup": "route"}
    payload.update(data)
    return AgentResult(status="ok", speech="从当前位置到深圳宝安国际机场全程约35.2公里、预计42分钟，"
                                          "沿途缓行约3.0公里、拥堵约0.3公里，其余畅通。",
                       ui_card={"type": "route_plan", "estimate": True}, data=payload)


def test_road_condition_to_a_destination_calls_route_traffic_not_poi_search():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(_traffic_result())
    res = asyncio.run(run_handle(agent, "safety.road_condition",
                                 slots={"route": "去宝安机场的路况"}, raw_text="去宝安机场的路况",
                                 ctx=make_context()))
    assert agent._agents.calls == [("navigation", "navigation.route_traffic", {"destination": "宝安机场"})]
    assert res.status == "ok" and not res.actions
    assert res.speech.startswith("从当前位置到深圳宝安国际机场全程约35.2公里")
    for junk in ("为您找到", "推荐前三个", "需要导航过去吗", "搜索"):
        assert junk not in res.speech
    assert res.ui_card == {"type": "route_plan", "estimate": True}
    assert res.data["traffic_source"] == "route_tmcs"


def test_road_condition_for_a_named_road_passes_the_road():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="ok", speech="深南大道目前整体畅通，畅通路段约占88.5%。",
                                            data={"status": 1, "traffic_lookup": "road"}))
    res = asyncio.run(run_handle(agent, "safety.road_condition",
                                 slots={"route": "深南大道路况"}, raw_text="深南大道路况怎么样",
                                 ctx=make_context()))
    assert agent._agents.calls == [("navigation", "navigation.route_traffic", {"road": "深南大道"})]
    assert res.speech == "深南大道目前整体畅通，畅通路段约占88.5%。"


def test_road_condition_without_a_route_uses_the_active_route_when_there_is_one():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(_traffic_result())
    active = {"destination": "深圳宝安国际机场", "lat": 22.6393, "lng": 113.8107, "ts": int(time.time())}
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={}, raw_text="路况怎么样",
                                 ctx=make_context(), meta={"focus_active_route": json.dumps(active)}))
    assert agent._agents.calls == [("navigation", "navigation.route_traffic", {})]
    assert res.status == "ok"


def test_road_condition_reads_the_destination_from_the_raw_text_when_the_slot_is_empty():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(_traffic_result())
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={}, raw_text="去宝安机场的路堵不堵",
                                 ctx=make_context()))
    assert agent._agents.calls == [("navigation", "navigation.route_traffic", {"destination": "宝安机场"})]
    assert res.status == "ok"


def test_road_condition_without_route_or_active_route_still_asks_and_calls_nobody():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(_traffic_result())
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={}, raw_text="路况怎么样",
                                 ctx=make_context()))
    assert res.status == "need_slot" and "route" in res.missing_slots
    assert agent._agents.calls == []


def test_navigation_asking_for_a_destination_becomes_our_route_slot():
    """navigation 反问「哪条路线」时（活动路线过龄等），挂起要落在本能力自己的槽名上。"""
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="need_slot", speech="您想查询哪条路线的路况？",
                                            missing_slots=["destination"]))
    active = {"destination": "深圳宝安国际机场", "lat": 22.6393, "lng": 113.8107, "ts": 1}
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={}, raw_text="路况怎么样",
                                 ctx=make_context(), meta={"focus_active_route": json.dumps(active)}))
    assert res.status == "need_slot" and res.missing_slots == ["route"]


def test_road_condition_collaboration_failure_is_honest():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(AgentResult(status="failed", speech=""))
    res = asyncio.run(run_handle(agent, "safety.road_condition",
                                 slots={"route": "去宝安机场的路况"}, raw_text="去宝安机场的路况",
                                 ctx=make_context()))
    assert res.status == "ok" and res.speech == "暂时查不到去宝安机场这一路的路况。"
    assert not res.actions


def test_heavy_congestion_gets_a_safety_tip_light_congestion_does_not():
    agent = RoadSafetyAgent()
    agent._agents = _FakeAgents(_traffic_result(
        traffic={"expedite_km": 20.0, "slow_km": 3.0, "congested_km": 1.4, "blocked_km": 0.6,
                 "unknown_km": 0.0}))
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={"route": "宝安机场"},
                                 raw_text="去宝安机场堵不堵", ctx=make_context()))
    assert "保持车距" in res.speech
    agent._agents = _FakeAgents(_traffic_result())      # 拥堵 0.3 公里：不唠叨
    res = asyncio.run(run_handle(agent, "safety.road_condition", slots={"route": "宝安机场"},
                                 raw_text="去宝安机场堵不堵", ctx=make_context()))
    assert "保持车距" not in res.speech

"""road-safety Agent 契约测试。

覆盖：safety.driving_advice / safety.weather_alert / safety.road_condition。
验证 NEED_SLOT、协作降级、只建议不控车。
"""
import asyncio
import pytest
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents._sdk import AgentResult
from agents._sdk.testing import make_context, run_handle
from agents.road_safety.src.agent import RoadSafetyAgent


def test_driving_advice_needs_destination():
    """safety.driving_advice 无目的地 → NEED_SLOT"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.driving_advice",
        slots={}, raw_text="路上怎么样", ctx=ctx))
    assert res.status == "need_slot"
    assert "destination" in res.missing_slots


def test_weather_alert_needs_city():
    """safety.weather_alert 无城市 → NEED_SLOT"""
    ctx = make_context(context_values={})
    res = asyncio.run(run_handle(
        RoadSafetyAgent(), "safety.weather_alert",
        slots={}, raw_text="有天气预警吗", ctx=ctx))
    assert res.status == "need_slot"
    assert "city" in res.missing_slots


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
    now = time.mktime((2026, 6, 21, 14, 0, 0, 0, 0, -1))  # 下午（非夜间）
    assert agent._should_broadcast("weather_safety", now) is True
    agent._last_broadcast["weather_safety"] = now
    assert agent._should_broadcast("weather_safety", now + 600) is False      # 10 分钟内
    assert agent._should_broadcast("weather_safety", now + 1801) is True      # 超 30 分钟


def test_night_uses_longer_throttle_window():
    """夜间降频：白天 30 分钟可再播，夜间需 60 分钟。"""
    agent = RoadSafetyAgent()
    night = time.mktime((2026, 6, 21, 23, 0, 0, 0, 0, -1))
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

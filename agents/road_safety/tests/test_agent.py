"""road-safety Agent 契约测试。

覆盖：safety.driving_advice / safety.weather_alert / safety.road_condition。
验证 NEED_SLOT、协作降级、只建议不控车。
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

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

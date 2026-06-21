"""charging-planner Agent 契约测试。

覆盖三种路径：NEED_SLOT / OK / NEED_CONFIRM。
验证 Provider 降级、协作降级、车控只产 action。
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents._sdk.testing import make_context, run_handle, assert_result_valid
from agents.charging_planner.src.agent import ChargingPlannerAgent


def test_find_returns_list():
    """charging.find → OK + ui_card.charging_list"""
    ctx = make_context(context_values={"vehicle.battery": "72%", "vehicle.location": "科技园"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.find",
        slots={"prefer": "快充"}, raw_text="找个充电站", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "charging_list"
    assert len(res.ui_card["items"]) > 0


def test_plan_needs_confirm():
    """charging.plan → NEED_CONFIRM + require_confirm"""
    ctx = make_context(context_values={"vehicle.battery": "45%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={"destination": "杭州"}, raw_text="去杭州怎么充电", ctx=ctx))
    assert res.status == "need_confirm"
    assert res.actions and res.actions[0].get("require_confirm") is True


def test_plan_needs_destination():
    """charging.plan 无目的地 → NEED_SLOT"""
    ctx = make_context(context_values={"vehicle.battery": "45%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={}, raw_text="帮我规划充电", ctx=ctx))
    assert res.status == "need_slot"
    assert "destination" in res.missing_slots


def test_status_returns_battery():
    """charging.status → OK + battery data"""
    ctx = make_context(context_values={"vehicle.battery": "72%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.status",
        slots={}, raw_text="现在电量多少", ctx=ctx))
    assert res.status == "ok"
    assert "72%" in res.speech


def test_find_provider_fallback():
    """Provider 失败 → 降级 mock，链路不阻断"""
    from agents._sdk.http import ProviderError
    agent = ChargingPlannerAgent()
    # 强制让 provider 抛 ProviderError（触发降级）
    async def _fail(*a, **kw):
        raise ProviderError("provider down")
    agent.charging.find_nearby = _fail
    ctx = make_context(context_values={"vehicle.battery": "72%", "vehicle.location": "科技园"})
    res = asyncio.run(run_handle(
        agent, "charging.find",
        slots={}, raw_text="找个充电站", ctx=ctx))
    # 降级到 mock，仍应返回结果
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "charging_list"


def test_unsupported_intent():
    """不支持的意图 → FAILED"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.unknown",
        slots={}, raw_text="xxx", ctx=ctx))
    assert res.status == "failed"

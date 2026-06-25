"""Phase 4：敏感上下文按 manifest context_scopes 最小化下发。

覆盖 clients._merge_meta 的 scope 过滤 + UnifiedDispatcher 把 step.context_scopes 透传给 cloud_call。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.clients import Clients
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.models import PlanContext, Step


def _ctx(prefs):
    return SimpleNamespace(prefs=prefs)


# ── _merge_meta scope 过滤 ──

def test_merge_meta_no_filter_when_scopes_none():
    """context_scopes=None（edge/stream/legacy 路径）→ 不过滤，保持既有行为。"""
    ctx = _ctx({"current_lat": "39.9", "vehicle_battery": "80", "answer_length": "short"})
    out = Clients._merge_meta(ctx, {"trace_id": "t"})
    assert out["current_lat"] == "39.9"
    assert out["vehicle_battery"] == "80"
    assert out["answer_length"] == "short"
    assert out["trace_id"] == "t"


def test_merge_meta_drops_sensitive_when_no_scope_declared():
    """声明为空（未声明任何敏感 scope）→ 精确位置/电量全部剔除；非敏感偏好保留。"""
    ctx = _ctx({"current_lat": "39.9", "current_lng": "116.4",
                "current_accuracy_m": "10", "vehicle_battery": "80",
                "answer_length": "short", "model_pref": "fast"})
    out = Clients._merge_meta(ctx, {}, context_scopes=[])
    assert "current_lat" not in out
    assert "current_lng" not in out
    assert "current_accuracy_m" not in out
    assert "vehicle_battery" not in out
    assert out["answer_length"] == "short"   # 非敏感保留
    assert out["model_pref"] == "fast"


def test_merge_meta_keeps_location_when_declared():
    ctx = _ctx({"current_lat": "39.9", "current_lng": "116.4", "vehicle_battery": "80"})
    out = Clients._merge_meta(ctx, {}, context_scopes=["location"])
    assert out["current_lat"] == "39.9"
    assert out["current_lng"] == "116.4"
    assert "vehicle_battery" not in out      # 未声明 vehicle_state


def test_merge_meta_keeps_battery_when_vehicle_state_declared():
    ctx = _ctx({"current_lat": "39.9", "vehicle_battery": "80"})
    out = Clients._merge_meta(ctx, {}, context_scopes=["vehicle_state"])
    assert "current_lat" not in out          # 未声明 location
    assert out["vehicle_battery"] == "80"


def test_merge_meta_step_meta_overrides_prefs():
    ctx = _ctx({"answer_length": "short"})
    out = Clients._merge_meta(ctx, {"confirmed": "true"}, context_scopes=["location"])
    assert out["confirmed"] == "true"
    assert out["answer_length"] == "short"


# ── dispatcher 透传 step.context_scopes ──

def test_dispatch_passes_context_scopes_to_cloud_call():
    captured = {}

    async def cloud(endpoint, intent, slots, ctx, meta, **kwargs):
        captured.update(kwargs)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="ok")

    async def edge(*_a):
        raise AssertionError("edge route should not be used")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=edge, tools=None)
    step = Step(id="s1", agent_id="navigation", endpoint="nav:50061",
                intent="navigation.search_poi", context_scopes=["location"])
    asyncio.run(dispatcher.dispatch(step, PlanContext()))
    assert captured.get("context_scopes") == ["location"]

"""Deterministic in-process tool registry."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from google.protobuf.json_format import MessageToDict
from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.tools import ToolRegistry


def _call(registry, intent, slots):
    return asyncio.run(registry.call(intent, slots, PlanContext()))


def _data(response):
    return MessageToDict(response.data, preserving_proto_field_name=True)


def test_math_eval_supports_arithmetic_without_python_eval_features():
    registry = ToolRegistry()

    response = _call(registry, "math.eval", {"expression": "2 + 3 * 4"})

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert _data(response)["result"] == 14

    rejected = _call(
        registry, "math.eval", {"expression": "__import__('os').system('whoami')"})
    assert rejected.status == agent_pb2.ExecuteResponse.REJECTED


def test_unit_convert_handles_compatible_units():
    response = _call(ToolRegistry(), "unit.convert", {
        "value": "1.5", "from_unit": "km", "to_unit": "m",
    })

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert _data(response)["value"] == 1500
    assert _data(response)["unit"] == "m"


def test_datetime_parse_normalizes_relative_chinese_time():
    tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    registry = ToolRegistry(
        now_fn=lambda: datetime(2026, 6, 14, 10, 0, tzinfo=tz))

    response = _call(
        registry, "datetime.parse", {"text": "明天19:30"})

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert _data(response)["iso8601"] == "2026-06-15T19:30:00+08:00"


def test_tool_manifest_is_discoverable_and_has_no_vehicle_permission():
    manifest = ToolRegistry().manifest

    assert manifest.agent_id == "builtin-tools"
    assert manifest.kind == "tool"
    assert manifest.deployment == "cloud"
    assert list(manifest.requires_permissions) == []
    assert {cap.intent for cap in manifest.capabilities} == {
        "datetime.parse", "unit.convert", "math.eval",
    }


def test_unknown_tool_fails_closed():
    response = _call(ToolRegistry(), "vehicle.control", {})

    assert response.status == agent_pb2.ExecuteResponse.FAILED
    assert response.error.code == "tool_not_found"

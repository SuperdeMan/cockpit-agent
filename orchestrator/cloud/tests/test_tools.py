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


def test_datetime_parse_answers_a_date_only_question():
    tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    registry = ToolRegistry(
        now_fn=lambda: datetime(2026, 6, 20, 10, 0, tzinfo=tz))

    response = _call(registry, "datetime.parse", {"text": "今天是几号"})

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert _data(response)["date"] == "2026-06-20"
    assert "2026年6月20日" in response.speech
    assert "星期六" in response.speech


def test_datetime_parse_accepts_a_planner_reduced_today_slot():
    tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    registry = ToolRegistry(
        now_fn=lambda: datetime(2026, 6, 20, 10, 0, tzinfo=tz))

    response = _call(registry, "datetime.parse", {"text": "今天"})

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert _data(response)["date"] == "2026-06-20"


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


# ── Android N-01（2026-09-24）：工具输入错误的话术是给用户听的 ──────────────────────────────────
# 修前 `speech=str(exc)`：REJECTED 在 executor 里映射成 FAILED，聚合器对单步失败「Agent 自己的失败话术原样透传」
# （C11-B）⇒ 真机上英文问时间听到的是「unsupported datetime format」。诊断串只该进 `error.message`。
import re

import pytest


@pytest.mark.parametrize("intent, slots, diagnostic", [
    ("datetime.parse", {"text": "what is the time in Shenzhen now"}, "unsupported datetime format"),  # 真机原句
    ("datetime.parse", {"text": ""}, "missing datetime text"),
    ("datetime.parse", {"text": "明天25点"}, "invalid time"),
    ("math.eval", {"expression": "__import__('os')"}, "unsupported expression"),
    ("math.eval", {"expression": "1/0"}, "division by zero"),
    ("math.eval", {"expression": "9**9999"}, "exponent is too large"),
    ("unit.convert", {"value": "abc", "from_unit": "km", "to_unit": "m"}, "value must be numeric"),
    ("unit.convert", {"value": "1", "from_unit": "km", "to_unit": "kg"}, "incompatible units"),
    ("unit.convert", {"value": "1", "from_unit": "mile", "to_unit": "km"}, "unsupported unit"),
])
def test_tool_input_errors_are_spoken_in_chinese_and_diagnosed_in_the_error(intent, slots, diagnostic):
    rejected = _call(ToolRegistry(), intent, slots)
    assert rejected.status == agent_pb2.ExecuteResponse.REJECTED
    assert rejected.error.code == "invalid_request"
    assert rejected.error.message == diagnostic
    assert rejected.speech, "失败话术不能为空（聚合器会换成裸「处理失败」，丢掉恢复指引）"
    assert not re.search(r"[A-Za-z]{2,}", rejected.speech), rejected.speech

"""数据驱动的安全门控语料回归（P2）。

逐对象验证 VAL 安全门控，补现有只覆盖 low_beam / driving_mode / window 三类的盲区。
语料见 corpus/safety_gate.yaml，数据源是 knowledge/commands.yaml 的安全标记。
"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from val import VAL

_CORPUS = os.path.join(os.path.dirname(__file__), "corpus", "safety_gate.yaml")
with open(_CORPUS, encoding="utf-8") as _f:
    _CASES = yaml.safe_load(_f)


@pytest.fixture
def val():
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    return VAL(knowledge_dir=knowledge_dir)


def _id(case):
    return f"{case['object']}.{case['operate']}"


@pytest.mark.parametrize("case", _CASES["voice_forbidden"], ids=_id)
def test_voice_forbidden_rejected(val, case):
    ok, msg = val._safety_gate(case["object"], case["operate"], {})
    assert not ok, f"{_id(case)} 应被语音禁止门控拒绝"
    assert "语音" in msg or "手动" in msg


@pytest.mark.parametrize("case", _CASES["drive_restricted"], ids=_id)
def test_drive_restricted_blocked_in_motion(val, case):
    val.state["speed_kmh"] = 60
    val.state["gear"] = "D"
    ok, msg = val._safety_gate(case["object"], case["operate"], {})
    assert not ok, f"{_id(case)} 行车中应被拒"
    assert "行驶" in msg or "行车" in msg


@pytest.mark.parametrize("case", _CASES["drive_restricted"], ids=_id)
def test_drive_restricted_allowed_when_parked(val, case):
    val.state["speed_kmh"] = 0
    val.state["gear"] = "P"
    ok, _ = val._safety_gate(case["object"], case["operate"], {})
    assert ok, f"{_id(case)} 停车时应允许"


@pytest.mark.parametrize("obj", _CASES["require_confirm"])
def test_require_confirm_objects_need_confirmation(val, obj):
    assert val._need_confirm(obj) is True, f"{obj} 应为危险动作需二次确认"


def test_high_speed_window_blocked(val):
    val.state["speed_kmh"] = _CASES["high_speed_window"]["blocked_speed"]
    val.state["gear"] = "D"
    ok, msg = val._safety_gate("window", "open", {})
    assert not ok
    assert "高速" in msg or "安全" in msg


def test_normal_speed_window_allowed(val):
    val.state["speed_kmh"] = _CASES["high_speed_window"]["allowed_speed"]
    val.state["gear"] = "D"
    ok, _ = val._safety_gate("window", "open", {})
    assert ok

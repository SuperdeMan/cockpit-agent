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


# ── ws8 P0 安全门控：高速车窗/天窗、低电量、倒车、儿童锁（新增，此前无覆盖）──

@pytest.mark.parametrize("obj", ["window", "sunroof"])
def test_high_speed_80_blocks_window_and_sunroof(val, obj):
    val.state["speed_kmh"] = 100  # >80
    val.state["gear"] = "D"
    ok, msg = val._safety_gate(obj, "open", {})
    assert not ok
    assert "高速" in msg


@pytest.mark.parametrize("obj,data", [
    ("seat", {"mode": "heating"}),         # 座椅加热（设计点名的高耗电功能）
    ("seat", {"mode": "ventilation"}),     # 座椅通风
    ("steering_wheel", {"mode": "heating"}),  # 方向盘加热
    ("ambient_light", {}),                 # 氛围灯
    ("fragrance", {}),                     # 香氛
])
def test_low_battery_blocks_high_power(val, obj, data):
    val.state["battery"] = 5  # <10%
    ok, msg = val._safety_gate(obj, "set", data)
    assert not ok, f"低电量应禁用 {obj}/{data}"
    assert "电量" in msg


def test_low_battery_allows_seat_heating_when_charged(val):
    """回归：电量正常时座椅加热不应被低电量门控误拦。"""
    val.state["battery"] = 60
    ok, _ = val._safety_gate("seat", "set", {"mode": "heating"})
    assert ok


def test_reversing_blocks_non_safety_control(val):
    val.state["gear"] = "R"
    ok, msg = val._safety_gate("ambient_light", "open", {})
    assert not ok
    assert "倒车" in msg


def test_reversing_allows_safety_control(val):
    """倒车中仍允许安全相关车控（雨刷/后视镜/大灯）。"""
    val.state["gear"] = "R"
    ok, _ = val._safety_gate("wiper", "open", {})
    assert ok


def test_child_lock_blocks_rear_window(val):
    val.state["child_lock"] = True
    ok, msg = val._safety_gate("window", "open", {"positions": ["rear_left"]})
    assert not ok
    assert "儿童锁" in msg


def test_child_lock_allows_front_window(val):
    """儿童锁只锁后排，前排车窗仍可控。"""
    val.state["child_lock"] = True
    ok, _ = val._safety_gate("window", "open", {"positions": ["front_left"]})
    assert ok

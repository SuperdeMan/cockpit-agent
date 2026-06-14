"""VAL 知识库升级测试。

覆盖：YAML 加载、实体归一化、命令校验、安全门控、话术选择、向后兼容。
"""
from __future__ import annotations

import os
import sys
import pytest

# 确保 import 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from val import VAL


@pytest.fixture
def val():
    """加载知识库的 VAL 实例。"""
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    return VAL(knowledge_dir=knowledge_dir)


@pytest.fixture
def val_no_knowledge():
    """不加载知识库的 VAL 实例（回退硬编码）。"""
    return VAL(knowledge_dir="/nonexistent")


# ═══════════════════════════════════════════════════
# 1. YAML 加载
# ═══════════════════════════════════════════════════

class TestKnowledgeLoading:
    def test_commands_loaded(self, val):
        assert "objects" in val.commands
        assert "aircon" in val.commands["objects"]
        assert "window" in val.commands["objects"]

    def test_entities_loaded(self, val):
        assert "positions" in val.entities
        assert "seat_modes" in val.entities
        assert "light_colors" in val.entities

    def test_responses_loaded(self, val):
        assert "hvac_on_success" in val.responses
        assert "Car_general_restrictions_4" in val.responses

    def test_all_objects_present(self, val):
        """设计文档 §3.1 字段字典中的所有 object 都应存在。"""
        expected_objects = [
            "seat", "window", "sunroof", "sunshade", "aircon",
            "ambient_light", "low_beam", "headlight", "trunk",
            "door_lock", "fuel_tank_cover", "charging_port",
            "rear_view_mirror", "steering_wheel", "wiper",
            "fragrance", "tire_pressure_monitoring", "dashcam",
            "scene_mode", "driving_mode", "power_mode",
            "energy_recovery", "lane_departure_assistance",
            "lane_assistance", "accompany_home",
            "volume", "page", "screen", "app", "weather",
        ]
        objects = val.commands.get("objects", {})
        for obj in expected_objects:
            assert obj in objects, f"缺少 object: {obj}"

    def test_fallback_when_no_knowledge(self, val_no_knowledge):
        """无知识库时 commands/entities/responses 为空 dict。"""
        assert val_no_knowledge.commands == {}
        assert val_no_knowledge.entities == {}
        assert val_no_knowledge.responses == {}


# ═══════════════════════════════════════════════════
# 2. 实体归一化
# ═══════════════════════════════════════════════════

class TestEntityNormalization:
    def test_position_main_driver(self, val):
        data = {"positions": ["主驾"]}
        result = val._normalize_entities(data)
        assert result["positions"] == ["front_left"]

    def test_position_copilot(self, val):
        data = {"positions": ["副驾"]}
        result = val._normalize_entities(data)
        assert result["positions"] == ["front_right"]

    def test_position_front_row(self, val):
        data = {"positions": ["前排"]}
        result = val._normalize_entities(data)
        assert set(result["positions"]) == {"front_left", "front_right"}

    def test_position_rear_row(self, val):
        data = {"positions": ["后排"]}
        result = val._normalize_entities(data)
        assert set(result["positions"]) == {"rear_left", "rear_right"}

    def test_position_all(self, val):
        data = {"positions": ["全车"]}
        result = val._normalize_entities(data)
        assert result["positions"] == ["all"]

    def test_position_alias(self, val):
        data = {"positions": ["主驾位"]}
        result = val._normalize_entities(data)
        assert result["positions"] == ["front_left"]

    def test_seat_mode_heating(self, val):
        data = {"mode": "加热"}
        result = val._normalize_entities(data)
        assert result["mode"] == "heating"

    def test_seat_mode_ventilation(self, val):
        data = {"mode": "通风"}
        result = val._normalize_entities(data)
        assert result["mode"] == "ventilation"

    def test_aircon_mode_internal(self, val):
        data = {"mode": "内循环"}
        result = val._normalize_entities(data)
        assert result["mode"] == "internal"

    def test_driving_mode_sport(self, val):
        data = {"mode": "运动"}
        result = val._normalize_entities(data)
        assert result["mode"] == "sport"

    def test_color_red(self, val):
        data = {"tag": "红色"}
        result = val._normalize_entities(data)
        assert result["tag"] == "red"

    def test_unit_degree(self, val):
        data = {"unit": "度"}
        result = val._normalize_entities(data)
        assert result["unit"] == "degree"


# ═══════════════════════════════════════════════════
# 3. 命令校验
# ═══════════════════════════════════════════════════

class TestCommandValidation:
    def test_valid_command(self, val):
        ok, _ = val._validate_command("aircon", "set", {"attr": "temperature"})
        assert ok

    def test_invalid_object(self, val):
        ok, msg = val._validate_command("nonexistent", "open", {})
        assert not ok
        assert "暂不支持" in msg

    def test_invalid_operate(self, val):
        ok, msg = val._validate_command("aircon", "fly", {})
        assert not ok
        assert "暂不支持" in msg

    def test_invalid_attr(self, val):
        ok, msg = val._validate_command("aircon", "set", {"attr": "color"})
        assert not ok
        assert "暂不支持" in msg

    def test_invalid_mode(self, val):
        ok, msg = val._validate_command("aircon", "set", {"mode": "turbo"})
        assert not ok
        assert "暂不支持" in msg

    def test_vehicle_model_supported(self, val):
        val.vehicle_model = "DeepWay"
        ok, _ = val._validate_command("aircon", "set", {})
        assert ok

    def test_vehicle_model_not_supported(self, val):
        val.vehicle_model = "UnknownModel"
        ok, msg = val._validate_command("aircon", "set", {})
        assert not ok
        assert "当前车型不支持" in msg

    def test_no_knowledge_skip_validation(self, val_no_knowledge):
        """无知识库时跳过校验。"""
        ok, _ = val_no_knowledge._validate_command("anything", "do", {})
        assert ok


# ═══════════════════════════════════════════════════
# 4. 安全门控
# ═══════════════════════════════════════════════════

class TestSafetyGate:
    def test_voice_forbidden_rejected(self, val):
        ok, msg = val._safety_gate("low_beam", "open", {})
        assert not ok
        assert "语音" in msg or "手动" in msg

    def test_drive_restricted_in_motion_open(self, val):
        val.state["speed_kmh"] = 60
        val.state["gear"] = "D"
        ok, msg = val._safety_gate("driving_mode", "set", {})
        assert not ok
        assert "行驶" in msg or "行车" in msg

    def test_drive_restricted_in_motion_close(self, val):
        val.state["speed_kmh"] = 60
        val.state["gear"] = "D"
        ok, msg = val._safety_gate("driving_mode", "close", {})
        assert not ok
        assert "行驶" in msg or "行车" in msg

    def test_drive_restricted_parked_ok(self, val):
        val.state["speed_kmh"] = 0
        val.state["gear"] = "P"
        ok, _ = val._safety_gate("driving_mode", "set", {})
        assert ok

    def test_high_speed_window_blocked(self, val):
        val.state["speed_kmh"] = 130
        val.state["gear"] = "D"
        ok, msg = val._safety_gate("window", "open", {})
        assert not ok
        assert "高速" in msg or "安全" in msg

    def test_normal_speed_window_ok(self, val):
        val.state["speed_kmh"] = 30
        val.state["gear"] = "D"
        ok, _ = val._safety_gate("window", "open", {})
        assert ok

    def test_no_knowledge_skip_safety(self, val_no_knowledge):
        """无知识库时安全门控仍正常（voice_forbidden/drive_restricted 默认 False）。"""
        ok, _ = val_no_knowledge._safety_gate("anything", "open", {})
        assert ok


# ═══════════════════════════════════════════════════
# 5. 话术选择
# ═══════════════════════════════════════════════════

class TestResponseSelection:
    def test_hvac_on_response(self, val):
        key = val._build_response_key("aircon", "open", {})
        assert key == "hvac_on_success"

    def test_hvac_off_response(self, val):
        key = val._build_response_key("aircon", "close", {})
        assert key == "hvac_off_success"

    def test_window_open_response(self, val):
        key = val._build_response_key("window", "open", {})
        assert key == "window_open_success"

    def test_seat_heating_response(self, val):
        key = val._build_response_key("seat", "open", {"mode": "heating"})
        assert key == "seat_heating_on_success"

    def test_ambient_light_color_response(self, val):
        key = val._build_response_key("ambient_light", "set", {"tag": "blue"})
        assert key == "ambient_light_color_success"

    def test_pick_response_returns_string(self, val):
        msg = val._pick_response("hvac_on_success")
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_pick_response_unknown_key(self, val):
        msg = val._pick_response("nonexistent_key")
        assert msg == "nonexistent_key"

    def test_pick_response_with_template(self, val):
        msg = val._pick_response("hvac_set_success", {"value": "26"})
        assert "26" in msg


# ═══════════════════════════════════════════════════
# 6. 向后兼容（旧接口）
# ═══════════════════════════════════════════════════

class TestBackwardCompatibility:
    def test_legacy_hvac_set(self, val):
        ok, msg = val.execute("hvac.set", {"temp": "26"})
        assert ok
        assert val.state["hvac_temp"] == 26
        assert "空调" in msg

    def test_legacy_hvac_on(self, val):
        ok, msg = val.execute("hvac.on", {})
        assert ok
        assert val.state["hvac_on"] is True

    def test_legacy_hvac_off(self, val):
        val.state["hvac_on"] = True
        ok, msg = val.execute("hvac.off", {})
        assert ok
        assert val.state["hvac_on"] is False

    def test_legacy_window_open(self, val):
        ok, msg = val.execute("window.open", {})
        assert ok
        assert val.state["window"] == "open"

    def test_legacy_window_close(self, val):
        ok, msg = val.execute("window.close", {})
        assert ok
        assert val.state["window"] == "closed"

    def test_legacy_high_speed_window_blocked(self, val):
        val.state["speed_kmh"] = 130
        ok, msg = val.execute("window.open", {})
        assert not ok
        assert "高速" in msg

    def test_legacy_media(self, val):
        ok, _ = val.execute("media.play", {})
        assert ok
        assert val.state["media"] == "playing"

    def test_legacy_unknown(self, val):
        ok, msg = val.execute("unknown.cmd", {})
        assert not ok
        assert "暂不支持" in msg

    def test_structured_hvac_set(self, val):
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {"operate": "set", "object": "aircon", "value": "26", "unit": "degree"},
        }
        ok, msg = val.execute(cmd)
        assert ok
        assert val.state["hvac_temp"] == 26

    def test_structured_window_open(self, val):
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {"operate": "open", "object": "window"},
        }
        ok, msg = val.execute(cmd)
        assert ok
        assert val.state["window"] == "open"

    def test_structured_invalid_object(self, val):
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {"operate": "open", "object": "nonexistent"},
        }
        ok, msg = val.execute(cmd)
        assert not ok
        assert "暂不支持" in msg

    def test_structured_voice_forbidden(self, val):
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {"operate": "open", "object": "low_beam"},
        }
        ok, msg = val.execute(cmd)
        assert not ok
        assert "语音" in msg or "手动" in msg

    def test_structured_drive_restricted(self, val):
        val.state["speed_kmh"] = 60
        val.state["gear"] = "D"
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {"operate": "set", "object": "driving_mode", "mode": "sport"},
        }
        ok, msg = val.execute(cmd)
        assert not ok
        assert "行驶" in msg or "行车" in msg

    def test_structured_seat_heating(self, val):
        val.state["speed_kmh"] = 0
        val.state["gear"] = "P"
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {
                "operate": "open", "object": "seat", "mode": "heating",
                "positions": ["主驾"],
            },
        }
        ok, msg = val.execute(cmd)
        assert ok
        assert val.state.get("seat_heating") is True

    def test_structured_with_position_normalization(self, val):
        cmd = {
            "domain": "setting",
            "intent": "control",
            "data": {
                "operate": "open", "object": "window",
                "positions": ["前排"],
            },
        }
        ok, msg = val.execute(cmd)
        assert ok

"""Cloud-scheduled edge calls must execute only through VAL."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf.json_format import MessageToDict

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2

from edge_call import EdgeCallExecutor
from edge_agents_mod.media import MEDIA_INTENTS
from edge_agents_mod.vehicle import VEHICLE_INTENTS
from val import VAL


def _call(intent: str, slots=None, meta=None):
    return channel_pb2.EdgeCall(
        step_id="s1",
        intent=common_pb2.Intent(name=intent, slots=slots or {}),
        meta=meta or {},
    )


def test_hvac_edge_call_executes_through_val():
    val = VAL()
    executor = EdgeCallExecutor(val)

    response = executor.execute(_call("hvac.set", {"temp": "25"}))

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert val.state["hvac_on"] is True
    assert val.state["hvac_temp"] == 25


def test_edge_call_returns_action_card_for_display():
    """case2: 云端调度的车控也要回动作卡（与本地快路径口径一致），
    且打 _origin=edge_val 标记，供 server 跳过二次下发。"""
    val = VAL()
    response = EdgeCallExecutor(val).execute(_call("hvac.on"))

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert len(response.actions) == 1
    action = response.actions[0]
    assert action.type == "vehicle.control"
    payload = MessageToDict(action.payload, preserving_proto_field_name=True)
    assert payload["command"] == "hvac.on"
    assert payload["_origin"] == "edge_val"


def test_edge_call_media_intent_uses_media_action_type():
    response = EdgeCallExecutor(VAL()).execute(_call("media.play"))

    assert response.status == agent_pb2.ExecuteResponse.OK
    assert [a.type for a in response.actions] == ["media.control"]


def test_relative_temperature_actually_changes_state():
    """case2: aircon.inc/dec 必须真正调温（之前落兜底分支，温度原地不动）。"""
    val = VAL()
    val.state["hvac_temp"] = 24
    executor = EdgeCallExecutor(val)

    up = executor.execute(_call("aircon.inc"))
    assert up.status == agent_pb2.ExecuteResponse.OK
    assert val.state["hvac_temp"] == 25
    assert val.state["hvac_on"] is True
    assert "完成操作" not in up.speech  # 不再是空泛的 generic_success 话术

    down = executor.execute(_call("aircon.dec"))
    assert down.status == agent_pb2.ExecuteResponse.OK
    assert val.state["hvac_temp"] == 24


def test_safety_gate_rejection_is_not_reported_as_success():
    val = VAL()
    val.state["speed_kmh"] = 130
    executor = EdgeCallExecutor(val)

    response = executor.execute(_call("window.open"))

    assert response.status == agent_pb2.ExecuteResponse.REJECTED
    assert response.error.code == "safety_gated"
    assert val.state["window"] == "closed"


def test_dangerous_edge_call_requires_confirmation_before_state_change():
    val = VAL()
    executor = EdgeCallExecutor(val)

    pending = executor.execute(_call("trunk.open"))

    assert pending.status == agent_pb2.ExecuteResponse.NEED_CONFIRM
    assert val.state.get("trunk") != "open"

    completed = executor.execute(_call(
        "trunk.open", meta={"confirmed": "true"}))

    assert completed.status == agent_pb2.ExecuteResponse.OK
    assert val.state["trunk"] == "open"


def test_unsupported_edge_intent_fails_closed():
    response = EdgeCallExecutor(VAL()).execute(_call("unknown.do_anything"))

    assert response.status == agent_pb2.ExecuteResponse.FAILED
    assert response.error.code == "invalid_request"


def test_all_registered_edge_intents_are_executable_through_val():
    failures = []
    for intent in sorted(VEHICLE_INTENTS | MEDIA_INTENTS):
        val = VAL()
        val.state["speed_kmh"] = 0
        val.state["gear"] = "P"
        executor = EdgeCallExecutor(val)
        slots = {"value": "2", "temp": "24"}

        response = executor.execute(_call(intent, slots))
        if response.status == agent_pb2.ExecuteResponse.NEED_CONFIRM:
            response = executor.execute(_call(
                intent, slots, meta={"confirmed": "true"}))

        if response.status != agent_pb2.ExecuteResponse.OK:
            failures.append((
                intent,
                response.status,
                response.error.code,
                response.error.message,
            ))

    assert failures == []


def test_hierarchical_edge_intents_update_the_expected_val_state():
    val = VAL()
    val.state["speed_kmh"] = 0
    val.state["gear"] = "P"
    executor = EdgeCallExecutor(val)

    assert executor.execute(_call(
        "aircon.wind_speed.set", {"value": "3"})).status == agent_pb2.ExecuteResponse.OK
    assert val.state["hvac_wind_speed"] == 3

    assert executor.execute(_call(
        "screen.brightness.set", {"value": "40"})).status == agent_pb2.ExecuteResponse.OK
    assert executor.execute(_call(
        "screen.brightness.inc")).status == agent_pb2.ExecuteResponse.OK
    assert val.state["screen_brightness"] == 50

    assert executor.execute(_call(
        "steering_wheel.height.set", {"value": "2"})).status == agent_pb2.ExecuteResponse.OK
    assert val.state["steering_wheel_height"] == 2

    assert executor.execute(_call(
        "steering_wheel.heating.open")).status == agent_pb2.ExecuteResponse.OK
    assert val.state["steering_wheel_heating"] is True
    assert executor.execute(_call(
        "steering_wheel.heating.close")).status == agent_pb2.ExecuteResponse.OK
    assert val.state["steering_wheel_heating"] is False

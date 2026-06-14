"""Execute cloud-scheduled edge intents through the deterministic VAL."""
from __future__ import annotations

from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from val import VAL


def _struct(values: dict) -> struct_pb2.Struct:
    result = struct_pb2.Struct()
    result.update(values)
    return result


def _normalize_operation(operation: str) -> str:
    return {
        "on": "open",
        "off": "close",
        "play": "start",
        "next": "switch",
        "prev": "switch",
        "fold": "set",
        "unfold": "set",
    }.get(operation, operation)


def _to_structured(intent_name: str, slots: dict[str, str]) -> dict | None:
    parts = [p for p in intent_name.split(".") if p]
    if len(parts) < 2:
        return None

    raw_object = parts[0]
    object_name = {
        "hvac": "aircon",
        "tire_pressure": "tire_pressure_monitoring",
    }.get(raw_object, raw_object)
    operation = _normalize_operation(parts[-1])
    path = ".".join(parts[1:-1])
    attribute = {
        ("aircon", "wind_speed"): "speed",
        ("screen", "brightness"): "brightness",
        ("steering_wheel", "height"): "height",
        ("wiper", "speed"): "speed",
    }.get((object_name, path))
    mode = "" if attribute else path

    # Only expose objects present in the VAL knowledge base.
    known_aliases = {
        "aircon", "window", "seat", "sunroof", "sunshade", "trunk",
        "door_lock", "ambient_light", "headlight", "wiper",
        "rear_view_mirror", "fragrance", "volume", "fuel_tank_cover",
        "charging_port", "steering_wheel", "energy_recovery",
        "lane_departure_assistance", "lane_assistance", "scene_mode",
        "power_mode", "screen", "tire_pressure_monitoring", "dashcam",
        "accompany_home", "media", "bluetooth", "wifi", "hotspot",
        "auto_hold", "equalizer", "sound_effect", "voice_assistant",
        "surround_view", "dashboard", "phone", "contacts", "call_log",
        "low_beam",
    }
    if object_name not in known_aliases:
        return None

    data = dict(slots)
    data["object"] = object_name
    data["operate"] = operation
    if attribute:
        data.setdefault("attr", attribute)
    if mode:
        data.setdefault("mode", mode)
    if parts[-1] in ("fold", "unfold"):
        data["mode"] = parts[-1]
    if parts[-1] in ("next", "prev"):
        data["mode"] = parts[-1]
    if object_name == "steering_wheel" and mode == "heating":
        if parts[-1] in ("open", "on"):
            data["operate"] = "set"
            data["enabled"] = True
        elif parts[-1] in ("close", "off"):
            data["operate"] = "set"
            data["enabled"] = False

    for source in ("temp", "temperature", "level", "brightness"):
        if source in data and "value" not in data:
            data["value"] = data[source]
            break

    return {
        "domain": "car_control" if object_name != "media" else "media",
        "intent": intent_name,
        "data": data,
    }


class EdgeCallExecutor:
    """Translate an EdgeCall to a VAL command and return Agent response semantics."""

    def __init__(self, val: VAL):
        self.val = val

    def execute(self, call) -> agent_pb2.ExecuteResponse:
        intent_name = call.intent.name
        structured = _to_structured(intent_name, dict(call.intent.slots))
        if structured is None:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.FAILED,
                error=common_pb2.ErrorInfo(
                    code="invalid_request",
                    message=f"unsupported edge intent: {intent_name}",
                ),
            )

        obj = structured["data"]["object"]
        confirmed = call.meta.get("confirmed", "").lower() == "true"
        if self.val._need_confirm(obj) and not confirmed:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
                speech="这项操作可能影响车辆安全，请确认是否继续。",
                follow_up="说“确认”后我再执行。",
            )

        answer_length = call.meta.get("answer_length", "short")
        ok, speech = self.val.execute(
            structured, answer_length=answer_length)
        if not ok:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.REJECTED,
                speech=speech,
                error=common_pb2.ErrorInfo(
                    code="safety_gated",
                    message=speech,
                ),
            )

        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=speech,
            data=_struct({"intent": intent_name, "executed": True}),
        )

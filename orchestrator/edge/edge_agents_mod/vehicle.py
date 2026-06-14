"""端侧车控 Agent。经 VAL 执行车控指令。

Phase 1 从 edge_agents.py 拆分独立，可独立测试。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from val import VAL

# 车控意图白名单（覆盖全部车控对象）
VEHICLE_INTENTS = {
    "hvac.set", "hvac.on", "hvac.off", "hvac.inc", "hvac.dec",
    "window.open", "window.close", "window.set",
    "seat.heating.on", "seat.heating.off", "seat.ventilation.on", "seat.ventilation.off",
    "seat.massage.on", "seat.massage.off", "seat.lumbar_support.on", "seat.lumbar_support.off",
    "sunroof.open", "sunroof.close", "sunroof.set",
    "sunshade.open", "sunshade.close", "sunshade.set",
    "trunk.open", "trunk.close",
    "door_lock.open", "door_lock.close",
    "fuel_tank_cover.open", "fuel_tank_cover.close",
    "charging_port.open", "charging_port.close",
    "ambient_light.on", "ambient_light.off",
    "headlight.on", "headlight.off",
    "wiper.on", "wiper.off", "wiper.speed.set", "wiper.speed.inc", "wiper.speed.dec",
    "rear_view_mirror.fold", "rear_view_mirror.unfold",
    "fragrance.on", "fragrance.off", "fragrance.set",
    "steering_wheel.heating.open", "steering_wheel.heating.close",
    "steering_wheel.height.set", "steering_wheel.height.inc", "steering_wheel.height.dec",
    "energy_recovery.set", "energy_recovery.inc", "energy_recovery.dec",
    "lane_departure_assistance.open", "lane_departure_assistance.close",
    "lane_assistance.open", "lane_assistance.close",
    "scene_mode.set", "power_mode.set",
    "screen.brightness.set", "screen.brightness.inc", "screen.brightness.dec",
    "aircon.wind_speed.set", "aircon.wind_speed.inc", "aircon.wind_speed.dec",
    "aircon.inc", "aircon.dec",
    "tire_pressure.query", "dashcam.open", "dashcam.close",
    "accompany_home.open", "accompany_home.close",
    "volume.set", "volume.inc", "volume.dec",
}


class VehicleAgent:
    def __init__(self, val: VAL):
        self.val = val

    def can_handle(self, intent_name: str) -> bool:
        return intent_name in VEHICLE_INTENTS

    def execute(self, intent: dict) -> tuple[str, dict | None]:
        name = intent["name"]
        slots = intent["slots"]
        ok, msg = self.val.execute(name, slots)
        action = {
            "type": "vehicle.control",
            "payload": {"command": name, **slots},
            "require_confirm": False,
        }
        return msg, (action if ok else None)

"""端侧车控 Agent。经 VAL 执行车控指令。

Phase 1 从 edge_agents.py 拆分独立，可独立测试。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from val import VAL

# 车控意图白名单
VEHICLE_INTENTS = {"hvac.set", "hvac.on", "hvac.off", "window.open", "window.close"}


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

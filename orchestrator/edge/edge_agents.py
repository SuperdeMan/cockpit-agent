"""端侧核心 Agent（车控/媒体）的本地执行。经 VAL 下发，产出话术与动作。"""
from __future__ import annotations

from val import VAL


def edge_execute(intent: dict, val: VAL) -> tuple[str, dict | None]:
    name = intent["name"]
    slots = intent["slots"]
    ok, msg = val.execute(name, slots)
    domain = name.split(".")[0]
    action_type = "vehicle.control" if domain in ("hvac", "window") else "media.control"
    action = {
        "type": action_type,
        "payload": {"command": name, **slots},
        "require_confirm": False,
    }
    return msg, (action if ok else None)

"""端侧 Agent 调度器。Phase 1：委托给独立的 vehicle/media Agent 模块。

保留 edge_execute() 作为向后兼容入口。
"""
from __future__ import annotations
from val import VAL
from edge_agents_mod.vehicle import VehicleAgent
from edge_agents_mod.media import MediaAgent


_vehicle: VehicleAgent | None = None
_media: MediaAgent | None = None


def _get_agents(val: VAL):
    global _vehicle, _media
    if _vehicle is None:
        _vehicle = VehicleAgent(val)
        _media = MediaAgent(val)
    return _vehicle, _media


def edge_execute(intent: dict, val: VAL) -> tuple[str, dict | None]:
    """端侧执行入口（向后兼容）。委托给独立 Agent 模块。"""
    vehicle, media = _get_agents(val)
    name = intent["name"]

    if vehicle.can_handle(name):
        return vehicle.execute(intent)
    if media.can_handle(name):
        return media.execute(intent)

    return "暂不支持该端侧指令", None

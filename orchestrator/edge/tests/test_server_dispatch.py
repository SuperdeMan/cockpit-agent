"""Edge orchestrator action-dispatch guard (R1 double-fire) and confirm routing (R8)."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf import struct_pb2

from cockpit.orchestrator.v1 import orchestrator_pb2
from cockpit.common.v1 import common_pb2

from server import EdgeOrchestratorServicer


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


def _final_with_action(speech: str, payload: dict):
    action = common_pb2.AgentAction(
        type="vehicle.control", payload=_struct(payload), require_confirm=False)
    final = orchestrator_pb2.FinalResult(speech=speech)
    final.actions.append(action)
    return orchestrator_pb2.HandleEvent(final=final)


def test_dispatch_skips_edge_executed_action():
    """R1 防双发：带 _origin=edge_val 的动作已在车端执行，仅展示不再经 VAL 下发。"""
    srv = EdgeOrchestratorServicer()
    event = _final_with_action(
        "已为您调高空调温度，当前25度",
        {"command": "aircon.inc", "_origin": "edge_val"})

    out = srv._dispatch_cloud_actions(event)

    # speech 未被 legacy VAL 改写成"暂不支持该控制指令"；动作仍保留用于展示
    assert out.final.speech == "已为您调高空调温度，当前25度"
    assert len(out.final.actions) == 1


def test_dispatch_still_executes_unmarked_cloud_action():
    """无 _origin 标记的车控动作（假想云端 agent 直产）仍走 VAL —— 守规划/执行分离。"""
    srv = EdgeOrchestratorServicer()
    event = _final_with_action("placeholder", {"command": "hvac.on"})

    out = srv._dispatch_cloud_actions(event)

    assert srv.val.state["hvac_on"] is True          # VAL 确实执行了
    assert out.final.speech != "placeholder"          # speech 被 VAL 结果替换


def test_confirm_required_helper_flags_dangerous_objects():
    """R8：危险对象需二次确认（不走本地秒回）。"""
    srv = EdgeOrchestratorServicer()
    for obj in ("trunk", "door_lock", "fuel_tank_cover", "charging_port"):
        assert srv._confirm_required({"data": {"object": obj}}) is True
    assert srv._confirm_required({"data": {"object": "aircon"}}) is False
    assert srv._confirm_required({"data": {"object": "window"}}) is False
    assert srv._confirm_required(None) is False


def _drive(srv, text):
    req = orchestrator_pb2.HandleRequest(text=text, session_id="s1", meta={})

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    return asyncio.run(run())


def test_dangerous_intent_routes_to_cloud_not_executed_locally(monkeypatch):
    """R8：'打开后备箱'（require_confirm）不再本地秒回，落云端走二次确认。"""
    srv = EdgeOrchestratorServicer()
    seen = {}

    async def fake_cloud_handle(req):
        seen["text"] = req.text
        final = orchestrator_pb2.FinalResult(
            speech="这项操作可能影响车辆安全，请确认是否继续。", need_confirm=True)
        yield orchestrator_pb2.HandleEvent(final=final)

    monkeypatch.setattr(srv.cloud, "handle", fake_cloud_handle)

    events = _drive(srv, "打开后备箱")

    assert "text" in seen                          # 路由到了云端
    assert srv.val.state.get("trunk") != "open"     # 本地未执行危险动作
    assert events and events[-1].final.need_confirm is True


def test_plain_local_intent_still_fast_path(monkeypatch):
    """非危险动作仍端侧秒回，不上云（不回归）。"""
    srv = EdgeOrchestratorServicer()
    seen = {}

    async def fake_cloud_handle(req):
        seen["text"] = req.text
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="cloud"))

    monkeypatch.setattr(srv.cloud, "handle", fake_cloud_handle)

    events = _drive(srv, "打开空调")

    assert "text" not in seen                        # 没上云
    assert srv.val.state["hvac_on"] is True           # 本地执行了


def test_mixed_route_keeps_cloud_context_groups(monkeypatch):
    """导航偏好和歌手限定必须跟主意图一起上云，不能先本地空播音乐。"""
    srv = EdgeOrchestratorServicer()
    seen = {}

    async def fake_cloud_handle(req):
        seen["text"] = req.text
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端请求已处理"))

    monkeypatch.setattr(srv.cloud, "handle", fake_cloud_handle)

    events = _drive(
        srv,
        "空调帮我关上吧，然后氛围灯帮我调成红色，然后车窗开条缝，"
        "然后帮我导航去南京欢乐谷，走最快的那条路，"
        "然后再帮我播一首歌，周杰伦的",
    )

    assert seen["text"] == (
        "帮我导航去南京欢乐谷，走最快的那条路，"
        "再帮我播一首歌，周杰伦的"
    )

    local_final = next(ev.final for ev in events if ev.WhichOneof("event") == "final")
    commands = [action.payload["command"] for action in local_final.actions]
    # "开条缝" → 小开度 set（旧实现误判为全开 open，已修正）
    assert commands == ["hvac.off", "ambient_light.set", "window.set"]

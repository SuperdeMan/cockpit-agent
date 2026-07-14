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


def test_dispatch_scene_actions_execute_via_structured():
    """场景动作（ambient_light/volume/fragrance/seat.recline）经结构化路径真正执行，
    不再落只认 hvac/window/media 的 legacy 串路径返回'暂不支持'。复刻评审实测的端到端缺口修复。"""
    srv = EdgeOrchestratorServicer()
    cases = [
        ({"command": "ambient_light.set", "color": "warm_white", "brightness": "60"},
         "ambient_light", True),
        ({"command": "volume.set", "level": "0"}, "volume", 0),
        ({"command": "fragrance.on"}, "fragrance", True),
        ({"command": "seat.recline", "position": "front_left", "angle": "160"},
         "seat_recline", 160),
    ]
    for payload, state_key, expected in cases:
        out = srv._dispatch_cloud_actions(_final_with_action("placeholder", payload))
        assert srv.val.state.get(state_key) == expected, f"{payload['command']} 未生效"
        assert "暂不支持" not in out.final.speech, f"{payload['command']} 仍落 legacy 串路径"


def test_dispatch_scene_hvac_respects_temperature():
    """场景 hvac.set 经结构化路径采纳 temperature；VAL 不认的 mode(auto) 被丢弃，不致整条拒绝。
    （legacy 串路径读 args['temp']，会把场景的 temperature 丢掉。）"""
    srv = EdgeOrchestratorServicer()
    out = srv._dispatch_cloud_actions(_final_with_action(
        "placeholder", {"command": "hvac.set", "temperature": "22", "mode": "auto"}))
    assert srv.val.state["hvac_on"] is True
    assert srv.val.state["hvac_temp"] == 22
    assert "暂不支持" not in out.final.speech


def test_dispatch_cloud_action_now_passes_safety_gate():
    """云端车控经结构化路径后会过安全门控（legacy 串路径此前绕过）。
    低电量(<10%)下氛围灯属高耗电功能，应被门控拒绝、不点亮。"""
    srv = EdgeOrchestratorServicer()
    srv.val.state["battery"] = 5
    out = srv._dispatch_cloud_actions(
        _final_with_action("placeholder", {"command": "ambient_light.set", "color": "red"}))
    assert srv.val.state.get("ambient_light") is not True
    assert "电量过低" in out.final.speech


def _final_with_actions(speech: str, payloads: list[dict]):
    final = orchestrator_pb2.FinalResult(speech=speech)
    for p in payloads:
        final.actions.append(common_pb2.AgentAction(
            type="vehicle.control", payload=_struct(p), require_confirm=False))
    return orchestrator_pb2.HandleEvent(final=final)


def test_multi_action_keeps_cloud_summary_speech():
    """多动作（场景激活）保留云端总结话术——不能被最后一条动作的 VAL 通用应答顶成"好的"。

    旧实现循环内逐条覆盖 new_speech：场景末尾追加的 scene_mode.set 应答会把
    "已为您开启露营模式" 冲掉（2026-07-14 真栈 e2e 实测命中）。
    """
    srv = EdgeOrchestratorServicer()
    out = srv._dispatch_cloud_actions(_final_with_actions(
        "已为您开启露营模式。",
        [{"command": "ambient_light.set", "brightness": "30"},
         {"command": "hvac.set", "temperature": "22"},
         {"command": "scene_mode.set", "mode": "camping"}]))

    assert out.final.speech == "已为您开启露营模式。"
    assert srv.val.state["hvac_temp"] == 22            # 动作照常执行
    assert srv.val.state["scene_mode"] == "camping"


def test_multi_action_rejection_is_not_buried_by_later_success():
    """D10 真缺陷：中间某条被安全门控拒绝、后续成功，旧实现会把拒绝话术盖掉——失败对用户
    完全静默。现在拒绝原因必须浮出来，和云端总结一起播。"""
    srv = EdgeOrchestratorServicer()
    srv.val.state["battery"] = 5                        # 低电量：氛围灯属高耗电，会被门控

    out = srv._dispatch_cloud_actions(_final_with_actions(
        "已为您开启午休模式。",
        [{"command": "ambient_light.set", "brightness": "10"},   # ← 被拒
         {"command": "hvac.set", "temperature": "24"},           # ← 成功（后发）
         {"command": "scene_mode.set", "mode": "nap"}]))

    assert "电量过低" in out.final.speech, "被拒的动作不能被后续成功静默掉"
    assert "已为您开启午休模式" in out.final.speech, "成功的部分也要交代"
    assert srv.val.state.get("ambient_light") is not True


def test_media_control_action_now_dispatched():
    """P1.4：云端回流的 media.control 也经 VAL 结构化流水线执行。

    此前 `_dispatch_cloud_actions` 只认 vehicle.control，媒体动作静默丢弃——浪漫模式的
    「舒缓音乐」写在 description 里却从来没响过。VAL 早已建模 media 对象、
    `edge_call.action_type_for` 也早已映射到 media.control，只差回流分发这一类。
    """
    srv = EdgeOrchestratorServicer()
    final = orchestrator_pb2.FinalResult(speech="已为您开启浪漫模式。")
    final.actions.append(common_pb2.AgentAction(
        type="media.control", payload=_struct({"command": "media.play"}),
        require_confirm=False))
    out = srv._dispatch_cloud_actions(orchestrator_pb2.HandleEvent(final=final))

    assert srv.val.state["media"] == "playing"
    assert "暂不支持" not in out.final.speech


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


def test_gated_action_not_emitted_in_multi_intent():
    """安全门控拒绝的动作不下发到 final.actions，且门控话术只播报一次。

    回归用户反馈：低电量下"氛围灯/座椅通风"被门控，却仍把 vehicle.control 动作回传，
    HMI 显示成"已执行"，与"已禁用"自相矛盾；多个被拦动作还各报一次"电量过低"。
    """
    srv = EdgeOrchestratorServicer()
    srv.val.state["battery"] = 5  # 低电量：氛围灯/座椅通风将被门控

    events = _drive(srv, "氛围灯调成橙色，座椅通风打开")
    final = next(ev.final for ev in events if ev.WhichOneof("event") == "final")

    # 播报门控原因，但不下发任何车控动作
    assert "电量" in final.speech
    assert all(not a.type.startswith("vehicle.control") for a in final.actions), \
        f"被门控的动作不应下发: {[a.type for a in final.actions]}"
    # 相邻重复的门控话术已去重，只出现一次
    assert final.speech.count("电量过低") == 1


def test_aircon_not_gated_at_low_battery_in_multi_intent():
    """空调不再被低电量门控：低电量下"空调调到23度"应正常执行并下发动作。"""
    srv = EdgeOrchestratorServicer()
    srv.val.state["battery"] = 5

    events = _drive(srv, "空调调到23度，座椅通风打开")
    final = next(ev.final for ev in events if ev.WhichOneof("event") == "final")

    assert srv.val.state.get("hvac_temp") == 23                 # 空调真的设了
    cmds = [a.payload["command"] for a in final.actions]
    assert "hvac.set" in cmds                                    # 空调动作下发
    assert all("seat" not in c for c in cmds)                   # 座椅通风仍被门控、不下发


def test_climate_feeling_cold_raises_temp_lowers_wind():
    """『感觉冷，把空调温度和风速都调一下』→ 温度+1、风速-1（暖一点）。"""
    srv = EdgeOrchestratorServicer()
    srv.val.state["hvac_temp"] = 22
    srv.val.state["hvac_wind_speed"] = 5

    events = _drive(srv, "我感觉有点冷帮我把空调温度和风速都调一下")
    final = next(ev.final for ev in events if ev.WhichOneof("event") == "final")

    assert srv.val.state["hvac_temp"] == 23           # 温度调高一点
    assert srv.val.state["hvac_wind_speed"] == 4       # 风速调小一点
    cmds = [a.payload["command"] for a in final.actions]
    assert "aircon.inc" in cmds and "aircon.wind_speed.dec" in cmds
    # 话术要明确反馈温度+风速都调了（不再是模糊的"好的"）
    assert "度" in final.speech and "风速" in final.speech


def test_climate_feeling_hot_lowers_temp_raises_wind():
    """『有点热，空调温度和风速都调一下』→ 温度-1、风速+1（凉一点）。"""
    srv = EdgeOrchestratorServicer()
    srv.val.state["hvac_temp"] = 26
    srv.val.state["hvac_wind_speed"] = 3

    _drive(srv, "有点热，空调温度和风速都调一下")

    assert srv.val.state["hvac_temp"] == 25
    assert srv.val.state["hvac_wind_speed"] == 4

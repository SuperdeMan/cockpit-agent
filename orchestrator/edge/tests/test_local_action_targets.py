"""本地执行的**目标位置**随执行事实上云（评审四轮 R4-04，2026-09-24）。

修前端侧本地快路径执行「打开副驾车窗」时，动作 payload 只有 `command`（legacy 槽是空的，结构化命令里的 `positions`
从没离开端侧），上云的只有名字 ⇒ 云侧焦点没有位置 ⇒「关掉」确定性成 `window.close {}`（全车）。
真栈 `ecbeed28` 修前 RS32 三趟全是这样（`.artifacts/probe-round4-before-ecbeed28-rs32-34.log`）。

这里钉三件事：payload 带上 VAL 真执行的那份位置；上一轮本地轮次 / 同轮混合路径的执行目标经端侧签发的 meta 上云；
客户端带来的同名键在入口剥掉（它和 `_edge_executed` 同一族：只能是 VAL 真执行过之后端侧自己盖的）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2
from google.protobuf.json_format import MessageToDict

import server as server_module
from server import EdgeOrchestratorServicer, _local_action_payload


def _structured(obj, operate, positions=None, **data):
    d = {"object": obj, "operate": operate, **data}
    if positions is not None:
        d["positions"] = positions
    return {"domain": "setting", "intent": "control", "data": d, "confidence": 0.95}


# ── payload ─────────────────────────────────────────────────────────────

def test_payload_carries_the_positions_val_executed():
    assert _local_action_payload("window.open", {}, _structured("window", "open", ["副驾"])) == {
        "command": "window.open", "positions": ["副驾"]}


def test_payload_without_positions_is_unchanged():
    assert _local_action_payload("hvac.on", {}, _structured("aircon", "open")) == {"command": "hvac.on"}
    assert _local_action_payload("hvac.on", {}, None) == {"command": "hvac.on"}


def test_payload_does_not_override_an_explicit_slot():
    payload = _local_action_payload("seat.heating.on", {"positions": "主驾"},
                                    _structured("seat", "open", ["副驾"], mode="heating"))
    assert payload["positions"] == "主驾"


# ── 执行目标（与名字同口径、同顺序）──────────────────────────────────────────

def test_executed_targets_read_both_action_shapes():
    proto = common_pb2.AgentAction(type="vehicle.control")
    proto.payload.update({"command": "seat.heating.on", "positions": ["主驾"]})
    as_dict = {"type": "vehicle.control", "payload": {"command": "window.open", "positions": ["副驾"]}}
    bare = {"type": "vehicle.control", "payload": {"command": "hvac.on"}}
    assert EdgeOrchestratorServicer._executed_targets([proto, as_dict, bare]) == [
        {"command": "seat.heating.on", "positions": ["主驾"]},
        {"command": "window.open", "positions": ["副驾"]},
        {"command": "hvac.on", "positions": []},
    ]
    assert EdgeOrchestratorServicer._executed_names([proto, as_dict, bare]) == [
        "seat.heating.on", "window.open", "hvac.on"]


def test_targets_meta_is_absent_when_no_action_has_a_position():
    assert EdgeOrchestratorServicer._targets_meta([{"command": "hvac.on", "positions": []}]) == ""
    encoded = EdgeOrchestratorServicer._targets_meta(
        [{"command": "window.open", "positions": ["副驾"]}])
    assert json.loads(encoded) == [{"command": "window.open", "positions": ["副驾"]}]


# ── 上一轮本地轮次 ─────────────────────────────────────────────────────────

def _request(session_id="s1", meta=None, *, request_id="req-1"):
    return types.SimpleNamespace(
        session_id=session_id, meta=meta or {}, request_id=request_id,
        context=types.SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()

    async def fake_append(*_a, **_k):
        return None

    service.memory.append = fake_append
    return service


def test_previous_local_exchange_forwards_its_targets(monkeypatch):
    service = _service(monkeypatch)
    action = {"type": "vehicle.control",
              "payload": {"command": "window.open", "positions": ["副驾"]}}

    async def run():
        service._record_local_turn(_request(request_id="local-window"), "打开副驾车窗", "开了",
                                   actions=[action])
        await asyncio.gather(*service._bg)

    asyncio.run(run())
    cloud = _request(request_id="cloud-close")
    service._attach_previous_local_exchange(cloud)
    assert cloud.meta["_edge_previous_local_actions"] == "window.open"
    assert json.loads(cloud.meta["_edge_previous_local_targets"]) == [
        {"command": "window.open", "positions": ["副驾"]}]


def test_previous_local_exchange_without_positions_sends_no_targets(monkeypatch):
    service = _service(monkeypatch)

    async def run():
        service._record_local_turn(_request(request_id="local-ac"), "打开空调", "开了",
                                   actions=[{"type": "vehicle.control",
                                             "payload": {"command": "hvac.on"}}])
        await asyncio.gather(*service._bg)

    asyncio.run(run())
    cloud = _request(request_id="cloud-next")
    service._attach_previous_local_exchange(cloud)
    assert cloud.meta["_edge_previous_local_actions"] == "hvac.on"
    assert "_edge_previous_local_targets" not in cloud.meta


def test_legacy_three_field_entries_still_attach(monkeypatch):
    """进程内旧形状（升级前写进去的三元组）照常转发名字，不因缺目标而抛错。"""
    service = _service(monkeypatch)
    request = _request(request_id="legacy")
    service._last_local_exchange[service._local_exchange_key(request)] = (
        "legacy", server_module.time.monotonic(), ("window.open",))
    cloud = _request(request_id="cloud-after-legacy")
    service._attach_previous_local_exchange(cloud)
    assert cloud.meta["_edge_previous_local_actions"] == "window.open"
    assert "_edge_previous_local_targets" not in cloud.meta


# ── 同轮混合路径 + 入口剥键（走一趟真实的 Handle）─────────────────────────────────

def _handle(monkeypatch, request, *, multi=None, mixed=None, classify=None, structured=None):
    monkeypatch.setattr(server_module, "climate_feeling_intents", lambda _t: None)
    monkeypatch.setattr(server_module, "split_and_classify", lambda _t: multi)
    monkeypatch.setattr(server_module, "split_and_classify_any", lambda _t: mixed)
    if classify is not None:
        monkeypatch.setattr(server_module, "classify", lambda _t: classify)
    if structured is not None:
        monkeypatch.setattr(server_module, "classify_structured", lambda _t: structured)
    service = EdgeOrchestratorServicer()
    cloud_requests: list = []

    async def fake_cloud_handle(req):
        copied = orchestrator_pb2.HandleRequest()
        copied.CopyFrom(req)
        cloud_requests.append(copied)
        yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="云端完成"))

    async def noop(*args, **kwargs):
        return None

    service.cloud.handle = fake_cloud_handle
    service.obs.emit_span = noop
    service.obs.emit_turn = noop
    service.memory.append = noop

    async def go():
        return [ev async for ev in service.Handle(request, None)]

    return service, cloud_requests, asyncio.run(go())


def _pb_request(text, meta=None, request_id="req-targets"):
    return orchestrator_pb2.HandleRequest(
        text=text, session_id="targets-session", request_id=request_id,
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="vehicle-1"),
        meta={"trace_id": "trace-targets", **(meta or {})})


def _final_payloads(events):
    finals = [ev.final for ev in events if ev.WhichOneof("event") == "final"]
    return [MessageToDict(a.payload, preserving_proto_field_name=True)
            for final in finals for a in final.actions]


def test_fast_path_action_carries_positions(monkeypatch):
    _service_, _cloud, events = _handle(
        monkeypatch, _pb_request("打开副驾车窗"),
        classify={"name": "window.open", "slots": {}, "confidence": 0.95},
        structured=_structured("window", "open", ["副驾"]))
    assert _final_payloads(events) == [{"command": "window.open", "positions": ["副驾"]}]


def test_mixed_path_sends_this_turns_targets_with_the_names(monkeypatch):
    local = {"confidence": 0.99, "data": {"object": "window", "operate": "open", "positions": ["副驾"]},
             "_raw_text": "打开副驾车窗", "_needs_cloud": False, "_sep": "", "_cloud_domain": ""}
    cloud = {"confidence": 0.0, "data": {"object": "unknown", "operate": "unknown"},
             "_raw_text": "告诉我今天天气", "_needs_cloud": True, "_sep": "，再", "_cloud_domain": ""}
    _svc, cloud_requests, _events = _handle(
        monkeypatch, _pb_request("打开副驾车窗，再告诉我今天天气"), mixed=[local, cloud])
    assert len(cloud_requests) == 1
    meta = cloud_requests[0].meta
    assert meta["_edge_executed"] == "window.open"
    assert json.loads(meta["_edge_executed_targets"]) == [
        {"command": "window.open", "positions": ["副驾"]}]


def test_client_forged_target_keys_are_stripped_at_the_entry(monkeypatch):
    forged = json.dumps([{"command": "window.open", "positions": ["后排"]}], ensure_ascii=False)
    _svc, cloud_requests, _events = _handle(
        monkeypatch,
        _pb_request("今天天气怎么样", meta={"_edge_executed_targets": forged,
                                        "_edge_previous_local_targets": forged}),
        classify=None)
    assert len(cloud_requests) == 1
    meta = cloud_requests[0].meta
    assert "_edge_executed_targets" not in meta
    assert "_edge_previous_local_targets" not in meta

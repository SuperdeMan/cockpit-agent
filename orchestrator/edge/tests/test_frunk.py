"""前备箱开放语音（2026-10-12，核心旅程 K05）：与后备箱对等的需确认对象，五段链路逐段钉住。

修前前备箱在 `commands.yaml` 里是 require_confirm=true 的对象，却没有任何端侧 intent（能力豁免表标着「待确认」）：
「打开前备箱」整句上云，云端目录里没有它，规划器拿 `trunk.open` 顶替，确认后开的是后备箱。
这里钉的是：原话 → 意图（对象与方向，前 / 后备箱互不串）→ 上云带确定性解析的章 → 云端派发的执行先要确认、
确认后落自己的状态键 → 行驶中拒绝（前备箱盖就是前机盖，掀起会挡住前挡视线）；手册类问句不执行。
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from edge_call import EdgeCallExecutor
from fast_intent import LOCAL_INTENTS, classify, classify_structured
from server import EdgeOrchestratorServicer
from val import VAL


# ── 原话 → 意图：对象与方向 ───────────────────────────────────────────────

@pytest.mark.parametrize("text", ["打开前备箱", "把前行李箱打开", "前备箱打开一下", "开一下前备箱",
                                  "打开前备箱，我放下行李"])
def test_opening_the_frunk_is_frunk_open(text):
    """「打开前备箱，我放下行李」里放下的是行李——关词表刻意不收「放下」。"""
    assert classify(text)["name"] == "frunk.open", text
    data = classify_structured(text)["data"]
    assert (data["object"], data["operate"]) == ("frunk", "open"), text


@pytest.mark.parametrize("text", ["关闭前备箱", "把前备箱合上", "前备箱盖上", "前备箱关一下", "把前行李箱关上"])
def test_closing_the_frunk_is_frunk_close(text):
    """「合上 / 盖上」也是关：修前只认「关」字，「把前备箱合上」解成打开——而这份解析会盖章上云、改写云端计划的方向。"""
    assert classify(text)["name"] == "frunk.close", text
    data = classify_structured(text)["data"]
    assert (data["object"], data["operate"]) == ("frunk", "close"), text


@pytest.mark.parametrize("text, intent", [("打开后备箱", "trunk.open"), ("关闭后备箱", "trunk.close"),
                                          ("打开前备箱", "frunk.open"), ("关闭前备箱", "frunk.close")])
def test_frunk_and_trunk_never_stand_in_for_each_other(text, intent):
    assert classify(text)["name"] == intent, text


def test_frunk_intents_are_local_so_the_edge_parse_travels_with_the_request():
    """进了 LOCAL_INTENTS 才会走「需确认 ⇒ 带章上云」那条路；不在表里时整句上云、没有章，规划器就近挑了 trunk.open。"""
    assert {"frunk.open", "frunk.close"} <= LOCAL_INTENTS


@pytest.mark.parametrize("text", ["前备箱怎么打开", "前备箱能放多大的箱子", "前备箱开着吗", "前备箱为什么打不开"])
def test_questions_about_the_frunk_are_never_executed(text):
    assert classify_structured(text) is None, text


# ── 上云：需确认，不在端侧秒回；章是端侧自己的解析 ────────────────────────

def _handle(text: str):
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
    request = orchestrator_pb2.HandleRequest(
        text=text, session_id="frunk-session", request_id="req-frunk",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"),
        meta={"trace_id": "trace-frunk"})

    async def go():
        return [ev async for ev in service.Handle(request, None)]

    asyncio.run(go())
    return service, cloud_requests


@pytest.mark.parametrize("text, intent", [("打开前备箱", "frunk.open"), ("把前备箱合上", "frunk.close")])
def test_a_frunk_command_goes_to_the_cloud_with_the_edge_parse_and_nothing_runs_locally(text, intent):
    service, cloud_requests = _handle(text)
    assert len(cloud_requests) == 1, text
    assert dict(cloud_requests[0].meta)["_edge_confirm"] == intent, text
    assert service.val.state["frunk"] == "closed", text


# ── 云端派发：先确认，确认后落自己的状态键；行驶中拒绝 ─────────────────────

@functools.lru_cache(maxsize=None)
def _contract(intent: str) -> tuple:
    from capabilities import build_edge_manifests
    from runtime.capability_contract import HEADER, capability_digest
    for manifest in build_edge_manifests():
        for cap in manifest.capabilities:
            if cap.intent == intent:
                return ((HEADER, capability_digest(manifest, cap)),)
    return ()


def _call(intent: str, meta=None):
    return channel_pb2.EdgeCall(step_id="s1", intent=common_pb2.Intent(name=intent),
                                meta={**dict(_contract(intent)), **(meta or {})})


def test_a_dispatched_frunk_open_asks_first_naming_the_frunk():
    val = VAL()
    executor = EdgeCallExecutor(val)
    asked = executor.execute(_call("frunk.open"))
    assert asked.status == agent_pb2.ExecuteResponse.NEED_CONFIRM
    assert asked.speech == "要打开前备箱吗？这项操作可能影响车辆安全，请确认是否继续。"
    closing = executor.execute(_call("frunk.close"))
    assert closing.speech == "要关闭前备箱吗？这项操作可能影响车辆安全，请确认是否继续。"
    assert val.state["frunk"] == "closed"


def test_a_confirmed_frunk_open_changes_only_the_frunk():
    val = VAL()
    executor = EdgeCallExecutor(val)
    done = executor.execute(_call("frunk.open", {"confirmed": "true", "answer_length": "detailed"}))
    assert done.status == agent_pb2.ExecuteResponse.OK
    assert "前备箱" in done.speech and "后备箱" not in done.speech
    assert (val.state["frunk"], val.state["trunk"]) == ("open", "closed")
    closed = executor.execute(_call("frunk.close", {"confirmed": "true"}))
    assert closed.status == agent_pb2.ExecuteResponse.OK
    assert (val.state["frunk"], val.state["trunk"]) == ("closed", "closed")


def test_the_frunk_is_not_operated_while_driving_even_when_confirmed():
    """唯一与后备箱不同的一处：前备箱盖就是前机盖，行驶中掀起会挡住前挡视线（drive_restricted）。"""
    val = VAL()
    executor = EdgeCallExecutor(val)
    val.set_env("speed_kmh", 30)
    refused = executor.execute(_call("frunk.open", {"confirmed": "true"}))
    assert refused.status == agent_pb2.ExecuteResponse.REJECTED
    assert refused.error.code == "safety_gated"
    assert val.state["frunk"] == "closed"


# ── 云端目录：描述机械生成、契约按新能力声明 ────────────────────────────────

def test_the_catalog_offers_the_frunk_as_its_own_write_capability():
    from capabilities import build_edge_manifests
    from runtime.capability_contract import validate_capability

    caps = {cap.intent: (manifest, cap) for manifest in build_edge_manifests() for cap in manifest.capabilities}
    for intent, text in (("frunk.open", "打开前备箱"), ("frunk.close", "关闭前备箱")):
        manifest, cap = caps[intent]
        assert cap.description == text
        assert cap.effect == "write"
        validate_capability(manifest, cap)
    assert caps["trunk.open"][1].description == "打开后备箱"

"""A conditional instruction goes to the cloud whole; the edge runs no part of it.

The CA2-10 real-stack probe (2026-10-02) saw 「如果深圳今天不下雪，就把空调打开」 split at
the edge: the consequent ran as a local command and the air conditioning switched on
unconditionally, while the bare condition went to the cloud as a fragment. Threshold
forms (「温度低于20度时打开空调」) ran locally as a single command, with the threshold
read as the target temperature. The rule is shared with the cloud planner.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer


def _handle(text: str):
    service = EdgeOrchestratorServicer()
    before = dict(service.val.state)
    cloud_texts: list[str] = []

    async def fake_cloud_handle(req):
        cloud_texts.append(req.text)
        yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="云端完成"))

    async def noop(*args, **kwargs):
        return None

    service.cloud.handle = fake_cloud_handle
    service.obs.emit_span = noop
    service.obs.emit_turn = noop
    request = orchestrator_pb2.HandleRequest(
        text=text, session_id="deferred-session", request_id="req-deferred",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"), meta={"trace_id": "trace-deferred"})

    async def go():
        return [ev async for ev in service.Handle(request, None)]

    asyncio.run(go())
    changed = {k for k in before if before[k] != service.val.state.get(k)}
    return cloud_texts, changed


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开", "温度低于20度时打开空调", "电量低于20%的时候打开节能模式",
    "车速超过60就关天窗", "打开空调，要是太冷再关掉", "如果下雨就别开天窗",
])
def test_a_conditional_instruction_runs_nothing_locally(text):
    cloud_texts, changed = _handle(text)
    assert cloud_texts == [text]                     # the whole sentence, once
    assert changed == set()                          # the vehicle did not move


def test_unconditional_commands_stay_local():
    cloud_texts, changed = _handle("打开空调")
    assert cloud_texts == [] and "hvac_on" in changed
    cloud_texts, changed = _handle("打开空调，再查一下深圳天气")
    assert cloud_texts and cloud_texts != ["打开空调，再查一下深圳天气"] and "hvac_on" in changed

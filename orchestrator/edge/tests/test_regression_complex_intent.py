"""Regression coverage for mixed local/cloud grouping."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer


def _drive(servicer, text: str):
    request = orchestrator_pb2.HandleRequest(
        text=text,
        session_id="regression-complex-intent",
        meta={},
    )

    async def collect():
        return [event async for event in servicer.Handle(request, None)]

    return asyncio.run(collect())


def test_depart_command_stays_with_cloud_trip_and_hvac_remains_local(monkeypatch):
    servicer = EdgeOrchestratorServicer()
    seen = {}

    async def fake_cloud_handle(request):
        seen["text"] = request.text
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端请求已处理"))

    monkeypatch.setattr(servicer.cloud, "handle", fake_cloud_handle)

    events = _drive(
        servicer,
        "我想去上海那个像船形一样的那个、那个、那个地方，"
        "然后在那附近帮我找一个吃的，然后再看看那附近有没有停车场，"
        "啊，帮我找一个。然后现在帮我把车内的氛围灯调成绿色，"
        "然后空调调成二十三度，出发吧。",
    )

    assert "出发吧" in seen["text"]
    assert "空调调成二十三度" not in seen["text"]
    assert "氛围灯调成绿色" not in seen["text"]

    local_final = next(
        event.final for event in events
        if event.WhichOneof("event") == "final"
    )
    commands = [action.payload["command"] for action in local_final.actions]
    assert commands == ["ambient_light.set", "hvac.set"]
    assert local_final.actions[1].payload["temp"] == "23"

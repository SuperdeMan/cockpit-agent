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


def _mixed(monkeypatch, text: str) -> tuple[list[str], str]:
    """跑一句混合意图，返回（端侧真执行的命令, 上云的文本）。"""
    servicer = EdgeOrchestratorServicer()
    seen = {"text": ""}

    async def fake_cloud_handle(request):
        seen["text"] = request.text
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端请求已处理"))

    monkeypatch.setattr(servicer.cloud, "handle", fake_cloud_handle)
    events = _drive(servicer, text)
    commands = [action.payload["command"]
                for event in events if event.WhichOneof("event") == "final"
                for action in event.final.actions]
    return commands, seen["text"]


def test_independent_cloud_request_does_not_swallow_the_local_half(monkeypatch):
    """findings §1.2：本地那半条必须当场秒回，不能被后半句拖着整句上云。

    风险不在「答错」——云端两件事都会办；在于**端侧秒回退化成整句上云**，断网时
    这半条本地指令也跟着失效。
    """
    commands, cloud_text = _mixed(monkeypatch, "音量调小一点，提醒我八点开会")
    assert commands == ["volume.dec"]
    assert cloud_text == "提醒我八点开会"

    commands, cloud_text = _mixed(monkeypatch, "打开座椅加热，再找个充电站")
    assert commands == ["seat.heating.on"]
    assert cloud_text == "找个充电站"


def test_trailing_qualifier_still_travels_with_its_head(monkeypatch):
    """反方向：补语被从主意图上撕下来，才是真会答错的那一种。

    「周杰伦的」若不跟着「播一首歌」上云，端侧会先随机放一首歌——用户点的歌没放成，
    而云端收到的是一个没有主语的片段。
    """
    commands, cloud_text = _mixed(monkeypatch, "打开空调，帮我播一首歌，周杰伦的")
    assert commands == ["hvac.on"]
    assert cloud_text == "帮我播一首歌，周杰伦的"

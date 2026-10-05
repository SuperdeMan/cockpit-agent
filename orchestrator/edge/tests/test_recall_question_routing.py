"""点到媒体对象的回忆式问句去云端；端侧什么也不播。

Jev shadow 探针（2026-10-04）发了「你昨天看的那部电影叫什么来着」（乘客间对话）：端侧快路径把「电影」读成「播放视频」，
把模拟车的媒体切到了播放。句末「来着」问的是发生过或说过的事——是问句，不是指令（`runtime.question_shape.RECALL_TAILS`）。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
        text=text, session_id="recall-session", request_id="req-recall",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"), meta={"trace_id": "trace-recall"})

    async def go():
        return [ev async for ev in service.Handle(request, None)]

    asyncio.run(go())
    return cloud_texts, {k for k in before if before[k] != service.val.state.get(k)}


def test_a_recall_question_about_a_film_plays_nothing():
    for text in ("你昨天看的那部电影叫什么来着", "那首歌叫什么来着"):
        cloud_texts, changed = _handle(text)
        assert cloud_texts == [text] and changed == set(), text


def test_playing_a_film_still_runs_locally():
    cloud_texts, changed = _handle("播放电影")
    assert cloud_texts == [] and "media" in changed

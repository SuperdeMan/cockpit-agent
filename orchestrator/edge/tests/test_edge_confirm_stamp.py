"""需确认的命令整句上云时，车端把自己的确定性解析盖章成 `_edge_confirm`（评审四轮 §5.5 b，2026-09-25）。

云侧据此纠正计划里同一对象写步的方向（真栈 `5ca289c7` RS34：「关闭后备箱」被规划成 `trunk.open`）。
它只能是车端自己在这条路上盖的：客户端带来的同名键在入口剥掉，别的路（普通上云）不盖。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer


def _cloud_meta(text: str, meta: dict | None = None) -> dict:
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
        text=text, session_id="edge-confirm-session", request_id="req-edge-confirm",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="vehicle-1"),
        meta={"trace_id": "trace-edge-confirm", **(meta or {})})

    async def go():
        return [ev async for ev in service.Handle(request, None)]

    asyncio.run(go())
    assert len(cloud_requests) == 1, "需确认的命令 / 普通请求都应整句上云一次"
    return dict(cloud_requests[0].meta)


def test_a_confirm_required_command_carries_the_edges_parse_to_the_cloud():
    assert _cloud_meta("关闭后备箱")["_edge_confirm"] == "trunk.close"
    assert _cloud_meta("打开后备箱")["_edge_confirm"] == "trunk.open"


def test_the_stamp_is_the_edges_own_never_the_clients():
    """普通上云的请求不盖；客户端伪造的同名键在入口剥掉——否则网页 / 手机能改写云侧计划的方向。"""
    assert "_edge_confirm" not in _cloud_meta("今天深圳天气怎么样", meta={"_edge_confirm": "trunk.open"})
    assert _cloud_meta("关闭后备箱", meta={"_edge_confirm": "trunk.open"})["_edge_confirm"] == "trunk.close"

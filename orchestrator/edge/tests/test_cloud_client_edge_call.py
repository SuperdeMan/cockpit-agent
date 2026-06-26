"""The edge cloud client must service EdgeCall frames on the active request stream."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from cloud_client import CloudClient


class _Executor:
    def execute(self, call):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=f"executed {call.intent.name}",
        )


class _Stream:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.reads = [
            channel_pb2.DownFrame(
                correlation_id="hello",
                hello_ack=channel_pb2.HelloAck(ok=True),
            ),
            channel_pb2.DownFrame(
                correlation_id="edge-corr",
                edge_call=channel_pb2.EdgeCall(
                    step_id="s-edge",
                    intent=common_pb2.Intent(name="hvac.on"),
                ),
            ),
        ]

    async def write(self, frame):
        self.writes.append(frame)

    async def read(self):
        if self.reads:
            return self.reads.pop(0)
        return channel_pb2.DownFrame(
            correlation_id=self.writes[1].correlation_id,
            event=orchestrator_pb2.HandleEvent(
                final=orchestrator_pb2.FinalResult(speech="done"),
            ),
        )

    async def done_writing(self):
        self.closed = True


class _Stub:
    def __init__(self, stream):
        self.stream = stream

    def Connect(self):
        return self.stream


def test_edge_call_is_executed_and_result_written_on_same_stream():
    stream = _Stream()
    client = CloudClient(
        edge_call_executor=_Executor(),
        stub_factory=lambda _channel: _Stub(stream),
    )
    client._ch = object()
    request = orchestrator_pb2.HandleRequest(
        session_id="sess-1",
        context=common_pb2.ContextRef(vehicle_id="v1"),
    )

    async def collect():
        return [event async for event in client.handle(request)]

    events = asyncio.run(collect())

    assert events[-1].final.speech == "done"
    result_frames = [f for f in stream.writes if f.HasField("edge_result")]
    assert len(result_frames) == 1
    assert result_frames[0].correlation_id == "edge-corr"
    assert result_frames[0].edge_result.step_id == "s-edge"
    assert result_frames[0].edge_result.result.speech == "executed hvac.on"
    assert stream.closed is True


def test_request_corr_id_is_unique_per_call():
    """请求 corr_id 必须每次唯一：曾用 id(request)(内存地址)会被 GC 回收复用→不同请求
    拿到相同 corr_id→cloud-gateway 幂等(10min TTL)误判重复静默丢弃→客户端挂起。改 uuid4 根治。"""
    def _corr_of():
        stream = _Stream()
        client = CloudClient(
            edge_call_executor=_Executor(),
            stub_factory=lambda _channel: _Stub(stream),
        )
        client._ch = object()
        request = orchestrator_pb2.HandleRequest(
            session_id="sess-1", context=common_pb2.ContextRef(vehicle_id="v1"))

        async def collect():
            return [event async for event in client.handle(request)]

        asyncio.run(collect())
        reqs = [f for f in stream.writes if f.HasField("request")]
        return reqs[0].correlation_id

    c1, c2 = _corr_of(), _corr_of()
    assert c1 != c2, f"corr_id 撞车: {c1} == {c2}"
    assert c1.startswith("sess-1-")

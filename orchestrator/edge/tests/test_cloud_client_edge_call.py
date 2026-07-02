"""持久 EdgeCloudChannel 客户端（R2.3）：多路复用 / edge_call 服务 / corr_id 唯一 / 断连快速失败。

原逐请求模型（每 handle 一条 Connect()+hello+done_writing）已重写为进程内单条持久 bidi，
故断言从「stream.closed / 每请求 hello」改为「单 hello 多请求复用 / corr_id 路由 / 在途快速失败」。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from cloud_client import CloudClient


_EOF = object()


class _Executor:
    def execute(self, call):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=f"executed {call.intent.name}",
        )


class FakeStream:
    """模拟 grpc.aio bidi：write() 记录并对 hello/request 自动回帧；read() 阻塞取下行队列。"""

    def __init__(self, auto_final: bool = True):
        self.writes = []
        self._down: asyncio.Queue = asyncio.Queue()
        self._auto_final = auto_final
        self.cancelled = False

    async def write(self, frame):
        self.writes.append(frame)
        which = frame.WhichOneof("body")
        if which == "hello":
            self._down.put_nowait(channel_pb2.DownFrame(
                correlation_id=frame.correlation_id,
                hello_ack=channel_pb2.HelloAck(ok=True, heartbeat_sec=15)))
        elif which == "request" and self._auto_final:
            self._down.put_nowait(channel_pb2.DownFrame(
                correlation_id=frame.correlation_id,
                event=orchestrator_pb2.HandleEvent(
                    final=orchestrator_pb2.FinalResult(speech="done"))))

    async def read(self):
        item = await self._down.get()
        if item is _EOF:
            raise RuntimeError("stream closed")
        return item

    def cancel(self):
        self.cancelled = True
        self._down.put_nowait(_EOF)

    def inject(self, frame):
        self._down.put_nowait(frame)


class _Stub:
    def __init__(self, stream):
        self.stream = stream

    def Connect(self):
        return self.stream


def _req(session_id="sess-1"):
    return orchestrator_pb2.HandleRequest(
        session_id=session_id,
        context=common_pb2.ContextRef(vehicle_id="v1"),
    )


async def _drain(client, request):
    return [event async for event in client.handle(request)]


def test_multiplexed_requests_share_one_stream_each_get_own_final():
    """两并发请求复用同一条持久流（仅一次 hello），按 corr_id 各自收到 final。"""
    async def scenario():
        stream = FakeStream()
        client = CloudClient(edge_call_executor=_Executor(),
                             stub_factory=lambda _ch: _Stub(stream))
        client._ch = object()
        r1, r2 = await asyncio.gather(
            _drain(client, _req("sess-1")),
            _drain(client, _req("sess-2")),
        )
        await client.aclose()
        return r1, r2, stream

    r1, r2, stream = asyncio.run(scenario())
    assert r1[-1].final.speech == "done"
    assert r2[-1].final.speech == "done"
    hellos = [f for f in stream.writes if f.HasField("hello")]
    reqs = [f for f in stream.writes if f.HasField("request")]
    assert len(hellos) == 1                      # 持久：多请求只握手一次
    assert len(reqs) == 2
    assert reqs[0].correlation_id != reqs[1].correlation_id


def test_request_corr_id_is_unique_and_prefixed():
    """corr_id 每次唯一且带 session 前缀（cloud-gateway 幂等按 corr_id，撞车会挂起）。"""
    async def one():
        stream = FakeStream()
        client = CloudClient(stub_factory=lambda _ch: _Stub(stream))
        client._ch = object()
        await _drain(client, _req("sess-1"))
        await client.aclose()
        return [f for f in stream.writes if f.HasField("request")][0].correlation_id

    c1 = asyncio.run(one())
    c2 = asyncio.run(one())
    assert c1 != c2, f"corr_id 撞车: {c1} == {c2}"
    assert c1.startswith("sess-1-")


def test_edge_call_is_executed_and_result_written_same_corr():
    """云→端 edge_call 经 VAL executor 执行并回写 EdgeResult（同 corr_id、同步流）。"""
    async def scenario():
        stream = FakeStream()
        client = CloudClient(edge_call_executor=_Executor(),
                             stub_factory=lambda _ch: _Stub(stream))
        client._stream = stream
        down = channel_pb2.DownFrame(
            correlation_id="edge-corr",
            edge_call=channel_pb2.EdgeCall(
                step_id="s-edge", intent=common_pb2.Intent(name="hvac.on")),
        )
        await client._service_edge_call(down)
        return stream

    stream = asyncio.run(scenario())
    results = [f for f in stream.writes if f.HasField("edge_result")]
    assert len(results) == 1
    assert results[0].correlation_id == "edge-corr"
    assert results[0].edge_result.step_id == "s-edge"
    assert results[0].edge_result.result.speech == "executed hvac.on"


def test_inflight_request_fails_fast_on_disconnect():
    """断连时在途请求快速失败（抛错）→ 由上层 server.py 出降级话术。"""
    async def scenario():
        stream = FakeStream(auto_final=False)   # request 无 final → handle 阻塞等待
        client = CloudClient(stub_factory=lambda _ch: _Stub(stream))
        client._ch = object()

        async def consume():
            return [event async for event in client.handle(_req("sess-1"))]

        task = asyncio.create_task(consume())
        for _ in range(200):                    # 等 request 写出并在队列上阻塞
            if any(f.HasField("request") for f in stream.writes):
                break
            await asyncio.sleep(0.01)
        stream.cancel()                          # 断流 → read_loop 出错 → 在途快速失败
        with pytest.raises(RuntimeError):
            await task
        client._closing = True
        await client.aclose()

    asyncio.run(scenario())

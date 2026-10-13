"""SDK 的「记住…」当场写入（2026-10-13）：客户端只换 gRPC 桩、走真实的请求构造与响应转换（2026-10-04 的教训：
换掉整个内部调用客户端就验不到传输——当时丢了 `data`）；`Context` 带上本轮说话人与代际。"""
import asyncio
import os
import sys

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from _sdk.base import Context  # noqa: E402
from _sdk.clients import MemoryClient  # noqa: E402
from cockpit.memory.v1 import memory_pb2  # noqa: E402


class _Stub:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []

    async def RememberUtterance(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.error is not None:
            raise grpc.aio.AioRpcError(self.error, grpc.aio.Metadata(), grpc.aio.Metadata(), details="x")
        return self.reply


def _client(stub):
    client = MemoryClient(addr="memory:50053")
    client._stub = lambda: stub
    return client


def test_the_request_and_the_outcome_cross_the_wire_as_is():
    stub = _Stub(reply=memory_pb2.RememberUtteranceResponse(
        outcome="stored", stored_count=2, stored_texts=["用户的女儿叫小满"]))
    out = asyncio.run(_client(stub).remember_utterance(
        "u1", "记住，我女儿叫小满", occupant_id="occ-2", vehicle_id="v1", session_id="s1", memory_epoch="3.ab"))

    assert out == {"outcome": "stored", "stored_count": 2, "stored_texts": ["用户的女儿叫小满"]}
    req, timeout = stub.requests[0]
    assert (req.user_id, req.occupant_id, req.vehicle_id, req.session_id, req.text, req.memory_epoch) == (
        "u1", "occ-2", "v1", "s1", "记住，我女儿叫小满", "3.ab")
    assert timeout and timeout < 8             # 唯一调用方提醒 Agent 的步预算是 8 s


def test_a_timeout_is_not_reported_as_not_written():
    """客户端放弃之后 Memory 可能照样写完了：超时是 `timeout`（答「不确定」），不是 `unavailable`（答「没记下来」）。"""
    timed_out = asyncio.run(_client(_Stub(error=grpc.StatusCode.DEADLINE_EXCEEDED)).remember_utterance("u1", "记住我喜欢喝拿铁"))
    old_memory = asyncio.run(_client(_Stub(error=grpc.StatusCode.UNIMPLEMENTED)).remember_utterance("u1", "记住我喜欢喝拿铁"))

    assert timed_out["outcome"] == "timeout"
    assert old_memory["outcome"] == "unavailable"


class _RecordingMemory:
    def __init__(self):
        self.calls = []

    async def remember_utterance(self, user_id, text, **kw):
        self.calls.append((user_id, text, kw))
        return {"outcome": "stored", "stored_count": 1, "stored_texts": []}


def test_context_passes_the_speaker_and_the_epoch_observed_this_turn():
    mem = _RecordingMemory()
    ctx = Context("s1", "u1", "v1", mem, "occ-2", meta={"memory_epoch": "3.ab"})

    assert asyncio.run(ctx.remember_utterance("记住我喜欢喝拿铁"))["outcome"] == "stored"
    assert mem.calls == [("u1", "记住我喜欢喝拿铁", {"occupant_id": "occ-2", "vehicle_id": "v1",
                                                    "session_id": "s1", "memory_epoch": "3.ab"})]


def test_context_without_a_user_writes_nothing():
    mem = _RecordingMemory()
    ctx = Context("s1", "", "v1", mem, "occ-2")

    assert asyncio.run(ctx.remember_utterance("记住我喜欢喝拿铁"))["outcome"] == "off"
    assert mem.calls == []

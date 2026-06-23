"""SDK gRPC 客户端连接自愈：依赖（llm-gateway / memory）重启换 IP 后，
旧 channel 会卡在旧地址(UNAVAILABLE)直到 agent 重启。_reset_channel 让下次调用
重建 channel、重新解析 DNS，complete/get_context 命中 UNAVAILABLE 时自动重试一次。
"""
import asyncio

import grpc

from agents._sdk.clients import LLMClient, MemoryClient


def test_llm_reset_channel_clears_cache_for_redial():
    async def go():
        client = LLMClient(addr="llm-gateway:50052")
        client._channel()                   # 建立并缓存 channel（需事件循环）
        assert client._ch is not None
        await client._reset_channel()
        assert client._ch is None           # 下次 _channel() 会重建并重新解析 DNS
    asyncio.run(go())


def test_memory_reset_channel_clears_cache_for_redial():
    async def go():
        client = MemoryClient(addr="memory:50053")
        client._channel()
        assert client._ch is not None
        await client._reset_channel()
        assert client._ch is None
    asyncio.run(go())


def test_llm_complete_retries_once_on_unavailable_then_succeeds():
    client = LLMClient(addr="llm-gateway:50052")
    state = {"calls": 0, "resets": 0}

    class _Resp:
        content = "ok"

    class _Stub:
        async def Complete(self, req, timeout=None):
            state["calls"] += 1
            if state["calls"] == 1:
                raise grpc.aio.AioRpcError(
                    grpc.StatusCode.UNAVAILABLE, grpc.aio.Metadata(),
                    grpc.aio.Metadata(), details="connect refused")
            return _Resp()

    client._stub = lambda: _Stub()
    orig = client._reset_channel

    async def _reset():
        state["resets"] += 1
        await orig()

    client._reset_channel = _reset
    out = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))

    assert out == "ok"
    assert state["calls"] == 2          # 第一次 UNAVAILABLE，重试第二次成功
    assert state["resets"] == 1         # 重试前重建了 channel

"""语义打断：播报中有人插话，先单独问一次「是不是对助手说的」（设计 docs/design/2026-10-11-semantic-barge-in.md §4.1）。

为什么在网关、不走对话通道：网关每个连接同一时刻只有一个在飞请求，把插话那句当正式一轮发出去的那一刻，旧回答那一轮就被取消了；
两端发新一轮也会先停掉旧播报。所以先在这里问、判受话才发正式一轮。两端本来就直连本网关做 ASR / TTS / 声纹。

判据只有一份（`runtime/continuation_admission.py`，云端续问窗判定同一份）；这里只把它接到本网关自己的 `Complete`
（本机回环 gRPC）：缓存、`@fast` 档位、降级、限流与 obs.llm 和云端那一路逐字相同。
"""
from __future__ import annotations

import asyncio
import os
import time

from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from runtime.continuation_admission import continuation_timeout_s, judge_continuation
from runtime.grpcio import aio_channel

_channel = None


def _stub():
    """本网关自己的 LLM gRPC（懒连接、复用）。走完整的 `Complete`，不在 HTTP 这边另起一条模型调用。"""
    global _channel
    if _channel is None:
        _channel = aio_channel(f"127.0.0.1:{os.getenv('LLM_GATEWAY_PORT', '50052')}")
    return llm_pb2_grpc.LLMGatewayStub(_channel)


async def _loopback_complete(messages: list[dict], meta: dict) -> str:
    """与云端判定同形的请求：快档、温度 0、只要一个 JSON 对象（`orchestrator/cloud/engine.py::_admission_complete`）。"""
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
        model="@fast", temperature=0.0, max_tokens=32)
    for key, value in meta.items():
        if value:
            req.meta[key] = value
    resp = await _stub().Complete(req, timeout=continuation_timeout_s() + 1.0)
    return resp.content


async def judge(utterance: str, previous: str, name: str, *, meta: dict) -> tuple[bool | None, tuple, int]:
    """返回 (结论, 每次的判定, 耗时 ms)。结论 None = 判不出（模型失败 / 超时 / 解析不出），客户端按今天的路径处理。
    每一问各自受 `continuation_timeout_s()` 约束（同云端那一路）。"""
    async def bounded(messages):
        return await asyncio.wait_for(_loopback_complete(messages, meta), timeout=continuation_timeout_s())

    started = time.monotonic()
    try:
        verdict, votes = await judge_continuation(bounded, utterance, previous, name)
    except Exception:
        verdict, votes = None, ()
    return verdict, votes, round((time.monotonic() - started) * 1000)

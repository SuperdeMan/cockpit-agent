"""llm-gateway Embed 的共享出口（M5 P1 从 skills.py 提取）。

skills 语义预筛（guide `description` 余弦）与 exemplars 范例检索（历史话术余弦）共用
**同一个 stub、同一段失败冷却**——故障必须一起 fail-open：网关挂了两条通道各自重试，
等于把一次故障打成两次超时，规划轮延迟翻倍。与 registry 语义路由 / memory 画像同源
（经 llm-gateway，百炼 text-embedding-v4；数据飞轮 RFC §6「共用一个出口」）。

契约：`embed_texts()` 成功 → `(向量组, 模型名)`；不可用/超时/返回畸形 → `None`
（调用方该轮回落纯词法，冷却期内不重试）。**绝不抛异常、绝不堵规划。**
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time

logger = logging.getLogger("cloud.embedding")

_COOLDOWN_S = 30.0          # 失败冷却（防抖打日志、防雪崩重试）
_stub = None
_stub_loop = None           # stub 绑定的事件循环（grpc aio channel 是 loop-bound）
_fail_ts = 0.0


def reset_cooldown() -> None:
    """清失败冷却。测试与「网关刚修好想立刻恢复语义档」的运维动作用。"""
    global _fail_ts
    _fail_ts = 0.0


def in_cooldown() -> bool:
    return time.monotonic() - _fail_ts < _COOLDOWN_S


async def embed_texts(texts: list[str], timeout_s: float = 1.0
                      ) -> tuple[list[tuple[float, ...]], str] | None:
    """一次 Embed 调用。空输入/冷却期内/任何异常 → None（调用方回落词法）。"""
    global _stub, _stub_loop, _fail_ts
    if not texts or in_cooldown():
        return None
    try:
        from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
        # grpc aio channel 绑定创建它的事件循环：换了 loop（评测里 full/off 两趟各一次
        # asyncio.run；服务侧重启事件循环同理）继续用旧 stub 只会得到「Event loop is
        # closed」，然后被当成「Embed 不可用」静默回落词法——一个只在评测里现形、
        # 却会让 A/B 数据失真的坑。按 loop 重建。旧 channel 不显式 close：它绑定的 loop
        # 已经关了，跨 loop close 自己就会炸——交给 GC（服务侧仅重启时发生，评测侧每次
        # asyncio.run 一枚，量级无害；2026-07-30 评审记录在案的「有意不做」）。
        loop = asyncio.get_running_loop()
        if _stub is None or _stub_loop is not loop:
            from runtime.grpcio import aio_channel
            _stub = llm_pb2_grpc.LLMGatewayStub(
                aio_channel(os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")))
            _stub_loop = loop
        resp = await _stub.Embed(llm_pb2.EmbedRequest(texts=texts), timeout=timeout_s)
        vecs = [tuple(e.values) for e in resp.embeddings]
        # 少返/空向量都会让后续余弦静默算成 0（=语义通道假装工作却全不召回）——当异常处理
        if len(vecs) != len(texts) or any(not v for v in vecs):
            raise ValueError(f"embedding 返回异常（{len(vecs)}/{len(texts)}）")
        return vecs, str(resp.model_used or "")
    except Exception as e:
        _fail_ts = time.monotonic()
        logger.warning("Embed 不可用，调用方回落纯词法（%.0fs 冷却）: %s", _COOLDOWN_S, e)
        return None


def cos(a: tuple, b: tuple) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0

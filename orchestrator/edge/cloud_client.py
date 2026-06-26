"""端→云客户端：端侧编排器的慢路径上云通道。

架构 §8：端云通信走 EdgeCloudChannel 双向流（bidi 帧协议），
经 Cloud Gateway 到达 Cloud Planner。

Phase 1：逐请求 bidi 流（发 UpFrame_Request → 收 DownFrame_Event → 流结束）。
Phase 2：持久 bidi 长连 + 多路复用 + 断线重连（Go ChannelClient 的逻辑）。
"""
from __future__ import annotations
import os
import logging
import uuid

import grpc
from cockpit.channel.v1 import channel_pb2, channel_pb2_grpc
from cockpit.orchestrator.v1 import orchestrator_pb2

from runtime.grpcio import aio_channel

logger = logging.getLogger("edge.cloud_client")


class CloudClient:
    def __init__(self, edge_call_executor=None, stub_factory=None):
        self.addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
        self._ch: grpc.aio.Channel | None = None
        self._edge_calls = edge_call_executor
        self._stub_factory = stub_factory or channel_pb2_grpc.EdgeCloudChannelStub

    def _channel(self) -> grpc.aio.Channel:
        if self._ch is None:
            self._ch = aio_channel(self.addr)
        return self._ch

    async def handle(self, request):
        """通过 EdgeCloudChannel bidi 协议转发请求到云端，yield HandleEvent。"""
        stub = self._stub_factory(self._channel())
        stream = None
        try:
            # 建立 bidi 流
            stream = stub.Connect()
            # 发握手
            await stream.write(channel_pb2.UpFrame(
                correlation_id=f"{request.session_id}-hello",
                hello=channel_pb2.Hello(
                    vehicle_id=getattr(request.context, "vehicle_id", "v1") if hasattr(request, "context") and request.context else "v1",
                ),
            ))
            # 等 HelloAck
            ack = await stream.read()
            ha = ack.hello_ack
            if ha and not ha.ok:
                logger.warning("Cloud hello rejected: %s", ha.reason)
                return

            # 发请求。corr_id 必须全局唯一——cloud-gateway 按它做幂等去重(10min TTL)，
            # 撞车会被当重复静默丢弃致客户端挂起。曾用 id(request)(内存地址)，会被 GC 回收
            # 复用→不同请求拿到相同 id→corrID 撞车(典型坑)；改 uuid4 根治。
            corr_id = f"{request.session_id}-{uuid.uuid4().hex}"
            await stream.write(channel_pb2.UpFrame(
                correlation_id=corr_id,
                request=request,
            ))

            # 收事件直到 final
            while True:
                down = await stream.read()
                which = down.WhichOneof("body")
                if which == "edge_call":
                    if self._edge_calls is None:
                        logger.warning("Received edge_call without executor")
                        result = channel_pb2.EdgeResult(
                            step_id=down.edge_call.step_id,
                        )
                        result.result.status = 3  # FAILED
                    else:
                        result = channel_pb2.EdgeResult(
                            step_id=down.edge_call.step_id,
                            result=self._edge_calls.execute(down.edge_call),
                        )
                    await stream.write(channel_pb2.UpFrame(
                        correlation_id=down.correlation_id,
                        edge_result=result,
                    ))
                    continue
                if down.correlation_id != corr_id:
                    continue
                if which != "event":
                    continue
                evt = down.event
                if evt is None:
                    continue
                yield evt
                # final 帧后结束
                if evt.HasField("final"):
                    break

        except grpc.aio.AioRpcError as e:
            logger.warning("Cloud channel error: %s", e)
            raise
        finally:
            if stream is not None and hasattr(stream, "done_writing"):
                try:
                    await stream.done_writing()
                except (grpc.aio.AioRpcError, RuntimeError):
                    logger.debug("Cloud stream already closed", exc_info=True)

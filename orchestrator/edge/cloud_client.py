"""端→云客户端：端侧编排器的慢路径上云通道。

架构 §8：端云通信走 EdgeCloudChannel 双向流（bidi 帧协议），
经 Cloud Gateway 到达 Cloud Planner。

Phase 1：逐请求 bidi 流（发 UpFrame_Request → 收 DownFrame_Event → 流结束）。
Phase 2：持久 bidi 长连 + 多路复用 + 断线重连（Go ChannelClient 的逻辑）。
"""
from __future__ import annotations
import os
import logging

import grpc
from cockpit.channel.v1 import channel_pb2, channel_pb2_grpc
from cockpit.orchestrator.v1 import orchestrator_pb2

logger = logging.getLogger("edge.cloud_client")


class CloudClient:
    def __init__(self):
        self.addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
        self._ch: grpc.aio.Channel | None = None

    def _channel(self) -> grpc.aio.Channel:
        if self._ch is None:
            self._ch = grpc.aio.insecure_channel(self.addr)
        return self._ch

    async def handle(self, request):
        """通过 EdgeCloudChannel bidi 协议转发请求到云端，yield HandleEvent。"""
        stub = channel_pb2_grpc.EdgeCloudChannelStub(self._channel())
        try:
            # 建立 bidi 流
            stream = stub.Connect()
            # 发握手
            await stream.send(channel_pb2.UpFrame(
                correlation_id=f"{request.session_id}-hello",
                hello=channel_pb2.Hello(
                    vehicle_id=getattr(request.context, "vehicle_id", "v1") if hasattr(request, "context") and request.context else "v1",
                ),
            ))
            # 等 HelloAck
            ack = await stream.recv()
            ha = ack.hello_ack
            if ha and not ha.ok:
                logger.warning("Cloud hello rejected: %s", ha.reason)
                return

            # 发请求
            corr_id = f"{request.session_id}-{id(request)}"
            await stream.send(channel_pb2.UpFrame(
                correlation_id=corr_id,
                request=request,
            ))

            # 收事件直到 final
            while True:
                down = await stream.recv()
                if down.correlation_id != corr_id:
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

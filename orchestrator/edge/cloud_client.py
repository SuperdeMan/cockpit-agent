"""端→云客户端：把慢意图转发到 Cloud Gateway(实现 CloudPlanner)。"""
from __future__ import annotations
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc


class CloudClient:
    def __init__(self):
        self.addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")

    async def handle(self, request):
        channel = grpc.aio.insecure_channel(self.addr)
        stub = orchestrator_pb2_grpc.CloudPlannerStub(channel)
        async for event in stub.Handle(request):
            yield event

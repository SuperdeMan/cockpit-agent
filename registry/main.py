"""Agent Registry 启动入口。"""
import asyncio
import os

import grpc
from cockpit.registry.v1 import registry_pb2_grpc

from server import RegistryServicer


async def serve():
    port = int(os.getenv("REGISTRY_PORT", "50051"))
    server = grpc.aio.server()
    registry_pb2_grpc.add_RegistryServicer_to_server(RegistryServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[registry] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

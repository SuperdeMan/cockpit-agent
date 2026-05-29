"""Memory 服务启动入口。"""
import asyncio
import os

import grpc
from cockpit.memory.v1 import memory_pb2_grpc

from server import MemoryServicer


async def serve():
    port = int(os.getenv("MEMORY_PORT", "50053"))
    server = grpc.aio.server()
    memory_pb2_grpc.add_MemoryServicer_to_server(MemoryServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[memory] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

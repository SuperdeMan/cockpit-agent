"""Memory 服务启动入口。"""
import asyncio
import os

import grpc
from cockpit.memory.v1 import memory_pb2_grpc

from runtime.grpcio import aio_server, run_aio_server
from server import MemoryServicer


async def serve():
    port = int(os.getenv("MEMORY_PORT", "50053"))
    server = aio_server()
    memory_pb2_grpc.add_MemoryServicer_to_server(MemoryServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[memory] serving on :{port}", flush=True)
    await run_aio_server(server, name="memory")


if __name__ == "__main__":
    asyncio.run(serve())

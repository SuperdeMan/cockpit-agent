"""Memory 服务启动入口。"""
import asyncio
import os

import grpc
from cockpit.memory.v1 import memory_pb2_grpc

from observability import setup_structured_logging
from runtime.grpcio import aio_server, bind_port, run_aio_server

# 结构化日志：stdout JSON 带 trace/session + obs.log 上报（badcase 按 trace 检索）
setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="memory")

from server import MemoryServicer  # noqa: E402


async def serve():
    port = int(os.getenv("MEMORY_PORT", "50053"))
    server = aio_server()
    memory_pb2_grpc.add_MemoryServicer_to_server(MemoryServicer(), server)
    bind_port(server, f"[::]:{port}")
    await server.start()
    print(f"[memory] serving on :{port}", flush=True)
    await run_aio_server(server, name="memory")


if __name__ == "__main__":
    asyncio.run(serve())

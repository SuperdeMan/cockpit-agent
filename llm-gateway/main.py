"""LLM Gateway 启动入口。"""
import asyncio
import os

import grpc
from cockpit.llm.v1 import llm_pb2_grpc

from server import LLMGatewayServicer


async def serve():
    port = int(os.getenv("LLM_GATEWAY_PORT", "50052"))
    server = grpc.aio.server()
    llm_pb2_grpc.add_LLMGatewayServicer_to_server(LLMGatewayServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[llm-gateway] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

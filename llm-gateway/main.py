"""LLM Gateway 启动入口。提供 LLM 文本生成 + ASR 语音识别 + TTS 语音合成。"""
import asyncio
import os

import grpc
from cockpit.llm.v1 import llm_pb2_grpc, audio_pb2_grpc

from runtime.grpcio import aio_server, bind_port, run_aio_server
from server import LLMGatewayServicer, AudioServiceServicer
from http_server import start_http_server


async def serve():
    port = int(os.getenv("LLM_GATEWAY_PORT", "50052"))

    # gRPC server（内部服务间调用）
    server = aio_server()
    llm_pb2_grpc.add_LLMGatewayServicer_to_server(LLMGatewayServicer(), server)
    audio_pb2_grpc.add_AudioServiceServicer_to_server(AudioServiceServicer(), server)
    bind_port(server, f"[::]:{port}")
    await server.start()
    print(f"[llm-gateway] LLM + Audio(ASR/TTS) gRPC on :{port}", flush=True)

    # HTTP proxy（HMI 前端调用 ASR/TTS）
    await start_http_server()

    await run_aio_server(server, name="llm-gateway")


if __name__ == "__main__":
    asyncio.run(serve())

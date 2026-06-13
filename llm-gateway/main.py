"""LLM Gateway 启动入口。提供 LLM 文本生成 + ASR 语音识别 + TTS 语音合成。"""
import asyncio
import os

import grpc
from cockpit.llm.v1 import llm_pb2_grpc, audio_pb2_grpc

from server import LLMGatewayServicer, AudioServiceServicer
from http_server import start_http_server


async def serve():
    port = int(os.getenv("LLM_GATEWAY_PORT", "50052"))

    # gRPC server（内部服务间调用）
    server = grpc.aio.server()
    llm_pb2_grpc.add_LLMGatewayServicer_to_server(LLMGatewayServicer(), server)
    audio_pb2_grpc.add_AudioServiceServicer_to_server(AudioServiceServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[llm-gateway] LLM + Audio(ASR/TTS) gRPC on :{port}", flush=True)

    # HTTP proxy（HMI 前端调用 ASR/TTS）
    await start_http_server()

    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

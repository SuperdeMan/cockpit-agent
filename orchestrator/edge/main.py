"""Edge Orchestrator 启动入口。"""
import asyncio
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc

from server import EdgeOrchestratorServicer
from capabilities import register_edge_capabilities


async def serve():
    port = int(os.getenv("EDGE_ORCHESTRATOR_PORT", "50070"))
    server = grpc.aio.server()
    orchestrator_pb2_grpc.add_EdgeOrchestratorServicer_to_server(EdgeOrchestratorServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    try:
        await register_edge_capabilities()
    except Exception as exc:
        print(f"[edge-orchestrator] registry register failed (continuing): {exc}", flush=True)
    print(f"[edge-orchestrator] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

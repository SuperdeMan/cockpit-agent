"""Edge Orchestrator 启动入口。"""
import asyncio
import contextlib
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc

from server import EdgeOrchestratorServicer
from capabilities import register_edge_capabilities


async def serve():
    port = int(os.getenv("EDGE_ORCHESTRATOR_PORT", "50070"))
    server = grpc.aio.server()
    servicer = EdgeOrchestratorServicer()
    orchestrator_pb2_grpc.add_EdgeOrchestratorServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    state_task = asyncio.create_task(servicer.drain_state())
    await servicer.emit_snapshot()
    try:
        await register_edge_capabilities()
    except Exception as exc:
        print(f"[edge-orchestrator] registry register failed (continuing): {exc}", flush=True)
    print(f"[edge-orchestrator] serving on :{port}", flush=True)
    try:
        await server.wait_for_termination()
    finally:
        state_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state_task
        await servicer.obs.close()


if __name__ == "__main__":
    asyncio.run(serve())

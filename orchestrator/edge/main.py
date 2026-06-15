"""Edge Orchestrator 启动入口。"""
import asyncio
import contextlib
import json
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc

from server import EdgeOrchestratorServicer
from capabilities import register_edge_capabilities


async def _debug_subscription(servicer: EdgeOrchestratorServicer):
    url = os.getenv("NATS_URL", "")
    if not url:
        return

    connection = None
    try:
        import nats

        connection = await nats.connect(
            url,
            connect_timeout=2,
            max_reconnect_attempts=3,
        )

        async def apply(message):
            try:
                payload = json.loads(message.data.decode())
                servicer.apply_debug(
                    payload.get("key", ""),
                    payload.get("value"),
                )
            except Exception:
                pass

        await connection.subscribe("obs.debug.vehicle.set", cb=apply)
        await asyncio.Future()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(
            f"[edge-orchestrator] debug subscribe skipped: {exc}",
            flush=True,
        )
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.drain()


async def serve():
    port = int(os.getenv("EDGE_ORCHESTRATOR_PORT", "50070"))
    server = grpc.aio.server()
    servicer = EdgeOrchestratorServicer()
    orchestrator_pb2_grpc.add_EdgeOrchestratorServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    state_task = asyncio.create_task(servicer.drain_state())
    debug_task = asyncio.create_task(_debug_subscription(servicer))
    await servicer.emit_snapshot()
    try:
        await register_edge_capabilities()
    except Exception as exc:
        print(f"[edge-orchestrator] registry register failed (continuing): {exc}", flush=True)
    print(f"[edge-orchestrator] serving on :{port}", flush=True)
    try:
        await server.wait_for_termination()
    finally:
        for task in (state_task, debug_task):
            task.cancel()
        for task in (state_task, debug_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await servicer.obs.close()


if __name__ == "__main__":
    asyncio.run(serve())

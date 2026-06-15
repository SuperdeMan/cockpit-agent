"""Agent Registry 启动入口。"""
import asyncio
import contextlib
import os

import grpc
from cockpit.registry.v1 import registry_pb2_grpc

from observability.events import EventEmitter
from registry.health import probe_all
from registry.server import RegistryServicer


async def emit_all_health(store, emitter):
    """Publish the current health state of every registered agent."""
    for record in store.all():
        manifest = record.manifest
        try:
            await emitter.emit_health(
                agent_id=manifest.agent_id,
                healthy=record.healthy,
                fail_count=record.fail_count,
                last_seen=record.last_seen,
                deployment=getattr(manifest, "deployment", ""),
                kind=getattr(manifest, "kind", ""),
            )
        except Exception:
            pass


async def _health_loop(store, emitter, interval: float = 5):
    while True:
        await probe_all(store)
        await emit_all_health(store, emitter)
        await asyncio.sleep(interval)


async def serve():
    port = int(os.getenv("REGISTRY_PORT", "50051"))
    server = grpc.aio.server()
    servicer = RegistryServicer()
    registry_pb2_grpc.add_RegistryServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    emitter = EventEmitter("registry")
    health_task = asyncio.create_task(_health_loop(servicer.store, emitter))
    print(f"[registry] serving on :{port}", flush=True)
    try:
        await server.wait_for_termination()
    finally:
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task
        await emitter.close()


if __name__ == "__main__":
    asyncio.run(serve())

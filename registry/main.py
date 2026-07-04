"""Agent Registry 启动入口。

ws2 P0：有 POSTGRES_DSN 时用 PgStore（持久化），否则回退内存 Store。
"""
import asyncio
import contextlib
import inspect
import logging
import os

import grpc
from cockpit.registry.v1 import registry_pb2_grpc

from runtime.grpcio import aio_server, bind_port, run_aio_server

from observability.events import EventEmitter
from registry.health import probe_all
from registry.server import RegistryServicer

# 与其余 Python 服务一致的日志配置。此前 registry 从未配置 handler：INFO 全部被吞、
# WARNING 走 lastResort——2026-07-04 embed 泄漏排查因此拿不到「embedding via llm-gateway」
# 等关键状态线索，被迫用 PG 计数器/TCP 采样定位。可观测性即修复的一部分。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("registry.main")


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


async def _create_store():
    """根据环境变量创建 Store：有 POSTGRES_DSN 用 PgStore，否则内存 Store。"""
    dsn = os.getenv("POSTGRES_DSN")
    if dsn:
        try:
            from registry.store import PgStore
            pg_store = PgStore(dsn)
            ok = await pg_store.init()
            if ok:
                return pg_store
            logger.warning("PgStore init failed, falling back to memory Store")
        except Exception as e:
            logger.warning("PgStore import/init error: %s, falling back to memory Store", e)
    from registry.store import Store
    return Store()


async def serve():
    port = int(os.getenv("REGISTRY_PORT", "50051"))
    store = await _create_store()
    server = aio_server()
    servicer = RegistryServicer(store=store)
    registry_pb2_grpc.add_RegistryServicer_to_server(servicer, server)
    bind_port(server, f"[::]:{port}")
    await server.start()
    emitter = EventEmitter("registry")
    health_task = asyncio.create_task(_health_loop(servicer.store, emitter))
    store_type = "PgStore" if hasattr(store, "_pg_ok") and store._pg_ok else "memory"
    print(f"[registry] serving on :{port} (store={store_type})", flush=True)
    try:
        await run_aio_server(server, name="registry")
    finally:
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task
        await emitter.close()


if __name__ == "__main__":
    asyncio.run(serve())

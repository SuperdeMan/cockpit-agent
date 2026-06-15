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


async def _periodic_snapshot(
    servicer: EdgeOrchestratorServicer,
    interval: float = 30,
):
    """周期广播全量车辆状态。

    collector 是内存聚合，重启后镜像清空；edge 周期重发全量快照，使其能在
    一个周期内自愈恢复车辆状态（best-effort，失败静默、不影响车控主链路）。
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await servicer.emit_snapshot()
        except Exception:
            pass


async def _reregister_capabilities(interval: float = 10):
    """周期重注册车端能力：registry 重启后 edge-vehicle/edge-media 自动补注册
    （Register 幂等 upsert，失败静默、下个周期重试）。"""
    while True:
        await asyncio.sleep(interval)
        try:
            await register_edge_capabilities()
        except Exception:
            pass


async def serve():
    port = int(os.getenv("EDGE_ORCHESTRATOR_PORT", "50070"))
    server = grpc.aio.server()
    servicer = EdgeOrchestratorServicer()
    orchestrator_pb2_grpc.add_EdgeOrchestratorServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    snapshot_interval = float(os.getenv("OBS_SNAPSHOT_INTERVAL", "30"))
    reregister_interval = float(os.getenv("AGENT_REREGISTER_INTERVAL", "10"))
    state_task = asyncio.create_task(servicer.drain_state())
    debug_task = asyncio.create_task(_debug_subscription(servicer))
    snapshot_task = asyncio.create_task(_periodic_snapshot(servicer, snapshot_interval))
    await servicer.emit_snapshot()
    try:
        await register_edge_capabilities()
    except Exception as exc:
        print(f"[edge-orchestrator] registry register failed (continuing): {exc}", flush=True)
    reregister_task = asyncio.create_task(_reregister_capabilities(reregister_interval))
    print(f"[edge-orchestrator] serving on :{port}", flush=True)
    try:
        await server.wait_for_termination()
    finally:
        tasks = (state_task, debug_task, snapshot_task, reregister_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await servicer.obs.close()


if __name__ == "__main__":
    asyncio.run(serve())

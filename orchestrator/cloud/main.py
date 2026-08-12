"""Cloud Planner 启动入口。Phase 1：使用 PlannerEngine。

以包模块方式启动：`python -m orchestrator.cloud.main`（仓库根或 /app 为工作目录）。
不要 `python main.py` 平铺启动——本包内部统一相对 import。
"""
import asyncio
import contextlib
import logging
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc

from runtime.grpcio import aio_server, bind_port, run_aio_server
from .clients import Clients
from .planning import PlanBuilder
from .executor import DagExecutor
from .dispatch import UnifiedDispatcher
from .tools import ToolRegistry
from .aggregator import Aggregator
from .session import SessionStore
from .engine import PlannerEngine
from .server import CloudPlannerServicer
from .state_mirror import VehicleStateMirror
from runtime.privacy_delete_bus import (
    CLOUD_SUBJECT,
    CLOUD_TARGET,
    install_delete_responder,
    require_ready_shared_redis_backend,
)

# 结构化日志（原 basicConfig 升级）：stdout JSON 自动带 trace/session，
# WARNING+ 与带 trace 的 INFO 经 obs.log 进 collector——badcase 按 trace 检索日志。
from observability import setup_structured_logging  # noqa: E402

setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="cloud-planner")
logger = logging.getLogger("planner.main")


async def _reregister_tools(tools, clients, interval: float = 10):
    """周期重注册内置工具：registry 重启后 builtin-tools 自动补注册
    （Register 幂等 upsert，失败静默、下个周期重试）。"""
    while True:
        await asyncio.sleep(interval)
        try:
            await tools.register(clients)
        except Exception:
            pass


async def install_privacy_delete_responder(
    nc,
    engine: PlannerEngine,
    *,
    key: bytes | None = None,
    now=None,
) -> bool:
    """Expose owner-scoped pending-session deletion on the internal bus."""
    await require_ready_shared_redis_backend(engine.session)

    async def delete(user_id: str, _action: str) -> bool:
        return await engine.session.delete_owner(user_id)

    return await install_delete_responder(
        nc,
        subject=CLOUD_SUBJECT,
        target=CLOUD_TARGET,
        delete=delete,
        globally_shared_backend=True,
        key=key,
        now=now,
    )


async def serve():
    port = int(os.getenv("CLOUD_PLANNER_PORT", "50054"))

    clients = Clients()
    session = SessionStore()

    planner = PlanBuilder(
        llm_fn=clients.llm_complete,
        registry_fn=clients.resolve,
        # M1a submit_plan 结构化输出通道（PLANNER_TOOLCALL=on 时启用，默认 off）
        llm_tool_fn=clients.llm_complete_tools,
    )
    tools = ToolRegistry()
    dispatcher = UnifiedDispatcher(
        cloud_call=clients.call_agent,
        edge_call=clients.dispatch_to_edge,
        tools=tools,
    )
    # M2 Verifier：车况镜像供 state_match 求值（只读订阅 vehicle.state.changed）。
    # 连不上 NATS → 镜像恒空 → 对账判 UNKNOWN 不定罪，主链不受影响。
    mirror = VehicleStateMirror()
    await mirror.start()
    executor = DagExecutor(dispatcher=dispatcher, state_mirror=mirror)
    aggregator = Aggregator(llm_fn=clients.llm_complete)

    engine = PlannerEngine(
        clients=clients, planner=planner, executor=executor,
        aggregator=aggregator, session=session,
    )

    privacy_nc = None
    try:
        import nats
        privacy_nc = await nats.connect(
            os.getenv("NATS_URL", "nats://nats:4222"),
            max_reconnect_attempts=-1,
        )
        await install_privacy_delete_responder(privacy_nc, engine)
        logger.info("cloud pending-session privacy delete responder enabled")
    except Exception as exc:
        logger.warning(
            "cloud pending-session privacy delete responder unavailable: %s",
            type(exc).__name__,
        )
        if privacy_nc is not None:
            await privacy_nc.close()
            privacy_nc = None

    server = aio_server()
    orchestrator_pb2_grpc.add_CloudPlannerServicer_to_server(
        CloudPlannerServicer(engine), server)
    bind_port(server, f"[::]:{port}")
    await server.start()
    print(f"[cloud-planner] serving on :{port}", flush=True)
    try:
        await tools.register(clients)
    except Exception as exc:
        print(f"[cloud-planner] tool registry register failed (continuing): {exc}", flush=True)
    interval = float(os.getenv("AGENT_REREGISTER_INTERVAL", "10"))
    tools_task = asyncio.create_task(_reregister_tools(tools, clients, interval))
    try:
        await run_aio_server(server, name="cloud-planner")
    finally:
        tools_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tools_task
        if privacy_nc is not None:
            await privacy_nc.close()
        await mirror.close()


if __name__ == "__main__":
    asyncio.run(serve())

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

# 让 compose 的 LOG_LEVEL 生效——此前未配置 root logger，INFO 全被压制
# （Plan ready、memory recall 等不可见）。配置后这些观测日志可见。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def _reregister_tools(tools, clients, interval: float = 10):
    """周期重注册内置工具：registry 重启后 builtin-tools 自动补注册
    （Register 幂等 upsert，失败静默、下个周期重试）。"""
    while True:
        await asyncio.sleep(interval)
        try:
            await tools.register(clients)
        except Exception:
            pass


async def serve():
    port = int(os.getenv("CLOUD_PLANNER_PORT", "50054"))

    clients = Clients()
    session = SessionStore()

    planner = PlanBuilder(
        llm_fn=clients.llm_complete,
        registry_fn=clients.resolve,
    )
    tools = ToolRegistry()
    dispatcher = UnifiedDispatcher(
        cloud_call=clients.call_agent,
        edge_call=clients.dispatch_to_edge,
        tools=tools,
    )
    executor = DagExecutor(dispatcher=dispatcher)
    aggregator = Aggregator(llm_fn=clients.llm_complete)

    engine = PlannerEngine(
        clients=clients, planner=planner, executor=executor,
        aggregator=aggregator, session=session,
    )

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


if __name__ == "__main__":
    asyncio.run(serve())

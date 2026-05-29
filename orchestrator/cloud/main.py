"""Cloud Planner 启动入口。Phase 1：使用 PlannerEngine。"""
import asyncio
import os

import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2_grpc

from clients import Clients
from planning import PlanBuilder
from executor import DagExecutor
from aggregator import Aggregator
from session import SessionStore
from engine import PlannerEngine
from server import CloudPlannerServicer
from security.permission import PermissionEngine


async def serve():
    port = int(os.getenv("CLOUD_PLANNER_PORT", "50054"))

    clients = Clients()
    perms = PermissionEngine()
    session = SessionStore()

    planner = PlanBuilder(
        llm_fn=clients.llm_complete,
        registry_fn=clients.resolve,
    )
    executor = DagExecutor(call_agent_fn=clients.call_agent)
    aggregator = Aggregator(llm_fn=clients.llm_complete)

    engine = PlannerEngine(
        clients=clients, planner=planner, executor=executor,
        aggregator=aggregator, session=session, perms=perms,
    )

    server = grpc.aio.server()
    orchestrator_pb2_grpc.add_CloudPlannerServicer_to_server(
        CloudPlannerServicer(engine), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    print(f"[cloud-planner] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())

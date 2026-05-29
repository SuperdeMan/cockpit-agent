"""Cloud Planner gRPC 服务：对 Cloud Gateway 暴露流式 Handle。

Phase 1：使用 PlannerEngine（DAG 编排 + 多轮 + 聚合）。
"""
from __future__ import annotations

from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from google.protobuf import struct_pb2

from engine import PlannerEngine


def _to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class CloudPlannerServicer(orchestrator_pb2_grpc.CloudPlannerServicer):
    def __init__(self, engine: PlannerEngine):
        self.engine = engine

    async def Handle(self, request, context):
        async for event in self.engine.run(request):
            kind = event.get("kind")
            if kind == "speech":
                yield orchestrator_pb2.HandleEvent(speech_delta=event["delta"])
            elif kind == "action":
                yield orchestrator_pb2.HandleEvent(action=event["action"])
            elif kind == "final":
                actions = []
                for a in event.get("actions", []):
                    if isinstance(a, dict):
                        actions.append(common_pb2.AgentAction(
                            type=a.get("type", ""),
                            payload=_to_struct(a.get("payload", {})),
                            require_confirm=a.get("require_confirm", False),
                        ))
                    else:
                        actions.append(a)
                final = orchestrator_pb2.FinalResult(
                    speech=event.get("speech", ""),
                    follow_up=event.get("follow_up", ""),
                    need_confirm=event.get("need_confirm", False),
                    actions=actions,
                )
                yield orchestrator_pb2.HandleEvent(final=final)

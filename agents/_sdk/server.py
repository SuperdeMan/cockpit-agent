"""把 BaseAgent 包装成 gRPC Agent 服务并自注册到 Registry。"""
from __future__ import annotations
import asyncio
import os
import socket

import grpc
from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.common.v1 import common_pb2

from .base import BaseAgent, Context, IntentView
from .clients import RegistryClient
from .result import AgentResult

_STATUS = {"ok": 0, "need_confirm": 1, "need_slot": 2, "failed": 3, "rejected": 4}
_STATUS_DEFAULT = 3  # F16：未知 status 默认 FAILED（不是 OK），fail-closed


def _to_struct(d: dict | None) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


def _result_to_proto(res: AgentResult) -> agent_pb2.ExecuteResponse:
    actions = [
        common_pb2.AgentAction(
            type=a["type"],
            payload=_to_struct(a.get("payload")),
            require_confirm=a.get("require_confirm", False),
        )
        for a in res.actions
    ]
    return agent_pb2.ExecuteResponse(
        status=_STATUS.get(res.status, _STATUS_DEFAULT),
        speech=res.speech,
        ui_card=_to_struct(res.ui_card),
        actions=actions,
        follow_up=res.follow_up,
        data=_to_struct(res.data),                  # F3：结构化结果供编排 slot_refs
        missing_slots=list(res.missing_slots),       # F12：缺失槽位名
    )


def _intent_view(req) -> IntentView:
    return IntentView(
        name=req.intent.name,
        slots=dict(req.intent.slots),
        raw_text=req.intent.raw_text,
        confidence=req.intent.confidence,
    )


def _context(req, memory) -> Context:
    c = req.context
    return Context(c.session_id, c.user_id, c.vehicle_id, memory)


class _Servicer(agent_pb2_grpc.AgentServicer):
    def __init__(self, agent: BaseAgent):
        self.agent = agent

    async def Describe(self, request, context):
        return self.agent.manifest

    async def Health(self, request, context):
        return agent_pb2.HealthResponse(status=agent_pb2.HealthResponse.SERVING)

    async def Execute(self, request, context):
        try:
            res = await self.agent.handle(
                _intent_view(request), _context(request, self.agent.memory), dict(request.meta))
            return _result_to_proto(res)
        except Exception as e:
            return agent_pb2.ExecuteResponse(
                status=3,  # FAILED
                speech=f"Agent 内部错误：{type(e).__name__}",
                error=common_pb2.ErrorInfo(code="agent_error", message=str(e)),
            )

    async def ExecuteStream(self, request, context):
        iv, ctx = _intent_view(request), _context(request, self.agent.memory)
        try:
            async for kind, payload in self.agent.handle_stream(iv, ctx, dict(request.meta)):
                if kind == "speech":
                    yield agent_pb2.ExecuteEvent(speech_delta=payload)
                elif kind == "action":
                    yield agent_pb2.ExecuteEvent(action=payload)
                elif kind == "final":
                    yield agent_pb2.ExecuteEvent(final=_result_to_proto(payload))
        except Exception as e:
            yield agent_pb2.ExecuteEvent(final=agent_pb2.ExecuteResponse(
                status=3,
                speech=f"Agent 内部错误：{type(e).__name__}",
                error=common_pb2.ErrorInfo(code="agent_error", message=str(e)),
            ))


async def serve(agent: BaseAgent):
    port = int(os.getenv("AGENT_PORT", "50060"))
    server = grpc.aio.server()
    agent_pb2_grpc.add_AgentServicer_to_server(_Servicer(agent), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()

    endpoint = f"{socket.gethostname()}:{port}"
    try:
        lease = await RegistryClient().register(agent.manifest, endpoint)
        print(f"[sdk] registered {agent.manifest.agent_id} lease={lease}", flush=True)
    except Exception as e:  # 注册失败不阻塞服务启动，便于本地单测
        print(f"[sdk] registry register failed (continuing): {e}", flush=True)

    print(f"[sdk] {agent.manifest.agent_id} serving on :{port}", flush=True)
    await server.wait_for_termination()


def run(agent: BaseAgent):
    """同步入口，供 `python -m ...` 调用。"""
    asyncio.run(serve(agent))

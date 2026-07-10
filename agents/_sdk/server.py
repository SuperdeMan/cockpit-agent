"""把 BaseAgent 包装成 gRPC Agent 服务并自注册到 Registry。"""
from __future__ import annotations
import asyncio
import contextlib
import os
import socket

import grpc
from google.protobuf import struct_pb2

from runtime.grpcio import aio_server, bind_port, run_aio_server
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.common.v1 import common_pb2

from observability.tracing import set_session_id, set_trace_id

from .base import BaseAgent, Context, IntentView, _set_current_meta
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
        meta = dict(request.meta)
        _set_current_meta(meta)  # 护栏：使 AgentClient 读取跨进程 depth/stack
        # 观测：agent 进程内的 span/结构化日志自动携带 trace/session（一处设置全 Agent 覆盖）
        set_trace_id(meta.get("trace_id", ""))
        set_session_id(request.session_id)
        try:
            res = await self.agent.handle(
                _intent_view(request), _context(request, self.agent.memory), meta)
            return _result_to_proto(res)
        except Exception as e:
            return agent_pb2.ExecuteResponse(
                status=3,  # FAILED
                speech=f"Agent 内部错误：{type(e).__name__}",
                error=common_pb2.ErrorInfo(code="agent_error", message=str(e)),
            )
        finally:
            _set_current_meta(None)  # 防止意外泄漏到后续 request

    async def ExecuteStream(self, request, context):
        iv, ctx = _intent_view(request), _context(request, self.agent.memory)
        meta = dict(request.meta)
        _set_current_meta(meta)  # 护栏：使 AgentClient 读取跨进程 depth/stack
        set_trace_id(meta.get("trace_id", ""))
        set_session_id(request.session_id)
        try:
            async for kind, payload in self.agent.handle_stream(iv, ctx, meta):
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
        finally:
            _set_current_meta(None)


async def _reregister_loop(registry, manifest, endpoint: str, interval: float):
    """周期重注册：registry 重启/暂不可达后，agent 在一个周期内自动补注册。

    Register 是幂等 upsert，重复调用安全；失败静默、下个周期重试，不影响 Agent
    自身服务（守住"registry 重启不应让运行中的 agent 永久失联"）。
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await registry.register(manifest, endpoint)
        except Exception:
            pass


async def serve(agent: BaseAgent):
    port = int(os.getenv("AGENT_PORT", "50060"))
    # 观测归属：LLMClient 的 obs meta（caller_service）从这里拿 agent 身份，免逐 Agent 配置
    os.environ.setdefault("AGENT_ID", agent.manifest.agent_id)
    server = aio_server()
    agent_pb2_grpc.add_AgentServicer_to_server(_Servicer(agent), server)
    bind_port(server, f"[::]:{port}")
    await server.start()

    endpoint = f"{socket.gethostname()}:{port}"
    registry = RegistryClient()
    try:
        lease = await registry.register(agent.manifest, endpoint)
        print(f"[sdk] registered {agent.manifest.agent_id} lease={lease}", flush=True)
    except Exception as e:  # 注册失败不阻塞服务启动，便于本地单测
        print(f"[sdk] registry register failed (continuing): {e}", flush=True)

    # 周期重注册：registry 重启后自动补注册（默认 10s，可经 env 调）
    interval = float(os.getenv("AGENT_REREGISTER_INTERVAL", "10"))
    reregister_task = asyncio.create_task(
        _reregister_loop(registry, agent.manifest, endpoint, interval))

    # 可选生命周期钩子：响应式 Agent（订阅 NATS 主动播报等）在此启动后台循环。
    # 失败不阻塞服务（fail-open），异常被吞掉避免 "Task exception never retrieved"。
    async def _run_on_start():
        try:
            await agent.on_start()
        except Exception as e:
            print(f"[sdk] on_start failed (continuing): {e}", flush=True)

    on_start_task = asyncio.create_task(_run_on_start())

    print(f"[sdk] {agent.manifest.agent_id} serving on :{port}", flush=True)
    try:
        await run_aio_server(server, name=agent.manifest.agent_id)
    finally:
        for task in (reregister_task, on_start_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def run(agent: BaseAgent):
    """同步入口，供 `python -m ...` 调用。"""
    asyncio.run(serve(agent))

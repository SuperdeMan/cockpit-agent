"""Cloud Planner 的下游客户端：Registry / LLM Gateway / Agent / Memory。

Phase 1 改进：连接复用、统一超时。
"""
from __future__ import annotations
import os
import grpc
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc
from cockpit.channel.v1 import channel_pb2, channel_pb2_grpc
from cockpit.common.v1 import common_pb2

_DEFAULT_TIMEOUT = 10


class Clients:
    def __init__(self):
        self.registry_addr = os.getenv("REGISTRY_ADDR", "registry:50051")
        self.llm_addr = os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
        self.memory_addr = os.getenv("MEMORY_ADDR", "memory:50053")
        self.cloud_gateway_addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
        self._ch_registry: grpc.aio.Channel | None = None
        self._ch_llm: grpc.aio.Channel | None = None
        self._ch_memory: grpc.aio.Channel | None = None
        self._ch_edge: grpc.aio.Channel | None = None
        self._ch_agents: dict[str, grpc.aio.Channel] = {}  # F15：按 endpoint 复用 channel

    def _registry_stub(self):
        if self._ch_registry is None:
            self._ch_registry = grpc.aio.insecure_channel(self.registry_addr)
        return registry_pb2_grpc.RegistryStub(self._ch_registry)

    def _llm_stub(self):
        if self._ch_llm is None:
            self._ch_llm = grpc.aio.insecure_channel(self.llm_addr)
        return llm_pb2_grpc.LLMGatewayStub(self._ch_llm)

    def _memory_stub(self):
        if self._ch_memory is None:
            self._ch_memory = grpc.aio.insecure_channel(self.memory_addr)
        return memory_pb2_grpc.MemoryStub(self._ch_memory)

    def _edge_stub(self):
        if self._ch_edge is None:
            self._ch_edge = grpc.aio.insecure_channel(self.cloud_gateway_addr)
        return channel_pb2_grpc.EdgeCloudChannelStub(self._ch_edge)

    async def append_turn(self, session_id: str, role: str, text: str):
        """写入一轮对话到 memory（task 2：对话记忆 + 指代消解的数据来源）。"""
        await self._memory_stub().AppendTurn(
            memory_pb2.AppendTurnRequest(session_id=session_id, role=role, text=text),
            timeout=_DEFAULT_TIMEOUT)

    async def get_session(self, session_id: str, last_n: int = 6) -> list[dict]:
        """取最近 N 轮对话（供 planner 注入上下文）。"""
        resp = await self._memory_stub().GetSession(
            memory_pb2.GetSessionRequest(session_id=session_id, last_n=last_n),
            timeout=_DEFAULT_TIMEOUT)
        return [{"role": t.role, "text": t.text, "ts": t.ts} for t in resp.turns]

    async def list_agents(self):
        resp = await self._registry_stub().ListAgents(
            registry_pb2.ListRequest(category=""), timeout=_DEFAULT_TIMEOUT)
        return list(resp.agents)

    async def register_manifest(self, manifest, endpoint: str):
        return await self._registry_stub().Register(
            registry_pb2.RegisterRequest(
                manifest=manifest,
                endpoint=endpoint,
            ),
            timeout=_DEFAULT_TIMEOUT,
        )

    async def resolve(self, query: str = "", intent: str = "", top_k: int = 1):
        resp = await self._registry_stub().ResolveAgents(
            registry_pb2.ResolveRequest(query=query, intent=intent, top_k=top_k),
            timeout=_DEFAULT_TIMEOUT)
        return list(resp.agents)

    async def llm_complete(self, messages: list[dict], max_tokens: int = 400) -> str:
        resp = await self._llm_stub().Complete(
            llm_pb2.CompleteRequest(
                messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
                temperature=0.3, max_tokens=max_tokens),
            timeout=30)
        return resp.content

    def _agent_stub(self, endpoint: str):
        # F15：按 endpoint 复用 channel（之前每次新建泄漏）
        if endpoint not in self._ch_agents:
            self._ch_agents[endpoint] = grpc.aio.insecure_channel(endpoint)
        return agent_pb2_grpc.AgentStub(self._ch_agents[endpoint])

    @staticmethod
    def _merge_meta(ctx, meta: dict | None) -> dict:
        """会话级偏好（ctx.prefs）作底，step.meta 覆盖——后者携带 confirmed 等运行期标记。"""
        prefs = getattr(ctx, "prefs", None) or {}
        return {**prefs, **(meta or {})}

    def _exec_request(self, intent: str, slots: dict, ctx, meta: dict | None):
        return agent_pb2.ExecuteRequest(
            session_id=ctx.session_id if ctx else "",
            intent=common_pb2.Intent(name=intent, slots=slots, raw_text="", confidence=0.9),
            context=common_pb2.ContextRef(
                session_id=ctx.session_id if ctx else "",
                user_id=ctx.user_id if ctx else "",
                vehicle_id=ctx.vehicle_id if ctx else "",
            ),
            meta=self._merge_meta(ctx, meta),
        )

    async def call_agent(self, endpoint: str, intent: str, slots: dict,
                         ctx=None, meta: dict | None = None) -> agent_pb2.ExecuteResponse:
        """meta 随 ExecuteRequest.meta 下发给 Agent（确认续接标记、trace、会话偏好等）。"""
        stub = self._agent_stub(endpoint)
        req = self._exec_request(intent, slots, ctx, meta)
        return await stub.Execute(req, timeout=_DEFAULT_TIMEOUT)

    async def call_agent_stream(self, endpoint: str, intent: str, slots: dict,
                                ctx=None, meta: dict | None = None, timeout: float = 30):
        """流式调用 Agent.ExecuteStream，归一化为 (kind, payload) 元组：
        ("speech", str) / ("action", AgentAction) / ("final", ExecuteResponse)。
        供 engine 单步开放域流式直通（边想边说）。
        """
        stub = self._agent_stub(endpoint)
        req = self._exec_request(intent, slots, ctx, meta)
        async for ev in stub.ExecuteStream(req, timeout=timeout):
            which = ev.WhichOneof("event")
            if which == "speech_delta":
                yield ("speech", ev.speech_delta)
            elif which == "action":
                yield ("action", ev.action)
            elif which == "final":
                yield ("final", ev.final)

    async def dispatch_to_edge(self, vehicle_id: str, step, ctx):
        """Call the requesting vehicle's edge executor through Cloud Gateway."""
        meta = self._merge_meta(ctx, step.meta)
        if getattr(ctx, "trace_id", ""):
            meta.setdefault("trace_id", ctx.trace_id)
        envelope = channel_pb2.EdgeCallEnvelope(
            vehicle_id=vehicle_id,
            call=channel_pb2.EdgeCall(
                step_id=step.id,
                intent=common_pb2.Intent(
                    name=step.intent,
                    slots=step.slots,
                    confidence=0.9,
                ),
                meta=meta,
            ),
        )
        result = await self._edge_stub().DispatchToEdge(
            envelope, timeout=step.latency_budget_ms / 1000.0)
        if not result.HasField("result"):
            raise RuntimeError("edge result missing execute response")
        return result.result

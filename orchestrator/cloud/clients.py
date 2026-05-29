"""Cloud Planner 的下游客户端：Registry / LLM Gateway / Agent / Memory。

Phase 1 改进：连接复用、统一超时。
"""
from __future__ import annotations
import os
import grpc
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.common.v1 import common_pb2

_DEFAULT_TIMEOUT = 10


class Clients:
    def __init__(self):
        self.registry_addr = os.getenv("REGISTRY_ADDR", "registry:50051")
        self.llm_addr = os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
        self._ch_registry: grpc.aio.Channel | None = None
        self._ch_llm: grpc.aio.Channel | None = None

    def _registry_stub(self):
        if self._ch_registry is None:
            self._ch_registry = grpc.aio.insecure_channel(self.registry_addr)
        return registry_pb2_grpc.RegistryStub(self._ch_registry)

    def _llm_stub(self):
        if self._ch_llm is None:
            self._ch_llm = grpc.aio.insecure_channel(self.llm_addr)
        return llm_pb2_grpc.LLMGatewayStub(self._ch_llm)

    async def list_agents(self):
        resp = await self._registry_stub().ListAgents(
            registry_pb2.ListRequest(category=""), timeout=_DEFAULT_TIMEOUT)
        return list(resp.agents)

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

    async def call_agent(self, endpoint: str, intent: str, slots: dict,
                         ctx=None) -> agent_pb2.ExecuteResponse:
        ch = grpc.aio.insecure_channel(endpoint)
        stub = agent_pb2_grpc.AgentStub(ch)
        req = agent_pb2.ExecuteRequest(
            session_id=ctx.session_id if ctx else "",
            intent=common_pb2.Intent(name=intent, slots=slots, raw_text="", confidence=0.9),
            context=common_pb2.ContextRef(
                session_id=ctx.session_id if ctx else "",
                user_id=ctx.user_id if ctx else "",
                vehicle_id=ctx.vehicle_id if ctx else "",
            ),
        )
        return await stub.Execute(req, timeout=_DEFAULT_TIMEOUT)

"""Cloud Planner 的下游客户端：Registry / LLM Gateway / Agent / Memory。

Phase 1 改进：连接复用、统一超时。
"""
from __future__ import annotations
import logging
import os
from contextvars import ContextVar

import grpc

from runtime.grpcio import aio_channel

logger = logging.getLogger("planner.clients")

# 运行时硬化 D2：请求级 LLM pin（meta.llm_provider/llm_model）。engine 在请求入口按
# ctx.prefs 设置；llm_complete（planner/aggregator 共用）据此透传给网关。Agent 路径
# 不走此变量——pin 随 _merge_meta 进 ExecuteRequest.meta、SDK 自动透传。
_LLM_PIN: ContextVar[tuple[str, str]] = ContextVar("cloud_llm_pin", default=("", ""))


def set_llm_pin(provider: str = "", model: str = "") -> None:
    _LLM_PIN.set(((provider or "").strip(), (model or "").strip()))
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
            self._ch_registry = aio_channel(self.registry_addr)
        return registry_pb2_grpc.RegistryStub(self._ch_registry)

    def _llm_stub(self):
        if self._ch_llm is None:
            self._ch_llm = aio_channel(self.llm_addr)
        return llm_pb2_grpc.LLMGatewayStub(self._ch_llm)

    def _memory_stub(self):
        if self._ch_memory is None:
            self._ch_memory = aio_channel(self.memory_addr)
        return memory_pb2_grpc.MemoryStub(self._ch_memory)

    def _edge_stub(self):
        if self._ch_edge is None:
            self._ch_edge = aio_channel(self.cloud_gateway_addr)
        return channel_pb2_grpc.EdgeCloudChannelStub(self._ch_edge)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = ""):
        """写入一轮对话到 memory（指代消解的数据来源）。带 user_id 时 memory 侧据此触发异步抽取。"""
        await self._memory_stub().AppendTurn(
            memory_pb2.AppendTurnRequest(session_id=session_id, role=role, text=text,
                                         user_id=user_id, vehicle_id=vehicle_id),
            timeout=_DEFAULT_TIMEOUT)

    async def get_session(self, session_id: str, last_n: int = 6) -> list[dict]:
        """取最近 N 轮对话（供 planner 注入上下文）。"""
        resp = await self._memory_stub().GetSession(
            memory_pb2.GetSessionRequest(session_id=session_id, last_n=last_n),
            timeout=_DEFAULT_TIMEOUT)
        return [{"role": t.role, "text": t.text, "ts": t.ts} for t in resp.turns]

    async def recall(self, user_id: str, query: str = "", *, occupant_id: str = "",
                     scopes: list[str] | None = None, kinds: list[str] | None = None,
                     top_k: int = 3, min_confidence: float = 0.0) -> list[dict]:
        """语义召回用户偏好（供 planner 注入）。返回 dict 列表（含 score）。"""
        resp = await self._memory_stub().Recall(
            memory_pb2.RecallRequest(
                user_id=user_id, occupant_id=occupant_id, query=query,
                scopes=scopes or [], kinds=kinds or [], top_k=top_k,
                min_confidence=min_confidence),
            timeout=_DEFAULT_TIMEOUT)
        return [{"text": it.text, "scope": it.scope, "predicate": it.predicate,
                 "provenance": it.provenance, "confidence": it.confidence}
                for it in resp.items]

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

    async def llm_complete(self, messages: list[dict], max_tokens: int = 800,
                           thinking: bool = False) -> str:
        """thinking=True 时本次开思考（meta 透传给网关）并抬 token/超时。
        **Planner 调用恒 False**（结构化 JSON 不能被 reasoning 吃空）；Aggregator 由
        engine 对复杂任务传 True。"""
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            temperature=0.3, max_tokens=max(max_tokens, 2048) if thinking else max_tokens)
        if thinking:
            req.meta["thinking"] = "on"
        # 运行时硬化 D2：请求级 LLM pin（engine 在请求入口 set_llm_pin）——planner/aggregator
        # 的 LLM 调用与 Agent 路径同脑，评测/重放 A/B 才有意义。
        pin_provider, pin_model = _LLM_PIN.get()
        if pin_provider:
            req.meta["llm_provider"] = pin_provider
            if pin_model:
                req.meta["llm_model"] = pin_model
        # 观测贯通：LLM 网关据此发 obs.llm 事件（模型/tokens/时延按 trace 归档）。
        # caller_service 仅供观测归属——刻意不用 "caller"（那是限流桶键，不能扰动）。
        from observability.tracing import get_session_id, get_trace_id
        if get_trace_id():
            req.meta["trace_id"] = get_trace_id()
        if get_session_id():
            req.meta["session_id"] = get_session_id()
        req.meta["caller_service"] = "cloud-planner"
        resp = await self._llm_stub().Complete(req, timeout=60 if thinking else 30)
        return resp.content

    def _agent_stub(self, endpoint: str):
        # F15：按 endpoint 复用 channel（之前每次新建泄漏）
        if endpoint not in self._ch_agents:
            self._ch_agents[endpoint] = aio_channel(endpoint)
        return agent_pb2_grpc.AgentStub(self._ch_agents[endpoint])

    # 敏感上下文键 → 所需 scope。Agent 经 manifest context_scopes 声明后才下发（最小化）。
    _SENSITIVE_SCOPE = {
        "current_lat": "location", "current_lng": "location",
        "current_accuracy_m": "location", "current_location_at": "location",
        "current_location_source": "location", "vehicle_battery": "vehicle_state",
    }

    @classmethod
    def _merge_meta(cls, ctx, meta: dict | None, context_scopes=None) -> dict:
        """会话级偏好（ctx.prefs）作底，step.meta 覆盖——后者携带 confirmed 等运行期标记。

        context_scopes 非 None（cloud unary 下发）时按声明最小化敏感键：未声明 location/
        vehicle_state 的 Agent 收不到精确位置/电量；非敏感偏好（answer_length 等）始终下发。
        None（edge/stream/legacy 路径）= 不过滤，保持既有行为（电量供端侧安全门控）。"""
        prefs = dict(getattr(ctx, "prefs", None) or {})
        if context_scopes is not None:
            allowed = set(context_scopes or [])
            prefs = {k: v for k, v in prefs.items()
                     if cls._SENSITIVE_SCOPE.get(k) is None
                     or cls._SENSITIVE_SCOPE.get(k) in allowed}
        merged = {**prefs, **(meta or {})}
        # 观测贯通：trace_id 随 meta 下发——SDK server 据此 set_trace_id，Agent 进程内
        # span/日志/LLM 调用自动归属本轮 trace；子调用经父 meta 透传天然继承。
        tid = getattr(ctx, "trace_id", "") or ""
        if tid:
            merged.setdefault("trace_id", tid)
        return merged

    def _exec_request(self, intent: str, slots: dict, ctx, meta: dict | None,
                      context_scopes=None):
        return agent_pb2.ExecuteRequest(
            session_id=ctx.session_id if ctx else "",
            intent=common_pb2.Intent(
                name=intent, slots=slots,
                raw_text=getattr(ctx, "raw_text", "") or "",
                confidence=0.9),
            context=common_pb2.ContextRef(
                session_id=ctx.session_id if ctx else "",
                user_id=ctx.user_id if ctx else "",
                vehicle_id=ctx.vehicle_id if ctx else "",
            ),
            meta=self._merge_meta(ctx, meta, context_scopes),
        )

    async def call_agent(self, endpoint: str, intent: str, slots: dict,
                         ctx=None, meta: dict | None = None,
                         timeout: float = _DEFAULT_TIMEOUT,
                         context_scopes=None) -> agent_pb2.ExecuteResponse:
        """meta 随 ExecuteRequest.meta 下发给 Agent（确认续接标记、trace、会话偏好等）。

        context_scopes：Agent manifest 声明需要的敏感上下文（location|vehicle_state），
        由 dispatcher 传 step.context_scopes，据此最小化下发精确位置/电量。
        timeout 由 dispatcher 传 step.latency_budget_ms/1000——慢 Agent（trip-planner 20s+、
        info 调研）需大于默认 10s，否则开思考后会被 10s 卡死。"""
        stub = self._agent_stub(endpoint)
        req = self._exec_request(intent, slots, ctx, meta, context_scopes)
        return await stub.Execute(req, timeout=timeout)

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
        logger.info("DispatchToEdge: vehicle=%s step=%s intent=%s",
                    vehicle_id, step.id, step.intent)
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
        logger.info("DispatchToEdge result: status=%s speech=%s",
                    result.result.status, result.result.speech[:80])
        return result.result

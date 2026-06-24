"""SDK 内置客户端：LLM Gateway / Memory / Registry。均为 async gRPC。

Phase 1 改进：连接复用（每个 client 实例共享一个 channel）、
统一超时、异常→ErrorInfo 映射。
"""
from __future__ import annotations
import os
import grpc

from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
from ._ctx import get_current_meta

# 默认超时（秒）
DEFAULT_TIMEOUT = 10
# 开思考时给足预算：reasoning 占 token、且更慢，故抬高 token 下限与超时。
_THINK_MAX_TOKENS = 2048
_THINK_TIMEOUT = 30


def _resolve_thinking(thinking) -> bool:
    """thinking=None 时从当前请求 meta 自动判定（编排层对复杂任务下发 meta["thinking"]="on"）。
    这样所有 Agent 的 LLM 调用据此自动覆盖，无需逐个改业务码。"""
    if thinking is not None:
        return bool(thinking)
    meta = get_current_meta() or {}
    return str(meta.get("thinking", "")).lower() in ("on", "true", "1", "enabled")


class LLMClient:
    def __init__(self, addr: str | None = None):
        self.addr = addr or os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
        self._ch: grpc.aio.Channel | None = None

    def _channel(self) -> grpc.aio.Channel:
        if self._ch is None:
            self._ch = grpc.aio.insecure_channel(self.addr)
        return self._ch

    async def _reset_channel(self):
        """连接失效（如 llm-gateway 重启换 IP，旧 channel 卡在旧地址）→ 关旧 channel，
        下次重建并重新解析 DNS。否则依赖一重启，agent 的缓存 channel 永久失联到 agent 重启。"""
        ch, self._ch = self._ch, None
        if ch is not None:
            try:
                await ch.close()
            except Exception:
                pass

    def _stub(self):
        return llm_pb2_grpc.LLMGatewayStub(self._channel())

    async def complete(self, messages: list[dict], model: str = "",
                       temperature: float = 0.7, max_tokens: int = 512,
                       timeout: float = DEFAULT_TIMEOUT, thinking=None) -> str:
        think = _resolve_thinking(thinking)
        if think:
            max_tokens = max(max_tokens, _THINK_MAX_TOKENS)
            timeout = max(timeout, _THINK_TIMEOUT)
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            model=model, temperature=temperature, max_tokens=max_tokens,
        )
        if think:
            req.meta["thinking"] = "on"
        for attempt in (1, 2):
            try:
                resp = await self._stub().Complete(req, timeout=timeout)
                return resp.content
            except grpc.aio.AioRpcError as e:
                # 依赖重启换 IP → 旧 channel UNAVAILABLE：重建 channel 重新解析 DNS，重试一次。
                if attempt == 1 and e.code() == grpc.StatusCode.UNAVAILABLE:
                    await self._reset_channel()
                    continue
                raise RuntimeError(f"LLM Gateway error: {e.code().name}: {e.details()}") from e

    async def stream(self, messages: list[dict], model: str = "",
                     temperature: float = 0.7, max_tokens: int = 512,
                     timeout: float = 30, thinking=None):
        think = _resolve_thinking(thinking)
        if think:
            max_tokens = max(max_tokens, _THINK_MAX_TOKENS)
            timeout = max(timeout, _THINK_TIMEOUT)
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            model=model, temperature=temperature, max_tokens=max_tokens,
        )
        if think:
            req.meta["thinking"] = "on"
        try:
            async for chunk in self._stub().CompleteStream(req, timeout=timeout):
                if chunk.delta:
                    yield chunk.delta
        except grpc.aio.AioRpcError as e:
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                await self._reset_channel()  # 让后续调用重建 channel 自愈
            raise RuntimeError(f"LLM Gateway stream error: {e.code().name}: {e.details()}") from e


class MemoryClient:
    def __init__(self, addr: str | None = None):
        self.addr = addr or os.getenv("MEMORY_ADDR", "memory:50053")
        self._ch: grpc.aio.Channel | None = None

    def _channel(self) -> grpc.aio.Channel:
        if self._ch is None:
            self._ch = grpc.aio.insecure_channel(self.addr)
        return self._ch

    async def _reset_channel(self):
        """memory 重启换 IP 后旧 channel 失联 → 关旧 channel，下次重建重新解析 DNS。"""
        ch, self._ch = self._ch, None
        if ch is not None:
            try:
                await ch.close()
            except Exception:
                pass

    def _stub(self):
        return memory_pb2_grpc.MemoryStub(self._channel())

    async def get_context(self, session_id: str, user_id: str, vehicle_id: str,
                          scopes: list[str]) -> dict:
        for attempt in (1, 2):
            try:
                resp = await self._stub().GetContext(
                    memory_pb2.GetContextRequest(
                        session_id=session_id, user_id=user_id,
                        vehicle_id=vehicle_id, scopes=scopes),
                    timeout=DEFAULT_TIMEOUT)
                return dict(resp.values)
            except grpc.aio.AioRpcError as e:
                if attempt == 1 and e.code() == grpc.StatusCode.UNAVAILABLE:
                    await self._reset_channel()
                    continue
                raise RuntimeError(f"Memory error: {e.code().name}: {e.details()}") from e

    async def get_session(self, session_id: str, last_n: int = 6) -> list[dict]:
        try:
            resp = await self._stub().GetSession(
                memory_pb2.GetSessionRequest(session_id=session_id, last_n=last_n),
                timeout=DEFAULT_TIMEOUT)
            return [{"role": t.role, "text": t.text, "ts": t.ts} for t in resp.turns]
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"Memory error: {e.code().name}: {e.details()}") from e

    async def upsert_profile(self, user_id: str, key: str, value_json: str) -> bool:
        """写用户画像字段（如常用地点 places）。失败抛 RuntimeError，调用方决定容错。"""
        try:
            resp = await self._stub().UpsertProfile(
                memory_pb2.UpsertProfileRequest(
                    user_id=user_id, key=key, value_json=value_json),
                timeout=DEFAULT_TIMEOUT)
            return resp.ok
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"Memory error: {e.code().name}: {e.details()}") from e


class RegistryClient:
    """注册中心客户端。连接复用，支持 register + resolve。"""

    def __init__(self, addr: str | None = None):
        self.addr = addr or os.getenv("REGISTRY_ADDR", "registry:50051")
        self._ch: grpc.aio.Channel | None = None

    def _channel(self) -> grpc.aio.Channel:
        if self._ch is None:
            self._ch = grpc.aio.insecure_channel(self.addr)
        return self._ch

    def _stub(self):
        return registry_pb2_grpc.RegistryStub(self._channel())

    async def register(self, manifest, endpoint: str) -> str:
        try:
            resp = await self._stub().Register(
                registry_pb2.RegisterRequest(manifest=manifest, endpoint=endpoint),
                timeout=DEFAULT_TIMEOUT)
            return resp.lease_id
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"Registry register error: {e.code().name}: {e.details()}") from e

    async def resolve(self, intent: str = "", query: str = "", top_k: int = 1) -> list:
        try:
            resp = await self._stub().ResolveAgents(
                registry_pb2.ResolveRequest(intent=intent, query=query, top_k=top_k),
                timeout=DEFAULT_TIMEOUT)
            return list(resp.agents)
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"Registry resolve error: {e.code().name}: {e.details()}") from e

    async def list_agents(self, category: str = "") -> list:
        try:
            resp = await self._stub().ListAgents(
                registry_pb2.ListRequest(category=category),
                timeout=DEFAULT_TIMEOUT)
            return list(resp.agents)
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"Registry list error: {e.code().name}: {e.details()}") from e

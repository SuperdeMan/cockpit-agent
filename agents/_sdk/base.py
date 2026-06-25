"""BaseAgent: 业务 Agent 的基类。子类只需实现 handle()。

Phase 1：注入 AgentClient（跨 Agent 协作）。
护栏跨进程修复：server.Execute 在调 handle 前把 request.meta 中的
call_depth/call_stack 写入 _current_meta contextvar，agents 属性读取
它构造正确深度的 AgentClient，使 MAX_DEPTH/环检测跨进程生效。

ws2 P0：注入 RegistryClient，AgentClient 经 Registry 动态解析 endpoint。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .clients import LLMClient, MemoryClient, RegistryClient
from .manifest import load_manifest
from .result import AgentResult
# contextvar 抽到 _ctx（中立模块），解 base↔clients 循环依赖；此处保留同名再导出向后兼容。
from ._ctx import _current_meta, set_current_meta as _set_current_meta, get_current_meta  # noqa: F401


@dataclass
class IntentView:
    """传给业务的意图视图（已从 proto 解包）。"""
    name: str
    slots: dict[str, str]
    raw_text: str
    confidence: float


class Context:
    """会话上下文句柄。按需向 Memory 拉取声明的 scopes（隐私最小化）。"""
    def __init__(self, session_id: str, user_id: str, vehicle_id: str, memory: MemoryClient):
        self.session_id = session_id
        self.user_id = user_id
        self.vehicle_id = vehicle_id
        self._memory = memory

    async def fetch(self, *scopes: str) -> dict:
        if not scopes:
            return {}
        return await self._memory.get_context(
            self.session_id, self.user_id, self.vehicle_id, list(scopes))

    async def history(self, last_n: int = 6) -> list[dict]:
        return await self._memory.get_session(self.session_id, last_n)

    async def save_profile(self, key: str, value) -> bool:
        """写用户画像字段（如常用地点 places）。value 为可 JSON 序列化对象。
        无 user_id 时静默跳过（PoC 单用户由网关注入 user_id）。"""
        if not self.user_id:
            return False
        import json
        return await self._memory.upsert_profile(
            self.user_id, key, json.dumps(value, ensure_ascii=False))

    async def recall(self, query: str = "", *, scopes: list[str] | None = None,
                     kinds: list[str] | None = None, top_k: int = 5,
                     predicate_prefix: str = "", min_score: float = 0.0,
                     min_confidence: float = 0.0, max_age_days: int = 0) -> list[dict]:
        """语义召回与当前问题相关的偏好/事件（如点餐前取口味）。无 user_id 返回空。
        精确画像读取传 predicate_prefix（如 "place." "taste."）走谓词精确而非向量。"""
        if not self.user_id:
            return []
        return await self._memory.recall(
            self.user_id, query, scopes=scopes, kinds=kinds, top_k=top_k,
            predicate_prefix=predicate_prefix, min_score=min_score,
            min_confidence=min_confidence, max_age_days=max_age_days)

    async def remember(self, text: str, *, predicate: str = "", kind: str = "semantic",
                       scope: str = "", value=None, provenance: str = "user_stated",
                       confidence: float = 1.0, privacy_level: str = "normal",
                       vehicle_id: str = "", memory_level: str = "user",
                       expires_at: int = 0, review_status: str = "user_confirmed",
                       source_turn_ids: str = "") -> bool:
        """显式写一条记忆。无 user_id 或空文本时静默跳过。
        家/公司等高敏地点用 privacy_level="highly_sensitive"；车级偏好传 vehicle_id+memory_level。"""
        if not self.user_id or not text:
            return False
        import json
        item = {"user_id": self.user_id, "kind": kind, "predicate": predicate,
                "text": text, "scope": scope, "provenance": provenance,
                "confidence": confidence, "privacy_level": privacy_level,
                "vehicle_id": vehicle_id, "memory_level": memory_level,
                "expires_at": expires_at, "review_status": review_status,
                "source_turn_ids": source_turn_ids,
                "value_json": json.dumps(value, ensure_ascii=False) if value is not None else ""}
        ids = await self._memory.remember([item])
        return bool(ids)


class BaseAgent(ABC):
    def __init__(self, manifest_path: str):
        self.manifest = load_manifest(manifest_path)
        self.llm = LLMClient()
        self.memory = MemoryClient()
        self.registry = RegistryClient()  # ws2: 供 AgentClient 动态解析 endpoint
        # 跨 Agent 协作客户端（延迟初始化，避免循环依赖）
        self._agents = None
        # 跨 Agent 调用的 channel 缓存：按 endpoint 复用 keepalive 连接，
        # 避免每次协作调用新建且不关闭导致的连接泄漏（AgentClient 每请求新建，故缓存在此长生命周期对象上）
        self._agent_channels: dict = {}

    @property
    def agents(self):
        """跨 Agent 协作客户端。从当前请求 meta 读取 call_depth/call_stack，
        使 MAX_DEPTH/环检测跨进程生效。ws2: 注入 RegistryClient。"""
        from .agent_client import AgentClient
        meta = _current_meta.get()
        if meta is not None:
            depth = int(meta.get("call_depth", 0))
            stack = [s for s in meta.get("call_stack", "").split(",") if s]
            return AgentClient(caller=self, call_depth=depth, call_stack=stack,
                               registry=self.registry, parent_meta=meta)
        # 无 meta（本地测试 / 非 gRPC 调用）→ 默认深度 0
        if self._agents is None:
            self._agents = AgentClient(caller=self, registry=self.registry)
        return self._agents

    @abstractmethod
    async def handle(self, intent: IntentView, ctx: Context, meta: dict) -> AgentResult:
        """处理一个意图，返回 AgentResult。这是业务唯一必须实现的方法。"""
        ...

    async def handle_stream(self, intent: IntentView, ctx: Context, meta: dict):
        """流式执行。默认调 handle 并包成单个 final 事件；需要流式话术的 Agent 可重写。
        yield 形如 ("speech", str) 或 ("final", AgentResult)。
        """
        res = await self.handle(intent, ctx, meta)
        yield ("final", res)

    async def on_start(self) -> None:
        """可选生命周期钩子：serve() 启动 gRPC 服务后调用一次（后台任务）。

        响应式 Agent（如订阅 NATS 做主动播报的 road-safety）在此启动后台循环；
        默认无操作。失败由 serve() 静默吞掉，不影响 Agent 正常请求-响应服务。
        """
        return None

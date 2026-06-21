"""BaseAgent: 业务 Agent 的基类。子类只需实现 handle()。

Phase 1：注入 AgentClient（跨 Agent 协作）。
护栏跨进程修复：server.Execute 在调 handle 前把 request.meta 中的
call_depth/call_stack 写入 _current_meta contextvar，agents 属性读取
它构造正确深度的 AgentClient，使 MAX_DEPTH/环检测跨进程生效。

ws2 P0：注入 RegistryClient，AgentClient 经 Registry 动态解析 endpoint。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass

from .clients import LLMClient, MemoryClient, RegistryClient
from .manifest import load_manifest
from .result import AgentResult

# server.Execute 在调 handle 前设置，agents 属性读取——跨进程 depth/stack 传递
_current_meta: ContextVar[dict] = ContextVar("_current_meta", default=None)


def _set_current_meta(meta: dict | None) -> None:
    """server.py 调用：在 handle() 前设置当前请求的 meta。"""
    _current_meta.set(meta)


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


class BaseAgent(ABC):
    def __init__(self, manifest_path: str):
        self.manifest = load_manifest(manifest_path)
        self.llm = LLMClient()
        self.memory = MemoryClient()
        self.registry = RegistryClient()  # ws2: 供 AgentClient 动态解析 endpoint
        # 跨 Agent 协作客户端（延迟初始化，避免循环依赖）
        self._agents = None

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
                               registry=self.registry)
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

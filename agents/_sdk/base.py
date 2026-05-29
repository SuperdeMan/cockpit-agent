"""BaseAgent: 业务 Agent 的基类。子类只需实现 handle()。

Phase 1：注入 AgentClient（跨 Agent 协作）。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .clients import LLMClient, MemoryClient
from .manifest import load_manifest
from .result import AgentResult


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
        # 跨 Agent 协作客户端（延迟初始化，避免循环依赖）
        self._agents = None

    @property
    def agents(self):
        """跨 Agent 协作客户端。Agent 内可通过 self.agents.call(...) 调用其他 Agent。"""
        if self._agents is None:
            from .agent_client import AgentClient
            self._agents = AgentClient(caller=self)
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

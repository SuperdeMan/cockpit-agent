"""Cockpit Agent SDK.

继承 BaseAgent，实现 handle()，配一份 manifest.yaml，即可成为一个标准 Agent。
gRPC 契约、注册发现、健康检查、LLM/Memory 客户端均由 SDK 提供。
Phase 1：支持 AgentClient 跨 Agent 协作。
"""
from .base import BaseAgent, Context, IntentView
from .result import AgentResult, OK, NEED_CONFIRM, NEED_SLOT, FAILED, REJECTED
from .server import serve
from .agent_client import AgentClient

__all__ = [
    "BaseAgent", "Context", "IntentView", "AgentResult", "serve", "AgentClient",
    "OK", "NEED_CONFIRM", "NEED_SLOT", "FAILED", "REJECTED",
]

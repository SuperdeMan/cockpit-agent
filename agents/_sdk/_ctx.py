"""当前请求 meta 的 contextvar（中立模块）。

server.Execute/ExecuteStream 在调 handle 前把整份 request.meta 写入这里；
- base.AgentClient 读 call_depth/call_stack（跨进程深度/环检测护栏）；
- clients.LLMClient 读 thinking（复杂任务动态开思考，无需改各 Agent 业务码）。

抽到独立模块是为了解开 base ↔ clients 的循环依赖（base import clients，clients 又要读它）。
"""
from __future__ import annotations
from contextvars import ContextVar

# 默认 None：非 gRPC 调用（本地单测）下读取返回 None，调用方按"无 meta"处理。
_current_meta: ContextVar[dict | None] = ContextVar("_current_meta", default=None)


def set_current_meta(meta: dict | None) -> None:
    """server.py 在 handle() 前后调用：设置/清空当前请求 meta。"""
    _current_meta.set(meta)


def get_current_meta() -> dict | None:
    """读当前请求 meta（无则 None）。"""
    return _current_meta.get()

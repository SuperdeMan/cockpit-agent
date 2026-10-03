"""本次 Agent 请求的主体与授权（CA2-17）。

SDK server 在 Execute 入口绑定、出口复位；需要把主体带给下游执行层的客户端（如支付网关）从这里取，
不从 Agent 传的参数取——Agent 不能替别的用户发起或查询支付。
"""
from __future__ import annotations

import contextvars

_caller: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "agent_request_caller", default=("", ""))


def bind_caller(user_id: str, granted_scopes: str) -> contextvars.Token:
    return _caller.set((str(user_id or "").strip(), str(granted_scopes or "").strip()))


def reset_caller(token: contextvars.Token) -> None:
    try:
        _caller.reset(token)
    except ValueError:
        # 流式处理被取消 / 回收时 finally 可能跑在别的 Context：退回空值，绝不把主体留给后续请求
        _caller.set(("", ""))


def caller_metadata() -> list[tuple[str, str]]:
    """`x-user-id` / `x-granted-scopes`：请求外（没绑定）为空，下游据此拒绝。"""
    user_id, granted = _caller.get()
    md: list[tuple[str, str]] = []
    if user_id:
        md.append(("x-user-id", user_id))
    if granted:
        md.append(("x-granted-scopes", granted))
    return md

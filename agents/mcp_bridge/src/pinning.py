"""工具指纹在调用期仍然成立（CA2-17 S3 V1）。

启动时的准入只证明「那一刻」服务器给的工具合同与 `servers.yaml` 钉的指纹一致。远端服务器可以在运行中换一版：
会话失效后重新握手、发来 tools/list_changed，或者什么信号都不给。`PinnedClient` 包住每台服务器的客户端，
在调用前按这些时机重新拉 tools/list 比对：

- 会话重新握手之后（HTTP 客户端在重试原请求之前回调 `on_session_renewed`）；
- 收到 tools/list_changed 之后（客户端只置标记）；
- 读工具距上次核对超过 `READ_MAX_AGE_S`，写工具超过 `WRITE_MAX_AGE_S`。

对不上（指纹变了或工具没了）⇒ 该工具在本进程内停用，要人工复核、重新钉指纹并重启才恢复，记 ERROR；调用在发出前拒，
写调用因此是「确定未发出」。核对本身失败（网络错）不停用任何工具，本次调用按原来的错误语义失败。
服务器自报的版本只记录，变了打告警、不拒——真正的合同是工具指纹。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time

from .admission import tool_fingerprint
from .mcp_client import McpError

logger = logging.getLogger("agent.mcp_bridge.pinning")

READ_MAX_AGE_S = 30 * 60
WRITE_MAX_AGE_S = 60

# 本任务正在调用的工具 / 正在核对：会话重新握手的回调在同一个任务里执行，靠它们知道要拒哪个调用、避免核对时自我重入
_CURRENT_TOOL: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_pinning_tool", default="")
_VERIFYING: contextvars.ContextVar[bool] = contextvars.ContextVar("mcp_pinning_verifying", default=False)


class ToolDrift(McpError):
    """工具合同与钉的指纹对不上（或不在准入清单里）：调用没有发出。"""


class PinnedClient:
    """包住一台服务器的 MCP 客户端，调用前保证被调的工具仍是准入时钉的那一版。其余属性原样透传。"""

    def __init__(self, inner, server_id: str, pins: dict[str, str], writes, *, clock=time.monotonic):
        self._inner = inner
        self.server_id = server_id
        self._pins = dict(pins)
        self._writes = set(writes)
        self._clock = clock
        self._verified_at = clock()
        self._lock = asyncio.Lock()
        self.disabled: dict[str, str] = {}
        self._version = str((getattr(inner, "server_info", None) or {}).get("version", ""))
        if hasattr(inner, "on_session_renewed"):
            inner.on_session_renewed = self._after_renewal

    # ── 透传 ───────────────────────────────────────────────────────
    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def healthy(self) -> bool:
        return bool(getattr(self._inner, "healthy", False))

    @property
    def alive(self) -> bool:
        return bool(getattr(self._inner, "alive", False))

    async def start(self) -> None:
        await self._inner.start()

    async def close(self) -> None:
        await self._inner.close()

    async def initialize(self) -> dict:
        return await self._inner.initialize()

    async def list_tools(self) -> list[dict]:
        return await self._inner.list_tools()

    # ── 调用 ───────────────────────────────────────────────────────
    async def call_tool(self, name: str, arguments: dict, timeout_s: float | None = None, *,
                        retry_on_session_loss: bool = True) -> dict:
        await self._ensure_current(name)
        token = _CURRENT_TOOL.set(name)
        try:
            return await self._inner.call_tool(name, arguments, timeout_s=timeout_s,
                                               retry_on_session_loss=retry_on_session_loss)
        finally:
            _CURRENT_TOOL.reset(token)

    async def _ensure_current(self, name: str) -> None:
        if name not in self._pins:
            raise ToolDrift(f"{self.server_id}: {name} 不在准入清单里")
        max_age = WRITE_MAX_AGE_S if name in self._writes else READ_MAX_AGE_S
        if (getattr(self._inner, "tools_changed", False)
                or self._clock() - self._verified_at > max_age):
            await self._verify()
        self._refuse_if_disabled(name)

    def _refuse_if_disabled(self, name: str) -> None:
        if name in self.disabled:
            raise ToolDrift(f"{self.server_id}: {name} 的接口与准入时不一致，已停用")

    async def _after_renewal(self) -> None:
        """会话重新握手之后、重试原请求之前：服务器可能换了一版，先核对。"""
        if _VERIFYING.get():
            return          # 核对自己的 tools/list 触发了重新握手：外层核对拿到的就是新会话的列表
        await self._verify()
        name = _CURRENT_TOOL.get()
        if name:
            self._refuse_if_disabled(name)

    async def _verify(self) -> None:
        async with self._lock:
            token = _VERIFYING.set(True)
            try:
                if hasattr(self._inner, "tools_changed"):
                    self._inner.tools_changed = False
                offered = await self._inner.list_tools()
            finally:
                _VERIFYING.reset(token)
            self._compare(offered)
            self._verified_at = self._clock()

    def _compare(self, offered: list) -> None:
        by_name = {tool.get("name"): tool for tool in offered or [] if isinstance(tool, dict)}
        for name, pin in self._pins.items():
            if name in self.disabled or not pin:
                continue        # 没钉指纹的只有本地演示工具（准入层的例外），无从比对
            found = by_name.get(name)
            if found is None:
                reason = "missing"
            elif tool_fingerprint(found) != pin:
                reason = "fingerprint"
            else:
                continue
            self.disabled[name] = reason
            logger.error("[mcp:%s] 工具 %s 的合同与准入时不一致（%s），已停用；人工复核后重新钉指纹并重启",
                         self.server_id, name, reason)
        version = str((getattr(self._inner, "server_info", None) or {}).get("version", ""))
        if version != self._version:
            logger.warning("[mcp:%s] 服务器自报版本变了（%r → %r）；工具指纹仍是准入依据",
                           self.server_id, self._version, version)
            self._version = version

"""MCP stdio 客户端（M3 P2）——JSON-RPC 2.0 over stdin/stdout，换行分帧。

只实现 `initialize` / `tools/list` / `tools/call` 三个方法：本轮 MCP 只做**生态桥**，
resources/prompts/sampling 与 HTTP/SSE transport 都不做（子 RFC §7）。

子进程隔离：server 崩了只影响它自己的工具，桥本身照常服务其余 server
（`healthy` 为 False，那批 capability 不再可用——**不静默返回假数据**）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

logger = logging.getLogger("agent.mcp_bridge.client")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "cockpit-mcp-bridge", "version": "0.1.0"}


class McpError(RuntimeError):
    pass


class StdioMcpClient:
    def __init__(self, server_id: str, command: list[str], *,
                 timeout_s: float = 20.0, env: dict | None = None):
        self.server_id = server_id
        self._command = list(command)
        self._timeout = timeout_s
        self._env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()          # stdio 是单条串行管道，请求必须排队
        self.server_info: dict = {}
        self.healthy = False

    # ── 生命周期 ──────────────────────────────────────────────────────
    async def start(self) -> None:
        # PYTHONIOENCODING 钉死子进程 stdio 编码：MCP 帧是 UTF-8 JSON，
        # 让 server 去撞平台默认编码是自找的解码错（Windows cp936 首跑即中招）。
        env = {**os.environ, **(self._env or {}), "PYTHONIOENCODING": "utf-8"}
        self._proc = await asyncio.create_subprocess_exec(
            *self._command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env)
        asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        """server 的 stderr 收进日志——不收的话管道写满会把 server 挂死。"""
        if not self._proc or not self._proc.stderr:
            return
        with contextlib.suppress(Exception):
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                logger.info("[mcp:%s] %s", self.server_id,
                            line.decode("utf-8", "replace").rstrip())

    async def close(self) -> None:
        self.healthy = False
        if not self._proc:
            return
        with contextlib.suppress(Exception):
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── JSON-RPC ─────────────────────────────────────────────────────
    async def _send(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise McpError(f"{self.server_id}: 子进程未启动")
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict | None = None,
                       timeout_s: float | None = None) -> dict:
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                              "params": params or {}})
            deadline = timeout_s if timeout_s is not None else self._timeout
            while True:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=deadline)
                if not line:
                    self.healthy = False
                    raise McpError(f"{self.server_id}: 子进程已退出")
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue                       # server 打的非协议噪声，跳过
                if msg.get("id") != rid:
                    continue                       # 通知/乱序响应
                if "error" in msg:
                    err = msg["error"] or {}
                    raise McpError(f"{self.server_id}: [{err.get('code')}] "
                                   f"{err.get('message')}")
                return msg.get("result") or {}

    async def _notify(self, method: str, params: dict | None = None) -> None:
        async with self._lock:
            await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── 三个方法 ──────────────────────────────────────────────────────
    async def initialize(self) -> dict:
        result = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self.server_info = result.get("serverInfo") or {}
        await self._notify("notifications/initialized")
        self.healthy = True
        return result

    async def list_tools(self) -> list[dict]:
        return (await self._request("tools/list")).get("tools") or []

    async def call_tool(self, name: str, arguments: dict,
                        timeout_s: float | None = None) -> dict:
        """返回 `{"ok": bool, "text": str, "data": dict}`。

        MCP 的 `content` 是给人看的文本块；结构化结果放 `structuredContent`（协议可选）。
        两者都取：文本进话术，结构化进业务判断（**拿不到结构化就不假装成功**）。
        """
        result = await self._request("tools/call",
                                     {"name": name, "arguments": arguments},
                                     timeout_s=timeout_s)
        texts = [c.get("text", "") for c in (result.get("content") or [])
                 if isinstance(c, dict) and c.get("type") == "text"]
        return {"ok": not result.get("isError", False),
                "text": "\n".join(t for t in texts if t),
                "data": result.get("structuredContent") or {}}

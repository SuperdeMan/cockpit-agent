"""MCP 客户端（M3 P2 stdio；批 3 +streamable_http）——JSON-RPC 2.0。

只实现 `initialize` / `tools/list` / `tools/call` 三个方法：本轮 MCP 只做**生态桥**，
resources/prompts/sampling 不做（子 RFC §7）。streamable_http 于 2026-08-11 批 3
按 §9.9 解封（仅官方商户远程端点——瑞幸/麦当劳平台托管 MCP；准入姿态不变）。

子进程隔离（stdio）：server 崩了只影响它自己的工具，桥本身照常服务其余 server
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


def parse_tool_result(result: dict) -> dict:
    """tools/call 结果归一：`{"ok", "text", "data"}`（stdio 与 http 共用同一口径）。

    MCP 的 `content` 是给人看的文本块；结构化结果放 `structuredContent`（协议可选）。
    两者都取：文本进话术，结构化进业务判断（**拿不到结构化就不假装成功**）。
    """
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content
             if isinstance(c, dict) and c.get("type") == "text"]
    structured = result.get("structuredContent")
    data = structured if isinstance(structured, dict) else {}
    allow_text_fallback = structured is None or structured == {}
    if (allow_text_fallback and not data and len(content) == 1 and
            len(texts) == 1 and isinstance(texts[0], str)):
        def _reject_duplicate_keys(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("duplicate JSON object key")
                obj[key] = value
            return obj

        def _reject_nonstandard_constant(value):
            raise ValueError(f"non-standard JSON constant: {value}")

        try:
            candidate = json.loads(
                texts[0], object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant)
        except (json.JSONDecodeError, ValueError, TypeError):
            candidate = None
        if isinstance(candidate, dict):
            data = candidate
    return {"ok": not result.get("isError", False),
            "text": "\n".join(t for t in texts if t),
            "data": data}


class McpError(RuntimeError):
    pass


class McpTimeout(McpError):
    """MCP transport timeout with a conservative request-delivery marker."""

    def __init__(self, message: str, *, sent: bool):
        super().__init__(message)
        self.sent = bool(sent)


def _rpc_error(server_id: str, error) -> McpError:
    """Keep only the protocol code; remote messages are untrusted free text."""
    code = error.get("code") if isinstance(error, dict) else None
    safe_code = code if isinstance(code, int) and not isinstance(code, bool) else "unknown"
    return McpError(f"{server_id}: JSON-RPC error code={safe_code}")


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
                    raise _rpc_error(self.server_id, msg["error"])
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
                        timeout_s: float | None = None, *,
                        retry_on_session_loss: bool = True) -> dict:
        """返回 `{"ok": bool, "text": str, "data": dict}`（口径见 parse_tool_result）。"""
        result = await self._request("tools/call",
                                     {"name": name, "arguments": arguments},
                                     timeout_s=timeout_s)
        return parse_tool_result(result)


class HttpMcpClient:
    """Streamable HTTP 传输（MCP 2025-06-18）——与 StdioMcpClient 鸭子同形。

    与 stdio 的关键语义差：HTTP 无子进程持久状态——`alive` 恒 True、`healthy`
    在首次握手成功后**保持 True**，单次网络失败按次抛错（读/写路径各自诚实
    话术），下次请求自动重试；不像 stdio「崩了整批能力死到重启」。商户平台
    重启丢会话（404）时按 MCP 规范**重新握手并重试一次**。

    安全：headers 里有 Bearer token——**日志与异常文本永不打印 headers**。
    """

    def __init__(self, server_id: str, url: str, headers: dict[str, str] | None = None,
                 *, timeout_s: float = 20.0):
        self.server_id = server_id
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout_s
        self._http = None
        self._session_id = ""
        self._next_id = 0
        self._lock = asyncio.Lock()
        self.server_info: dict = {}
        self.healthy = False

    # ── 生命周期（与 stdio 同形）──────────────────────────────────
    async def start(self) -> None:
        import httpx
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=self._timeout,
                                  write=10.0, pool=5.0),
            trust_env=True)          # 出站经 HTTPS_PROXY（egress 白名单代理）

    async def close(self) -> None:
        self.healthy = False
        if self._http is not None:
            with contextlib.suppress(Exception):
                await self._http.aclose()
            self._http = None

    @property
    def alive(self) -> bool:
        return self._http is not None

    # ── JSON-RPC over Streamable HTTP ────────────────────────────
    def _post_headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self._headers,
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_sse(text: str, rid: int) -> dict | None:
        """SSE 流里找匹配 id 的 JSON-RPC response（逐 data: 帧；多行 data 拼接）。"""
        for block in text.split("\n\n"):
            data_lines = [ln[5:].lstrip() for ln in block.splitlines()
                          if ln.startswith("data:")]
            if not data_lines:
                continue
            try:
                msg = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                return msg
        return None

    async def _post(self, payload: dict, timeout_s: float | None):
        import httpx
        if self._http is None:
            raise McpError(f"{self.server_id}: HTTP 客户端未启动")
        sanitized_error = None
        try:
            return await self._http.post(
                self._url, content=json.dumps(payload, ensure_ascii=False).encode(),
                headers=self._post_headers(),
                timeout=timeout_s if timeout_s is not None else self._timeout)
        except httpx.TimeoutException as e:
            sent = not isinstance(e, httpx.ConnectTimeout)
            sanitized_error = McpTimeout(
                f"{self.server_id}: HTTP 请求超时 {type(e).__name__}",
                sent=sent)
        except httpx.HTTPError as e:
            # 异常文本只带类型不带请求细节（headers 里有 token）
            sanitized_error = McpError(
                f"{self.server_id}: HTTP 请求失败 {type(e).__name__}")
        # Raise outside the handler so the secret-bearing httpx exception is not
        # reachable through __context__ (``from None`` only hides its display).
        raise sanitized_error

    async def _request(self, method: str, params: dict | None = None,
                       timeout_s: float | None = None, *,
                       retry_on_session_loss: bool = True,
                       _retried: bool = False) -> dict:
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
        resp = await self._post({"jsonrpc": "2.0", "id": rid, "method": method,
                                 "params": params or {}}, timeout_s)
        sid = resp.headers.get("Mcp-Session-Id", "")
        if sid:
            self._session_id = sid
        if (resp.status_code == 404 and self._session_id and
                retry_on_session_loss and not _retried):
            # 商户平台重启丢会话：按 MCP 规范重新握手，本请求重试一次
            logger.info("[mcp:%s] 会话失效，重新握手", self.server_id)
            self._session_id = ""
            await self.initialize()
            return await self._request(
                method, params, timeout_s,
                retry_on_session_loss=retry_on_session_loss, _retried=True)
        if resp.status_code >= 400:
            raise McpError(f"{self.server_id}: HTTP {resp.status_code}")
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype == "text/event-stream":
            msg = self._parse_sse(resp.text, rid)
            if msg is None:
                raise McpError(f"{self.server_id}: SSE 流中无 id={rid} 的响应")
        elif resp.text:
            invalid_json = False
            try:
                msg = json.loads(resp.text)
            except json.JSONDecodeError:
                invalid_json = True
            if invalid_json:
                # Raise after leaving the handler: JSONDecodeError.doc contains
                # the full remote response and must not remain as cause/context.
                raise McpError(f"{self.server_id}: 非法 JSON 响应")
        else:
            return {}                 # 202 Accepted（notification）
        if isinstance(msg, dict) and "error" in msg:
            raise _rpc_error(self.server_id, msg["error"])
        return (msg or {}).get("result") or {}

    async def _notify(self, method: str) -> None:
        """JSON-RPC notification（无 id；服务端 202 无 body）。"""
        with contextlib.suppress(McpError):
            await self._post({"jsonrpc": "2.0", "method": method, "params": {}},
                             None)

    # ── 三个方法（与 stdio 同形）─────────────────────────────────
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
                        timeout_s: float | None = None, *,
                        retry_on_session_loss: bool = True) -> dict:
        result = await self._request("tools/call",
                                     {"name": name, "arguments": arguments},
                                     timeout_s=timeout_s,
                                     retry_on_session_loss=retry_on_session_loss)
        return parse_tool_result(result)

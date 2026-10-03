"""StdioMcpClient（CA2-17 S3）：一次请求有总时限，吐噪声行拖不住；服务器的 list_changed 通知会被记下。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from agents.mcp_bridge.src.mcp_client import StdioMcpClient


class _Stdin:
    def write(self, data):
        pass

    async def drain(self):
        pass


class _Stdout:
    def __init__(self, lines, delay):
        self.lines = list(lines)
        self.delay = delay

    async def readline(self):
        await asyncio.sleep(self.delay)
        return self.lines.pop(0) if self.lines else b"not json, just noise\n"


class _Proc:
    returncode = None

    def __init__(self, stdout):
        self.stdin = _Stdin()
        self.stdout = stdout


def _client(lines, delay=0.01):
    client = StdioMcpClient("demo", ["python"], timeout_s=5)
    client._proc = _Proc(_Stdout(lines, delay))
    return client


def test_noisy_server_cannot_stretch_one_request_past_its_total_deadline():
    client = _client([], delay=0.05)
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(client._request("tools/list", timeout_s=0.3))
    assert time.monotonic() - started < 1.5


def test_list_changed_notification_is_recorded_and_the_reply_still_returned():
    notify = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}).encode() + b"\n"
    reply = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}).encode() + b"\n"
    client = _client([notify, reply])
    assert asyncio.run(client.list_tools()) == []
    assert client.tools_changed is True

"""AgentClient 跨进程护栏测试。

直接测试真实 AgentClient + base.py ContextVar 机制：
- 深度上限（MAX_DEPTH=2 跨进程生效）
- 环检测（call_stack 跨进程透传）
- port_map 含所有已注册 Agent（含 info=50067）
- _set_current_meta / _current_meta ContextVar 传递

不走 gRPC（mock 掉 channel），不依赖 proto 生成代码的运行时导入。
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import importlib
import sys
import os

# 确保能导入 agent_client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ─── 真实 AgentClient（通过 SDK 导入链）───

# 绕过 SDK __init__ 的全量 proto 导入，直接导入底层模块
from agents._sdk.agent_client import AgentClient, MAX_DEPTH
from agents._sdk.base import _set_current_meta, _current_meta


class _MockAgent:
    """最小化 Agent mock：有 manifest 即可构造 AgentClient。"""
    def __init__(self, agent_id="test-agent"):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []


# ─── ContextVar 跨进程传递测试 ───

def test_contextvar_set_and_read():
    """_set_current_meta 写入 ContextVar，_current_meta.get() 读出——跨层生效。"""
    _set_current_meta({"call_depth": "2", "call_stack": "a,b,c"})
    try:
        meta = _current_meta.get()
        assert meta is not None
        assert meta["call_depth"] == "2"
        assert meta["call_stack"] == "a,b,c"
    finally:
        _set_current_meta(None)


def test_contextvar_default_is_none():
    """未设置时 ContextVar 默认 None。"""
    _set_current_meta(None)
    assert _current_meta.get() is None


# ─── 深度上限（跨进程）───

def test_depth_at_limit_rejects():
    """AgentClient 在 call_depth >= MAX_DEPTH 时拒绝调用。"""
    agent = _MockAgent()
    client = AgentClient(caller=agent, call_depth=MAX_DEPTH, call_stack=[])
    result = asyncio.run(client.call("other", "other.intent", {}, timeout=0.1))
    assert result.status == "failed"
    assert "深度" in result.speech or "depth" in result.speech.lower()


def test_depth_below_limit_attempts():
    """call_depth < MAX_DEPTH 时 AgentClient 尝试调用（endpoint 解析失败也会标记 failed）。"""
    agent = _MockAgent()
    client = AgentClient(caller=agent, call_depth=0, call_stack=[])
    # 无 endpoint → resolve 返回 "" → "未找到 Agent"
    result = asyncio.run(client.call("nonexistent", "nonexistent.intent", {}, timeout=0.1))
    assert result.status == "failed"
    assert "未找到" in result.speech


# ─── 环检测（跨进程 call_stack 透传）───

def test_cycle_detected_from_stack():
    """call_stack 中已有目标 agent → 拒绝（跨进程环检测生效）。"""
    agent = _MockAgent(agent_id="planner")
    client = AgentClient(caller=agent, call_depth=1, call_stack=["navigation", "info"])
    result = asyncio.run(client.call("navigation", "navigation.search_poi", {}, timeout=0.1))
    assert result.status == "failed"
    assert "循环" in result.speech


def test_self_call_detected():
    """调用自己 → 拒绝。"""
    agent = _MockAgent(agent_id="agent-a")
    client = AgentClient(caller=agent, call_depth=0, call_stack=[])
    result = asyncio.run(client.call("agent-a", "agent-a.intent", {}, timeout=0.1))
    assert result.status == "failed"
    assert "循环" in result.speech


# ─── port_map 含已注册 Agent ───

def test_port_map_has_info():
    """port_map 必须含 info=50067（否则别的 agent 协作调不到天气/搜索等能力）。"""
    agent = _MockAgent()
    client = AgentClient(caller=agent)
    # _resolve_endpoint 是实例方法，通过内部 port_map 解析
    endpoint = client._resolve_endpoint("info")
    assert endpoint == "localhost:50067"


def test_port_map_has_navigation():
    endpoint = AgentClient(caller=_MockAgent())._resolve_endpoint("navigation")
    assert endpoint == "localhost:50061"


def test_port_map_unknown_returns_empty():
    endpoint = AgentClient(caller=_MockAgent())._resolve_endpoint("nonexistent")
    assert endpoint == ""


# ─── meta 透传 call_depth/call_stack ───

def test_meta_contains_depth_and_stack():
    """AgentClient.call() 构建的 ExecuteRequest.meta 应含 call_depth/call_stack。"""
    agent = _MockAgent(agent_id="trip-planner")
    client = AgentClient(caller=agent, call_depth=1, call_stack=["planner"])

    captured_meta = {}

    async def fake_call():
        with patch("agents._sdk.agent_client.grpc") as mock_grpc:
            mock_stub = MagicMock()
            mock_stub.Execute = AsyncMock(side_effect=Exception("capture meta"))
            mock_ch = MagicMock()
            mock_grpc.aio.insecure_channel.return_value = mock_ch

            with patch("agents._sdk.agent_client.agent_pb2_grpc.AgentStub", return_value=mock_stub):
                try:
                    await client.call("navigation", "navigation.search_poi", {}, timeout=0.1)
                except Exception:
                    pass
            # 捕获发送的 meta
            if mock_stub.Execute.called:
                req = mock_stub.Execute.call_args[0][0]
                captured_meta.update(dict(req.meta))

    asyncio.run(fake_call())
    assert captured_meta.get("call_depth") == "2"  # depth+1
    stack = captured_meta.get("call_stack", "")
    assert "planner" in stack
    assert "trip-planner" in stack


# ─── fork 增加深度 ───

def test_fork_increments_depth():
    agent = _MockAgent(agent_id="a")
    client = AgentClient(caller=agent, call_depth=0, call_stack=[])
    child = client.fork("b")
    assert child._depth == 1
    assert "a" in child._stack

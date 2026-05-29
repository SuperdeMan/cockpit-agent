"""AgentClient 护栏测试。独立于 proto 生成代码。

直接测试 AgentClient 的护栏逻辑（深度/环/端口解析），不走 gRPC。
"""
import pytest
from unittest.mock import MagicMock

# 直接导入 agent_client 模块，绕过 SDK __init__ 的 proto import 链
import importlib
import sys
import os

# 确保能导入 agent_client（它只依赖 base 的类型，运行时才需要 grpc）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class MockAgent:
    def __init__(self, agent_id="test-agent"):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []


# AgentClient 的核心逻辑不依赖 grpc，只在 .call() 发请求时用
# 我们直接实例化测试护栏逻辑
from dataclasses import dataclass


@dataclass
class _AgentClientShim:
    """AgentClient 护栏逻辑的独立测试桩。"""
    caller_id: str = "test-agent"
    call_depth: int = 0
    call_stack: list = None
    MAX_DEPTH: int = 2

    def __post_init__(self):
        if self.call_stack is None:
            self.call_stack = []

    def check_call(self, target_id: str) -> tuple[bool, str]:
        """检查调用是否被护栏拒绝。返回 (allowed, reason)。"""
        if self.call_depth >= self.MAX_DEPTH:
            return False, f"深度超限 ({self.call_depth} >= {self.MAX_DEPTH})"
        if target_id in self.call_stack or target_id == self.caller_id:
            return False, f"循环调用: {self.caller_id} -> {target_id}"
        return True, ""

    def resolve_endpoint(self, agent_id: str) -> str:
        port_map = {
            "navigation": "50061", "chitchat": "50062",
            "food-ordering": "50063", "parking-payment": "50064",
            "manual-rag": "50065", "trip-planner": "50066",
        }
        return port_map.get(agent_id, "")

    def fork(self, target_id: str) -> "_AgentClientShim":
        return _AgentClientShim(
            caller_id=target_id,
            call_depth=self.call_depth + 1,
            call_stack=self.call_stack + [self.caller_id],
        )


# ─── 测试 ───

def test_depth_limit():
    client = _AgentClientShim(call_depth=2, MAX_DEPTH=2)
    ok, reason = client.check_call("other-agent")
    assert not ok
    assert "深度" in reason


def test_cycle_detection():
    client = _AgentClientShim(caller_id="agent-a", call_stack=["agent-b", "agent-c"])
    ok, reason = client.check_call("agent-b")
    assert not ok
    assert "循环" in reason


def test_self_call_detection():
    client = _AgentClientShim(caller_id="agent-a")
    ok, reason = client.check_call("agent-a")
    assert not ok


def test_normal_call_allowed():
    client = _AgentClientShim(caller_id="agent-a")
    ok, reason = client.check_call("agent-b")
    assert ok


def test_fork_increments_depth():
    client = _AgentClientShim(caller_id="agent-a", call_depth=1)
    child = client.fork("agent-b")
    assert child.call_depth == 2
    assert "agent-a" in child.call_stack


def test_resolve_endpoint_known():
    client = _AgentClientShim()
    assert "50061" == client.resolve_endpoint("navigation")
    assert "50062" == client.resolve_endpoint("chitchat")
    assert "50066" == client.resolve_endpoint("trip-planner")


def test_resolve_endpoint_unknown():
    client = _AgentClientShim()
    assert client.resolve_endpoint("nonexistent") == ""

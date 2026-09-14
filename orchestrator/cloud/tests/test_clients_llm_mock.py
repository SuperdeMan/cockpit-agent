"""`Clients.llm_served_by_mock`：F09 例外①的判据来源。

读的是网关在 `CompleteResponse.model_used` 里自报的 provider 身份，不是话术前缀；
没打过网关时 fail-closed（当真模型）。`llm_complete` 与 `llm_complete_tools` 两条
调用路径都要更新读数——planner 规划轮走 tools 路径、aggregator 走纯文本路径，
漏掉一条就会在某种栈形态下读到过期值。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os

from cockpit.llm.v1 import llm_pb2

from orchestrator.cloud.clients import _MOCK_MODEL_USED, Clients


class _Stub:
    def __init__(self, model_used: str):
        self.model_used = model_used

    async def Complete(self, request, timeout=None):
        return llm_pb2.CompleteResponse(
            content=f"[{self.model_used}] 回显", model_used=self.model_used,
            finish_reason="stop")


def _serve(clients, model_used):
    clients._llm_stub = lambda: _Stub(model_used)


def test_fail_closed_before_any_gateway_call():
    assert Clients().llm_served_by_mock() is False


def test_tools_path_reads_the_gateway_reported_provider():
    clients = Clients()
    _serve(clients, "mock")
    asyncio.run(clients.llm_complete_tools(
        [{"role": "user", "content": "讲个笑话"}], {"tools": []}))
    assert clients.llm_served_by_mock() is True


def test_plain_path_reads_the_gateway_reported_provider():
    clients = Clients()
    _serve(clients, "mock")
    asyncio.run(clients.llm_complete([{"role": "user", "content": "讲个笑话"}]))
    assert clients.llm_served_by_mock() is True


def test_real_provider_is_never_mistaken_for_mock():
    clients = Clients()
    _serve(clients, "MiniMax-M3")
    asyncio.run(clients.llm_complete([{"role": "user", "content": "讲个笑话"}]))
    assert clients.llm_served_by_mock() is False


def test_mock_model_id_matches_the_gateway_declaration():
    """跨进程对账声明源：planner 认的 `"mock"` 必须就是 llm-gateway MockProvider 在
    `model_used` 里自报的那个值；两边任何一边改名，这条先红。planner 规划轮走的是
    `complete_tools`（BaseProvider 缺省回落 `complete` + 空 tool_calls），两条都对。
    按文件路径独名加载，不用裸 `import providers`（通用名劫持坑，同 llm-gateway/tests）。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "llm-gateway", "providers.py")
    spec = importlib.util.spec_from_file_location(
        "llm_gateway_providers_mock_id_under_test", path)
    providers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(providers)
    mock = providers.MockProvider()
    msgs = [{"role": "user", "content": "讲个笑话"}]

    _, used, *_ = asyncio.run(mock.complete(msgs, "mock", 0.3, 100))
    assert used == _MOCK_MODEL_USED
    content, used_tools, _, _, calls = asyncio.run(
        mock.complete_tools(msgs, "mock", 0.3, 100, tools=[{"type": "function"}]))
    assert used_tools == _MOCK_MODEL_USED
    assert calls == [] and content.startswith("[mock]")

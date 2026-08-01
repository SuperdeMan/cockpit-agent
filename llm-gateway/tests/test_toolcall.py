"""M1a submit_plan 结构化输出（RFC docs/design/2026-07-24-m1a-provider-toolcall-rfc.md）：
provider tools 注入 / tool_calls 归一化 / server Struct 透传回填 / 缓存跳过。

不起 gRPC server：假 provider/假 context 直驱 LLMGatewayServicer（同 test_server_degrade 模式）。
"""
import asyncio
import importlib.util
import os
import sys

from google.protobuf.json_format import MessageToDict

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)

from cockpit.llm.v1 import llm_pb2  # noqa: E402
from providers import (  # noqa: E402
    AnthropicProvider, MockProvider, OpenAICompatibleProvider, normalize_tool_calls,
)

# 不用 `import server`：裸模块名会劫持 orchestrator/edge tests 的同名 import
# （通用名劫持坑）——按文件路径独名加载，零污染。
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_server_toolcall_under_test", os.path.join(_DIR, "server.py"))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


# ── normalize_tool_calls：OpenAI 形状 → 网关统一形状 ──

def test_normalize_openai_shape_arguments_string_to_dict():
    calls = normalize_tool_calls([
        {"id": "c1", "type": "function",
         "function": {"name": "submit_plan",
                      "arguments": '{"addressed": true, "steps": []}'}},
    ])
    assert calls == [{"id": "c1", "name": "submit_plan",
                      "arguments": {"addressed": True, "steps": []}}]


def test_normalize_malformed_arguments_dropped_not_salvaged():
    """畸形 arguments 丢弃该条（不做字符串抢救，RFC §8-3）；合法条保留。"""
    calls = normalize_tool_calls([
        {"id": "c1", "function": {"name": "submit_plan", "arguments": '{"steps": [tru'}},
        {"id": "c2", "function": {"name": "submit_plan", "arguments": '{"ok": 1}'}},
    ])
    assert len(calls) == 1
    assert calls[0]["id"] == "c2"


def test_normalize_object_arguments_tolerated():
    """个别服务商 arguments 直接给 object 的宽容接收。"""
    calls = normalize_tool_calls([
        {"id": "c1", "function": {"name": "f", "arguments": {"a": 1}}}])
    assert calls[0]["arguments"] == {"a": 1}


def test_normalize_non_object_and_missing_name_dropped():
    calls = normalize_tool_calls([
        {"id": "c1", "function": {"name": "f", "arguments": "[1,2]"}},   # 非 object
        {"id": "c2", "function": {"arguments": "{}"}},                    # 无 name
        "not-a-dict",
    ])
    assert calls == []


def test_normalize_empty_inputs():
    assert normalize_tool_calls(None) == []
    assert normalize_tool_calls([]) == []
    # arguments 缺省 → 空 dict（named 强制下部分服务商可能省略空参数）
    calls = normalize_tool_calls([{"id": "c", "function": {"name": "f"}}])
    assert calls == [{"id": "c", "name": "f", "arguments": {}}]


# ── BaseProvider.complete_tools 默认回落（Mock/未覆盖 provider fail-open）──

def test_base_complete_tools_falls_back_to_complete():
    p = MockProvider()
    content, used, finish, usage, calls = asyncio.run(p.complete_tools(
        [{"role": "user", "content": "你好"}], "m", 0.3, 128,
        tools=[{"type": "function", "function": {"name": "submit_plan"}}],
        tool_choice="auto"))
    assert calls == []
    assert "你好" in content          # mock 回显=complete 原行为


# ── OpenAICompatibleProvider.complete_tools ──

def _openai_provider():
    return OpenAICompatibleProvider(
        "key", base_url="http://fake/v1/chat/completions",
        auth_style="bearer", token_param="max_tokens", thinking_style="none")


_TOOLS = [{"type": "function", "function": {
    "name": "submit_plan", "description": "d",
    "parameters": {"type": "object", "properties": {}}}}]
_NAMED = {"type": "function", "function": {"name": "submit_plan"}}


def test_openai_compatible_injects_tools_and_parses_response(monkeypatch):
    p = _openai_provider()
    seen = {}

    async def fake_post(body, timeout_s):
        seen.update(body)
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "submit_plan",
                "arguments": '{"addressed": true, "steps": []}'}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(p, "_post_chat", fake_post)
    content, used, finish, usage, calls = asyncio.run(p.complete_tools(
        [{"role": "user", "content": "hi"}], "m", 0.3, 256,
        tools=_TOOLS, tool_choice=_NAMED))
    assert seen["tools"] == _TOOLS
    assert seen["tool_choice"] == _NAMED
    assert finish == "tool_calls"
    assert usage == (10, 5)
    assert calls == [{"id": "c1", "name": "submit_plan",
                      "arguments": {"addressed": True, "steps": []}}]
    assert content == ""              # content None → 空串归一


def test_openai_compatible_no_tools_falls_back_without_tools_key(monkeypatch):
    """tools 缺省 → 基类回落 complete：请求体不带 tools/tool_choice 键（零行为变化）。"""
    p = _openai_provider()
    seen = {}

    async def fake_post(body, timeout_s):
        seen.update(body)
        return {"choices": [{"message": {"content": "文本"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(p, "_post_chat", fake_post)
    content, used, finish, usage, calls = asyncio.run(p.complete_tools(
        [{"role": "user", "content": "hi"}], "m", 0.3, 256))
    assert "tools" not in seen and "tool_choice" not in seen
    assert content == "文本"
    assert calls == []


def test_openai_compatible_tools_path_strips_think_block(monkeypatch):
    """模型无视工具直接文本作答（含 <think> 内联泄漏）→ content 剥离后返回，calls 空。"""
    p = _openai_provider()

    async def fake_post(body, timeout_s):
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "<think>推理</think>\n\n{\"steps\": []}"}}], "usage": {}}

    monkeypatch.setattr(p, "_post_chat", fake_post)
    content, _, _, _, calls = asyncio.run(p.complete_tools(
        [{"role": "user", "content": "hi"}], "m", 0.3, 256,
        tools=_TOOLS, tool_choice=_NAMED))
    assert content == '{"steps": []}'
    assert calls == []


# ── AnthropicProvider 形状转换（staticmethod，不实例化免 SDK 依赖）──

def test_anthropic_tool_shape_conversion():
    a_tools, a_choice = AnthropicProvider._to_anthropic_tools(_TOOLS, _NAMED)
    assert a_tools == [{"name": "submit_plan", "description": "d",
                        "input_schema": {"type": "object", "properties": {}}}]
    assert a_choice == {"type": "tool", "name": "submit_plan"}


def test_anthropic_tool_choice_variants():
    assert AnthropicProvider._to_anthropic_tools(_TOOLS, "required")[1] == {"type": "any"}
    assert AnthropicProvider._to_anthropic_tools(_TOOLS, "none")[1] == {"type": "none"}
    assert AnthropicProvider._to_anthropic_tools(_TOOLS, "auto")[1] is None
    assert AnthropicProvider._to_anthropic_tools(_TOOLS, None)[1] is None
    # parameters 缺省 → input_schema 兜底 object；无 name 的 tool 丢弃
    a_tools, _ = AnthropicProvider._to_anthropic_tools(
        [{"type": "function", "function": {"name": "f"}},
         {"type": "function", "function": {}}], None)
    assert a_tools == [{"name": "f", "description": "", "input_schema": {"type": "object"}}]


# ── server：Struct 透传 / 回填 / 缓存跳过 / 纯文本路径零波及 ──

class _Abort(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class _Ctx:
    def time_remaining(self):
        return 30.0

    async def abort(self, code, details):
        raise _Abort(code, details)


class _ToolFakeProvider:
    def __init__(self, tool_result=None):
        self.seen = None
        self.tool_calls_n = 0
        self.plain_calls_n = 0
        self._result = tool_result

    async def complete(self, msgs, model, temp, max_tokens, thinking=None, timeout_s=None):
        self.plain_calls_n += 1
        return ("plain", model, "stop", (1, 1))

    async def complete_tools(self, msgs, model, temp, max_tokens, tools=None,
                             tool_choice=None, thinking=None, timeout_s=None):
        self.tool_calls_n += 1
        self.seen = {"tools": tools, "tool_choice": tool_choice}
        return self._result


class _RT:
    active_id = "fake"

    def __init__(self, provider, models):
        self._p = provider
        self._m = models

    def active_provider(self):
        return self._p

    def resolve_models(self, requested):
        return list(self._m)

    def provider_entry(self, pid):
        return None

    def resolve_models_for(self, pid, requested, model_override=""):
        return list(self._m)


def _servicer(provider, models):
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(provider, models)
    return s


def _tools_req(text="hi"):
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role="user", content=text)])
    req.tools.update({"tools": _TOOLS, "tool_choice": _NAMED})
    return req


def test_server_passes_tools_and_fills_struct():
    p = _ToolFakeProvider(("", "m1", "tool_calls", (3, 4),
                          [{"id": "c1", "name": "submit_plan",
                            "arguments": {"addressed": True, "steps": []}}]))
    s = _servicer(p, ["m1"])
    resp = asyncio.run(s.Complete(_tools_req(), _Ctx()))
    assert resp.finish_reason == "tool_calls"
    assert resp.prompt_tokens == 3 and resp.completion_tokens == 4
    # provider 收到线格式原样（Struct round-trip 不改形状）
    assert p.seen["tools"][0]["function"]["name"] == "submit_plan"
    assert p.seen["tool_choice"]["function"]["name"] == "submit_plan"
    data = MessageToDict(resp.tool_calls)
    assert data["tool_calls"][0]["name"] == "submit_plan"
    assert data["tool_calls"][0]["arguments"] == {"addressed": True, "steps": []}


def test_server_tools_request_skips_cache():
    """带 tools 的同一请求两次 → provider 两次被调（缓存跳过，RFC §8-4）。"""
    p = _ToolFakeProvider(("", "m1", "tool_calls", (1, 1),
                          [{"id": "c", "name": "submit_plan", "arguments": {}}]))
    s = _servicer(p, ["m1"])
    asyncio.run(s.Complete(_tools_req("同句"), _Ctx()))
    asyncio.run(s.Complete(_tools_req("同句"), _Ctx()))
    assert p.tool_calls_n == 2


def test_server_plain_request_untouched():
    """无 tools 请求：complete_tools 不被调、响应无 tool_calls 字段（存量路径零波及）。"""
    p = _ToolFakeProvider()
    s = _servicer(p, ["m1"])
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role="user", content="hi")])
    resp = asyncio.run(s.Complete(req, _Ctx()))
    assert resp.content == "plain"
    assert p.tool_calls_n == 0 and p.plain_calls_n == 1
    assert not resp.HasField("tool_calls")


def test_server_empty_tool_calls_leaves_field_unset():
    """provider 返回空 tool_calls（模型文本作答）→ 响应字段不置位，content 照常。"""
    p = _ToolFakeProvider(('{"steps": []}', "m1", "stop", (1, 1), []))
    s = _servicer(p, ["m1"])
    resp = asyncio.run(s.Complete(_tools_req(), _Ctx()))
    assert resp.content == '{"steps": []}'
    assert not resp.HasField("tool_calls")


def test_server_tools_spec_malformed_falls_to_plain():
    """tools Struct 存在但无 "tools" 键 → 按纯文本路径处理（_tools_spec 返回 None）。"""
    p = _ToolFakeProvider()
    s = _servicer(p, ["m1"])
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role="user", content="hi")])
    req.tools.update({"whatever": 1})
    resp = asyncio.run(s.Complete(req, _Ctx()))
    assert resp.content == "plain"
    assert p.tool_calls_n == 0


# ── provider tool-calling 能力协商（M-D）──────────────────
def test_capability_defaults_to_supported_for_every_shipped_provider():
    """缺省 True：既有全部档位都支持 tool calling，**零行为变化**。"""
    from llm_runtime import LLMRuntime
    rt = LLMRuntime()
    assert rt.supports_toolcall("mimo") is True
    assert rt.supports_toolcall("minimax") is True
    assert rt.supports_toolcall("deepseek") is True


def test_unknown_provider_is_not_convicted():
    """不认识的档不定罪：照旧尝试，失败走既有降级——**缺能力位不等于没能力**。"""
    from llm_runtime import LLMRuntime
    assert LLMRuntime().supports_toolcall("no-such-provider") is True


def test_declared_unsupported_provider_short_circuits():
    """声明式：在 _PROVIDER_SPECS 里写一行即可，不改判定代码。"""
    from llm_runtime import LLMRuntime
    rt = LLMRuntime()
    rt._catalog.append({"id": "toyprov", "supports_toolcall": False})
    assert rt.supports_toolcall("toyprov") is False


def test_capability_is_read_per_request_so_hot_switch_is_not_stale():
    """provider 可热切——把能力缓存下来就会在切换后沿用旧能力。"""
    from llm_runtime import LLMRuntime
    rt = LLMRuntime()
    rt._catalog.append({"id": "toyprov", "supports_toolcall": True})
    assert rt.supports_toolcall("toyprov") is True
    rt._catalog[-1]["supports_toolcall"] = False
    assert rt.supports_toolcall("toyprov") is False

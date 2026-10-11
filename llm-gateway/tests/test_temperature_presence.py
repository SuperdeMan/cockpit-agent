"""温度「传了 0」与「没传」要分开（2026-10-11）。

`CompleteRequest.temperature` 改成 proto3 `optional` 之前是普通 float：0 不上线路，网关 `temperature or 0.7`
把「传了 0」当成「没传」——受话判定、手册目录路由、提醒时间抽取、场景编译首轮都要 0，实际都跑在 0.7。
这里钉住网关这一层：传了就原样给厂商（包括 0），没传才取缺省 0.7；非流式与流式同一口径。
"""
import asyncio
import importlib.util
import os
import sys

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)

from cockpit.llm.v1 import llm_pb2  # noqa: E402

# 按文件路径独名加载 server.py，避免裸模块名 `server` 劫持别的测试（同 test_server_degrade 的做法）
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_server_temperature", os.path.join(_DIR, "server.py"))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


class _Ctx:
    def time_remaining(self):
        return 30.0

    async def abort(self, code, details):
        raise AssertionError(f"unexpected abort {code}: {details}")


class _RecordingProvider:
    def __init__(self):
        self.temps: list[float] = []

    async def complete(self, msgs, model, temp, max_tokens, thinking=None, timeout_s=None):
        self.temps.append(temp)
        return "好", model, "stop", (1, 1)

    async def stream(self, msgs, model, temp, max_tokens, thinking=None, timeout_s=None):
        self.temps.append(temp)
        yield "好"


class _RT:
    active_id = "fake"

    def __init__(self, provider):
        self._p = provider

    def active_provider(self):
        return self._p

    def resolve_models(self, requested):
        return ["m1"]

    def provider_entry(self, pid):
        return None

    def resolve_models_for(self, pid, requested, model_override=""):
        return ["m1"]


def _servicer():
    provider = _RecordingProvider()
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(provider)
    return s, provider


def _req(text, **kw):
    return llm_pb2.CompleteRequest(messages=[llm_pb2.Message(role="user", content=text)], **kw)


def test_an_explicit_zero_reaches_the_provider_and_unset_means_the_default():
    s, p = _servicer()
    for text, kw, expected in (("零", {"temperature": 0.0}, 0.0), ("未传", {}, 0.7), ("零点三", {"temperature": 0.3}, 0.3)):
        asyncio.run(s.Complete(_req(text, **kw), _Ctx()))
        assert abs(p.temps[-1] - expected) < 1e-6, (kw, p.temps[-1])


def test_streaming_uses_the_same_rule():
    s, p = _servicer()

    async def drain(req):
        return [c async for c in s.CompleteStream(req, _Ctx())]

    asyncio.run(drain(_req("流零", temperature=0.0)))
    asyncio.run(drain(_req("流未传")))
    assert [round(t, 6) for t in p.temps] == [0.0, 0.7]


def test_a_zero_and_a_default_request_do_not_share_a_cache_entry():
    """缓存键带温度：同一句话传 0 与没传是两次独立调用，不能互相命中。"""
    s, p = _servicer()
    asyncio.run(s.Complete(_req("同一句", temperature=0.0), _Ctx()))
    asyncio.run(s.Complete(_req("同一句"), _Ctx()))
    assert [round(t, 6) for t in p.temps] == [0.0, 0.7]

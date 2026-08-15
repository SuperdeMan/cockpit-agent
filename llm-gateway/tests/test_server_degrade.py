"""运行时硬化 D3/D4 单测：429 语义分类、Retry-After 等待、流式首 token 前档位降级。

不起 gRPC server：假 provider/假 context 直驱 LLMGatewayServicer 的 Complete/CompleteStream。
"""
import asyncio
import importlib.util
import os
import sys

import grpc
import pytest

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)

from cockpit.llm.v1 import llm_pb2  # noqa: E402
from providers import ProviderHTTPError  # noqa: E402

# 不用 `import server`：裸模块名会注册进 sys.modules，全量跑时劫持
# orchestrator/edge tests 的 `from server import EdgeOrchestratorServicer`
# （通用名劫持坑，同「providers 包名劫持」教训）——按文件路径独名加载，零污染。
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_server_under_test", os.path.join(_DIR, "server.py"))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


class _Abort(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class _Ctx:
    def __init__(self, remaining=30.0):
        self._r = remaining

    def time_remaining(self):
        return self._r

    async def abort(self, code, details):
        raise _Abort(code, details)


class _FakeProvider:
    """complete_script 项 = 异常 或 (content, model, finish, usage)；
    stream_script 项 = list（元素为 str 增量或异常，遇异常即抛）。按调用顺序弹出。"""

    def __init__(self, complete_script=None, stream_script=None):
        self.calls: list[str] = []
        self.stream_calls: list[str] = []
        self._cs = list(complete_script or [])
        self._ss = list(stream_script or [])

    async def complete(self, msgs, model, temp, max_tokens, thinking=None, timeout_s=None):
        self.calls.append(model)
        step = self._cs.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def stream(self, msgs, model, temp, max_tokens, thinking=None, timeout_s=None):
        self.stream_calls.append(model)
        for item in self._ss.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


class _RT:
    active_id = "fake"

    def __init__(self, provider, models, entries=None):
        self._p = provider
        self._m = models
        self._entries = entries or {}    # pid -> provider（请求级 pin 用）

    def active_provider(self):
        return self._p

    def resolve_models(self, requested):
        return list(self._m)

    def provider_entry(self, pid):
        p = self._entries.get(pid)
        return (pid, p) if p is not None else None

    def resolve_models_for(self, pid, requested, model_override=""):
        return list(self._m)


def _servicer(provider, models):
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(provider, models)
    return s


def _req(text="hi"):
    return llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role="user", content=text)])


def test_429_with_small_retry_after_waits_and_retries_same_model():
    p = _FakeProvider(complete_script=[
        ProviderHTTPError(429, "rate", retry_after=0.01),
        ("好", "m1", "stop", (1, 1)),
    ])
    s = _servicer(p, ["m1", "m2"])
    resp = asyncio.run(s.Complete(_req("q1"), _Ctx()))
    assert resp.content == "好"
    assert p.calls == ["m1", "m1"]        # 等待后重试同模型，而不是降到 m2


def test_429_without_retry_after_skips_fast_tier_maps_resource_exhausted():
    p = _FakeProvider(complete_script=[ProviderHTTPError(429, "rate")])
    s = _servicer(p, ["m1", "m2"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_req("q2"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert p.calls == ["m1"]              # 限流按厂商级处理：fast 档不再白打


def test_429_long_retry_after_fails_fast_without_wait():
    p = _FakeProvider(complete_script=[ProviderHTTPError(429, "rate", retry_after=30.0)])
    s = _servicer(p, ["m1", "m2"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_req("q2b"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert p.calls == ["m1"]              # 30s 等不起（车内对话），直接诚实失败


def test_non_429_still_falls_through_tiers():
    p = _FakeProvider(complete_script=[RuntimeError("boom"), ("好", "m2", "stop", (1, 1))])
    s = _servicer(p, ["m1", "m2"])
    resp = asyncio.run(s.Complete(_req("q3"), _Ctx()))
    assert resp.model_used == "m2"
    assert p.calls == ["m1", "m2"]


def test_request_4xx_maps_invalid_argument():
    p = _FakeProvider(complete_script=[
        ProviderHTTPError(422, "bad"), ProviderHTTPError(422, "bad")])
    s = _servicer(p, ["m1", "m2"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_req("q4"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.INVALID_ARGUMENT


async def _collect_stream(s, req, ctx):
    out = []
    async for chunk in s.CompleteStream(req, ctx):
        out.append((chunk.delta, chunk.done))
    return out


def test_stream_falls_back_before_first_token():
    p = _FakeProvider(stream_script=[
        [ProviderHTTPError(500, "err")],       # m1 首 token 前失败
        ["你", "好"],                           # m2 正常出流
    ])
    s = _servicer(p, ["m1", "m2"])
    out = asyncio.run(_collect_stream(s, _req("s1"), _Ctx()))
    assert [d for d, done in out if not done] == ["你", "好"]
    assert out[-1][1] is True
    assert p.stream_calls == ["m1", "m2"]      # 兑现 R3.5 记录的缺口


def test_stream_mid_way_failure_does_not_switch_model():
    p = _FakeProvider(stream_script=[["你", RuntimeError("cut")], ["不该被用到"]])
    s = _servicer(p, ["m1", "m2"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(_collect_stream(s, _req("s2"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.UNAVAILABLE
    assert p.stream_calls == ["m1"]            # 半段话不可拼接，不切 m2


def test_stream_all_tiers_429_maps_resource_exhausted():
    p = _FakeProvider(stream_script=[
        [ProviderHTTPError(429, "rate")], [ProviderHTTPError(429, "rate")]])
    s = _servicer(p, ["m1", "m2"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(_collect_stream(s, _req("s3"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.RESOURCE_EXHAUSTED


# ── 请求级 pin（运行时硬化 D2）──

def _pin_req(text, provider):
    req = _req(text)
    req.meta["llm_provider"] = provider
    return req


def test_pin_routes_to_pinned_provider_not_active():
    active = _FakeProvider(complete_script=[("active 不该被用到", "a", "stop", (1, 1))])
    pinned = _FakeProvider(complete_script=[("好", "p1", "stop", (1, 1))])
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(active, ["p1"], entries={"other": pinned})
    resp = asyncio.run(s.Complete(_pin_req("pin1", "other"), _Ctx()))
    assert resp.content == "好"
    assert pinned.calls == ["p1"] and active.calls == []


def test_pin_unknown_provider_fails_closed_invalid_argument():
    active = _FakeProvider(complete_script=[("不该执行", "a", "stop", (1, 1))])
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(active, ["m1"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_pin_req("pin2", "nope"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.INVALID_ARGUMENT
    assert active.calls == []            # fail-closed：不许静默漂移到 active


def test_pin_stream_routes_to_pinned_provider():
    active = _FakeProvider(stream_script=[["不该被用到"]])
    pinned = _FakeProvider(stream_script=[["你", "好"]])
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(active, ["p1"], entries={"other": pinned})
    out = asyncio.run(_collect_stream(s, _pin_req("pin3", "other"), _Ctx()))
    assert [d for d, done in out if not done] == ["你", "好"]
    assert pinned.stream_calls == ["p1"] and active.stream_calls == []


# ── 跨厂商备份档（LLM_BACKUP，2026-08-15）──


def _rt_with_backup(active, backup, models=("m1",)):
    rt = _RT(active, list(models), entries={"bk": backup})
    return rt


def test_backup_provider_catches_active_chain_failure(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    active = _FakeProvider(complete_script=[RuntimeError("boom")])
    backup = _FakeProvider(complete_script=[("接住", "b1", "stop", (1, 1))])
    s = srv.LLMGatewayServicer()
    s.runtime = _rt_with_backup(active, backup)
    resp = asyncio.run(s.Complete(_req("bk1"), _Ctx()))
    assert resp.content == "接住"
    assert active.calls == ["m1"] and backup.calls == ["m1"]  # _RT 固定模型表


def test_backup_tried_even_after_rate_limit(monkeypatch):
    """限流是厂商级：同厂剩余档跳过，但**备份厂商照试**——换厂商正是对症。"""
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    active = _FakeProvider(complete_script=[ProviderHTTPError(429, "rate")])
    backup = _FakeProvider(complete_script=[("接住", "b1", "stop", (1, 1))])
    s = srv.LLMGatewayServicer()
    s.runtime = _rt_with_backup(active, backup, models=("m1", "m2"))
    resp = asyncio.run(s.Complete(_req("bk2"), _Ctx()))
    assert resp.content == "接住"
    assert active.calls == ["m1"]          # m2 同厂被跳过
    assert backup.calls == ["m1"]


def test_backup_failure_maps_by_last_error(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    active = _FakeProvider(complete_script=[ProviderHTTPError(429, "rate")])
    backup = _FakeProvider(complete_script=[RuntimeError("also down")])
    s = srv.LLMGatewayServicer()
    s.runtime = _rt_with_backup(active, backup)
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_req("bk3"), _Ctx()))
    # 终态按最后一次失败（备份的 RuntimeError）定性，而不是沿用主档的 429
    assert ei.value.code == grpc.StatusCode.UNAVAILABLE


def test_pinned_request_never_uses_backup(monkeypatch):
    """pin 的意义就是不许静默漂移（D2）：pinned 请求整链失败也不跨备份厂商。"""
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    pinned_p = _FakeProvider(complete_script=[RuntimeError("down")])
    backup = _FakeProvider(complete_script=[("不该被用到", "b1", "stop", (1, 1))])
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(_FakeProvider(), ["m1"],
                    entries={"other": pinned_p, "bk": backup})
    with pytest.raises(_Abort):
        asyncio.run(s.Complete(_pin_req("bk4", "other"), _Ctx()))
    assert backup.calls == []


def test_backup_unset_keeps_behavior(monkeypatch):
    monkeypatch.delenv("LLM_BACKUP", raising=False)
    active = _FakeProvider(complete_script=[RuntimeError("down")])
    s = _servicer(active, ["m1"])
    with pytest.raises(_Abort) as ei:
        asyncio.run(s.Complete(_req("bk5"), _Ctx()))
    assert ei.value.code == grpc.StatusCode.UNAVAILABLE
    assert active.calls == ["m1"]


def test_backup_same_as_active_is_skipped(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP", "fake:m1")   # _RT.active_id == "fake"
    active = _FakeProvider(complete_script=[RuntimeError("down")])
    s = srv.LLMGatewayServicer()
    s.runtime = _RT(active, ["m1"], entries={"fake": active})
    with pytest.raises(_Abort):
        asyncio.run(s.Complete(_req("bk6"), _Ctx()))
    assert active.calls == ["m1"]          # 没有对同一上游的第二次白打


def test_stream_backup_before_first_token(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    active = _FakeProvider(stream_script=[[ProviderHTTPError(500, "err")]])
    backup = _FakeProvider(stream_script=[["接", "住"]])
    s = srv.LLMGatewayServicer()
    s.runtime = _rt_with_backup(active, backup)
    out = asyncio.run(_collect_stream(s, _req("bk7"), _Ctx()))
    assert [d for d, done in out if not done] == ["接", "住"]
    assert backup.stream_calls == ["m1"]


def test_stream_first_token_out_never_switches_to_backup(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP", "bk:b1")
    active = _FakeProvider(stream_script=[["半", RuntimeError("cut")]])
    backup = _FakeProvider(stream_script=[["不该被用到"]])
    s = srv.LLMGatewayServicer()
    s.runtime = _rt_with_backup(active, backup)
    with pytest.raises(_Abort):
        asyncio.run(_collect_stream(s, _req("bk8"), _Ctx()))
    assert backup.stream_calls == []       # 半段话不可拼接，备份也不例外

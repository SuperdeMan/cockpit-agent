"""多 LLM 源运行时单测：per-provider body 构造 + 注册表 + 档位解析 + 全局切换。"""
import os
import sys

import pytest

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)
from providers import OpenAICompatibleProvider  # noqa: E402
import llm_runtime  # noqa: E402
from llm_runtime import LLMRuntime  # noqa: E402

_MSG = [{"role": "user", "content": "hi"}]


# ── per-provider body 构造（token 参数名 + 思考风格 + 鉴权）──

def test_build_body_mimo_style():
    p = OpenAICompatibleProvider("k", token_param="max_completion_tokens", thinking_style="mimo")
    body = p._build_body(_MSG, "m", 0.7, 100, thinking=None, stream=False)   # 默认关思考
    assert body["max_completion_tokens"] == 100
    assert body["thinking"] == {"type": "disabled"} and "enable_thinking" not in body
    on = p._build_body(_MSG, "m", 0.7, 100, thinking=True, stream=True)      # 开思考不发键、抬 token
    assert "thinking" not in on and on["max_completion_tokens"] == 2048 and on["stream"] is True


def test_build_body_none_thinking_style():
    # thinking_style="none"：不发任何思考键（用服务商默认）。注：DeepSeek 真栈探测发现其推理模型
    # 认 thinking:{type:disabled}，故 deepseek 实际走 mimo 风格（见 llm_runtime._PROVIDER_SPECS）。
    p = OpenAICompatibleProvider("k", token_param="max_tokens", thinking_style="none")
    body = p._build_body(_MSG, "m", 0.7, 100, thinking=None, stream=False)
    assert body["max_tokens"] == 100
    assert "thinking" not in body and "enable_thinking" not in body


def test_build_body_qwen_style():
    p = OpenAICompatibleProvider("k", token_param="max_tokens", thinking_style="qwen")
    assert p._build_body(_MSG, "m", 0.7, 100, thinking=None, stream=False)["enable_thinking"] is False
    assert p._build_body(_MSG, "m", 0.7, 100, thinking=True, stream=False)["enable_thinking"] is True


def test_auth_headers():
    assert OpenAICompatibleProvider("k", auth_style="bearer")._headers()["Authorization"] == "Bearer k"
    assert OpenAICompatibleProvider("k", auth_style="api-key")._headers()["api-key"] == "k"


# ── 注册表 / 档位解析 / 切换 ──

_ENV_KEYS = ("LLM_PROVIDER", "LLM_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY",
             "DASHSCOPE_LLM_KEY", "DASHSCOPE_ASR_KEY", "LLM_EMBED_API_KEY",
             "LLM_MODEL_PRIMARY", "LLM_MODEL_FAST", "MINIMAX_LLM_MODEL",
             "DEEPSEEK_MODEL_PRIMARY", "DEEPSEEK_MODEL_FAST", "QWEN_MODEL_PRIMARY",
             "QWEN_MODEL_FAST", "REDIS_URL")


@pytest.fixture(autouse=True)
def _clean_env():
    old = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _runtime(env: dict) -> LLMRuntime:
    os.environ.update(env)
    return LLMRuntime()


def test_registry_lists_all_greys_unconfigured():
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk", "DEEPSEEK_API_KEY": "dk"})
    st = rt.status()
    by_id = {p["id"]: p for p in st["providers"]}
    assert by_id["mimo"]["available"] and by_id["deepseek"]["available"]
    assert by_id["minimax"]["available"] is False and by_id["qwen"]["available"] is False  # 未配 key 置灰
    assert st["active"]["provider"] == "mimo"                # 默认 xiaomimimo→mimo
    assert rt.resolve_models("") == ["mimo-v2.5-pro", "mimo-v2.5"]
    assert rt.resolve_models("@fast")[0] == "mimo-v2.5"


def test_switch_provider_and_unknown_model_falls_back():
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk", "DEEPSEEK_API_KEY": "dk"})
    rt.set_active("deepseek")
    assert rt.active_id == "deepseek"
    assert rt.resolve_models("")[0] == "deepseek-v4-pro"
    assert rt.resolve_models("@fast")[0] == "deepseek-v4-flash"
    # active=deepseek 时收到 chitchat 发来的 mimo 模型名（不认识）→ 回落 deepseek primary
    assert rt.resolve_models("mimo-v2.5")[0] == "deepseek-v4-pro"
    with pytest.raises(ValueError):        # 切到未配 key 的厂商 → 拒绝
        rt.set_active("qwen")


def test_set_active_specific_model():
    rt = _runtime({"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "dk"})
    rt.set_active("deepseek", "deepseek-v4-flash")
    assert rt.resolve_models("")[0] == "deepseek-v4-flash"   # 具体模型覆盖 primary
    assert rt.status()["active"]["model"] == "deepseek-v4-flash"


def test_qwen_reuses_dashscope_key():
    rt = _runtime({"LLM_PROVIDER": "qwen", "DASHSCOPE_ASR_KEY": "bk"})
    assert {p["id"] for p in rt.status()["providers"] if p["available"]} >= {"qwen"}
    assert rt.active_id == "qwen"
    assert rt.resolve_models("")[0] == "qwen3.7-max"


def test_no_keys_falls_back_to_mock():
    rt = _runtime({})
    assert rt.active_id == "mock"
    assert rt.resolve_models("") == ["mock"]


# ── active 持久化（运行时硬化 D1：重启/重建不回落 env 默认）──

class _FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v


class _DeadRedis:
    def get(self, k):
        raise ConnectionError("redis down")

    def set(self, k, v):
        raise ConnectionError("redis down")


def test_active_persists_across_rebuild(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(llm_runtime, "_redis_client", lambda: fake)
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk", "DEEPSEEK_API_KEY": "dk"})
    rt.set_active("deepseek", "deepseek-v4-flash")
    rt2 = LLMRuntime()   # 重建（模拟网关重启/重建镜像）
    assert rt2.active_id == "deepseek"
    assert rt2.status()["active"]["model"] == "deepseek-v4-flash"


def test_persisted_unknown_provider_falls_back_env_default(monkeypatch):
    import json as _json
    fake = _FakeRedis()
    fake.set("llm:active", _json.dumps({"provider": "qwen", "model": ""}))  # 未配 key 的厂商
    monkeypatch.setattr(llm_runtime, "_redis_client", lambda: fake)
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk"})
    assert rt.active_id == "mimo"   # 持久化值不可用 → 保持 env 默认


def test_redis_down_degrades_to_memory_state(monkeypatch):
    monkeypatch.setattr(llm_runtime, "_redis_client", lambda: _DeadRedis())
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk", "DEEPSEEK_API_KEY": "dk"})
    rt.set_active("deepseek")           # 写失败仅告警，不炸
    assert rt.active_id == "deepseek"   # 内存态不受影响


def test_no_redis_url_keeps_legacy_behavior():
    rt = _runtime({"LLM_PROVIDER": "xiaomimimo", "LLM_API_KEY": "mk"})
    assert rt._redis is None            # 未配 REDIS_URL → 持久化整体旁路
    rt.set_active("mimo")               # 不炸


# ── 按需探针 + 被动健康（运行时硬化 D5）──

def test_probe_default_active_records_health():
    import asyncio
    rt = _runtime({})                   # 无 key → mock provider
    res = asyncio.run(rt.probe(""))
    assert res["ok"] is True and res["provider"] == "mock"
    from health import health_tracker
    snap = health_tracker.snapshot()
    assert snap["mock"]["ok"] >= 1 and snap["mock"]["ewma_latency_ms"] >= 0
    assert "health" in rt.status()      # /api/llm/providers 附带健康块


def test_probe_unknown_provider_reports_not_configured():
    import asyncio
    rt = _runtime({})
    res = asyncio.run(rt.probe("nope"))
    assert res["ok"] is False and "未配置" in res["error"]

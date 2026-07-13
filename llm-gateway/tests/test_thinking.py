"""动态思考开关：MiMo provider 按 thinking 形参决定是否发 disabled 键 + token 抬升。"""
from __future__ import annotations
import asyncio
import os
import sys

# 让 llm-gateway 包可被仓库根导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import OpenAICompatibleProvider


def _run(coro):
    return asyncio.run(coro)


def test_resolve_thinking_default_and_override():
    p = OpenAICompatibleProvider("k", disable_thinking=True)
    assert p._resolve_thinking(None) is True     # 默认关思考
    assert p._resolve_thinking(True) is False     # 本次开思考 → 不关
    assert p._resolve_thinking(False) is True      # 本次显式关
    p2 = OpenAICompatibleProvider("k", disable_thinking=False)
    assert p2._resolve_thinking(None) is False    # provider 默认开思考


class _Resp:
    status_code = 200   # provider 现按 status_code 判 4xx（响应体带进异常，见 complete()）

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}


def _patch_httpx(monkeypatch, captured):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            captured.update(json or {})
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_thinking_on_omits_disabled_and_bumps_tokens(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    p = OpenAICompatibleProvider("k", disable_thinking=True)
    _run(p.complete([{"role": "user", "content": "hi"}], "m", 0.3, 400, thinking=True))
    assert "thinking" not in captured                 # 开思考 → 不发 disabled 键
    assert captured["max_completion_tokens"] >= 2048   # token 抬升给 reasoning 留头


def test_thinking_off_sends_disabled_keeps_tokens(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    p = OpenAICompatibleProvider("k", disable_thinking=True)
    _run(p.complete([{"role": "user", "content": "hi"}], "m", 0.3, 400, thinking=None))
    assert captured.get("thinking") == {"type": "disabled"}   # 默认关思考
    assert captured["max_completion_tokens"] == 400            # 不抬 token

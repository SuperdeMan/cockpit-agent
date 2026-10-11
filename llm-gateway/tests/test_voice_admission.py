"""语义打断的判定接口 `/api/voice/admission`（设计 docs/design/2026-10-11-semantic-barge-in.md §4.1）。

判据与云端续问窗判定同一份（`runtime/continuation_admission.py`）；这里钉的是接口这一层：只认 Bearer 主体、
空句不问模型、判定结论与每次读数原样回给客户端、每一问有上限、请求形状与云端那一路一致（快档 / 温度 0 / 32 token）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_http_server_voice_admission", os.path.join(_DIR, "http_server.py"))
HS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HS)

import voice_admission  # noqa: E402
from runtime import continuation_admission as shared  # noqa: E402

TOKENS = "tok-owner:owner-1:v1:memory.delete"


def _post(monkeypatch, body, *, token="tok-owner", answers=None, delay=0.0):
    """answers：按顺序给每一问的回答（布尔 / 原始文本 / 异常）。返回 (status, payload, 每一问收到的消息)。"""
    monkeypatch.setenv("AUTH_TOKENS", TOKENS)
    queue = list(answers or [])
    seen = []

    async def fake_complete(messages, meta):
        seen.append({"messages": messages, "meta": dict(meta)})
        if delay:
            await asyncio.sleep(delay)
        answer = queue.pop(0) if queue else True
        if isinstance(answer, Exception):
            raise answer
        return json.dumps({"addressed": answer}) if isinstance(answer, bool) else str(answer)

    monkeypatch.setattr(voice_admission, "_loopback_complete", fake_complete)

    async def go():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            data = body if isinstance(body, (bytes, str)) else json.dumps(body)
            response = await client.post("/api/voice/admission", data=data, headers=headers)
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    return status, payload, seen


_BODY = {"utterance": "妈，你到哪了", "previous": "深圳明天多云，最高气温 27 度。", "assistant_name": "小航"}


def test_without_a_token_the_model_is_not_asked(monkeypatch):
    status, payload, seen = _post(monkeypatch, _BODY, token="")
    assert status == 401 and payload["error"] == "unauthorized"
    assert seen == []
    status, payload, seen = _post(monkeypatch, _BODY, token="tok-unknown")
    assert status == 401 and seen == []


@pytest.mark.parametrize("body,error", [
    (b"not json", "bad_request"),
    (["a list"], "bad_request"),
    ({"utterance": "   ", "previous": "好的"}, "empty_utterance"),
    ({"previous": "好的"}, "empty_utterance"),
])
def test_a_bad_or_empty_request_is_refused_before_the_model(monkeypatch, body, error):
    status, payload, seen = _post(monkeypatch, body)
    assert status == 400 and payload["error"] == error
    assert seen == []


@pytest.mark.parametrize("answers,addressed,votes", [
    ([True], True, "1"),
    ([False, False], False, "0,0"),
    ([False, True], True, "0,1"),
    (["no json at all"], None, "-"),
    ([RuntimeError("gateway down")], None, "-"),
    ([False, RuntimeError("gateway down")], None, "0,-"),
])
def test_the_verdict_and_every_vote_come_back_as_judged(monkeypatch, answers, addressed, votes):
    status, payload, seen = _post(monkeypatch, _BODY, answers=answers)
    assert status == 200 and payload["ok"] is True
    assert payload["addressed"] is addressed and payload["votes"] == votes
    assert isinstance(payload["ms"], int)
    assert len(seen) == len(answers)


def test_the_question_is_the_shared_continuation_judgment_with_the_spoken_answer_as_previous(monkeypatch):
    _status, _payload, seen = _post(monkeypatch, _BODY, answers=[False, False])
    first, second = (s["messages"] for s in seen)
    assert first == shared.continuation_messages(_BODY["utterance"], _BODY["previous"], "小航")
    assert second == shared.continuation_messages(_BODY["utterance"], _BODY["previous"], "小航", recheck=True)
    assert seen[0]["meta"]["caller_service"] == "voice-admission"


def test_a_memory_directive_is_addressed_without_asking(monkeypatch):
    status, payload, seen = _post(monkeypatch, {"utterance": "记住我喜欢靠窗的座位", "previous": "好的"})
    assert status == 200 and payload["addressed"] is True and payload["votes"] == ""
    assert seen == []


def test_each_ask_is_bounded_and_a_slow_model_means_unjudged(monkeypatch):
    monkeypatch.setenv("CONTINUATION_ADMISSION_TIMEOUT_S", "0.05")
    status, payload, seen = _post(monkeypatch, _BODY, answers=[False], delay=1.0)
    assert status == 200 and payload["addressed"] is None and payload["votes"] == "-"
    assert len(seen) == 1


def test_the_loopback_request_has_the_cloud_judgment_shape(monkeypatch):
    """与云端判定同形：快档、温度 0、32 token、meta 只带非空值——缓存键与模型档位两边一致。"""
    captured = []

    class _Stub:
        async def Complete(self, req, timeout):
            captured.append((req, timeout))
            return SimpleNamespace(content=json.dumps({"addressed": True}))

    monkeypatch.setattr(voice_admission, "_stub", lambda: _Stub())
    out = asyncio.run(voice_admission._loopback_complete(
        shared.continuation_messages("那后天呢", "明天多云。"), {"caller_service": "voice-admission", "session_id": ""}))
    assert json.loads(out) == {"addressed": True}
    req, timeout = captured[0]
    assert (req.model, req.temperature, req.max_tokens) == ("@fast", 0.0, 32)
    assert dict(req.meta) == {"caller_service": "voice-admission"}
    assert [m.role for m in req.messages] == ["system", "user"]
    assert timeout > shared.continuation_timeout_s()

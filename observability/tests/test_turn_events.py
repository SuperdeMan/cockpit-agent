"""obs.turn / obs.llm 事件与 session_id 自动注入。"""
import asyncio
import json

import pytest

from observability.events import EventEmitter
from observability.tracing import set_session_id


@pytest.fixture(autouse=True)
def _clear_session():
    set_session_id("")
    yield
    set_session_id("")


def _connected_emitter(monkeypatch):
    emitter = EventEmitter("edge", nats_url="nats://example")
    sent = []

    class FakeNats:
        async def publish(self, subject, data):
            sent.append((subject, json.loads(data)))

    async def fake_conn():
        return FakeNats()

    monkeypatch.setattr(emitter, "_conn", fake_conn)
    return emitter, sent


def test_emit_turn_payload_shape(monkeypatch):
    emitter, sent = _connected_emitter(monkeypatch)

    async def run():
        await emitter.emit_turn(
            "trace-1", "sess-1",
            user_text="打开空调", speech="好的，空调已打开",
            status="ok", path="local", input_source="voice_wake",
            is_confirmation=False, ui_card_type="", actions=1,
            duration_ms=12.34, ts=1720000000000)
        await emitter.flush()

    asyncio.run(run())
    assert sent and sent[0][0] == "obs.turn"
    body = sent[0][1]
    assert body["trace_id"] == "trace-1"
    assert body["session_id"] == "sess-1"
    assert body["user_text"] == "打开空调"
    assert body["speech"] == "好的，空调已打开"
    assert body["path"] == "local"
    assert body["status"] == "ok"
    assert body["actions"] == 1
    assert body["ts"] == 1720000000000
    assert body["duration_ms"] == 12.3


def test_emit_turn_respects_content_gate(monkeypatch):
    monkeypatch.setenv("OBS_CONTENT_CAPTURE", "off")
    emitter, sent = _connected_emitter(monkeypatch)

    async def run():
        await emitter.emit_turn("t", "s", user_text="导航去机场", speech="好的")
        await emitter.flush()

    asyncio.run(run())
    body = sent[0][1]
    assert "机场" not in body["user_text"]
    assert body["user_text"].startswith("<len=")


def test_span_auto_carries_session_from_contextvar(monkeypatch):
    emitter, sent = _connected_emitter(monkeypatch)

    async def run():
        set_session_id("sess-ctx")
        await emitter.emit_span("trace-2", "route.local")
        await emitter.flush()

    asyncio.run(run())
    assert sent[0][1]["session_id"] == "sess-ctx"


def test_span_without_session_context_stays_clean(monkeypatch):
    emitter, sent = _connected_emitter(monkeypatch)

    async def run():
        await emitter.emit_span("trace-3", "route.local")
        await emitter.flush()

    asyncio.run(run())
    assert "session_id" not in sent[0][1]


def test_emit_llm_payload(monkeypatch):
    emitter, sent = _connected_emitter(monkeypatch)

    async def run():
        await emitter.emit_llm(
            trace_id="t-llm", session_id="s-llm", caller="cloud-planner",
            model="mimo-v2.5", prompt_tokens=100, completion_tokens=50,
            latency_ms=321.7, cache_hit=False, thinking=True,
            prompt_tail="用户说: 你好", content_head='{"steps":[]}')
        await emitter.flush()

    asyncio.run(run())
    assert sent[0][0] == "obs.llm"
    body = sent[0][1]
    assert body["caller"] == "cloud-planner"
    assert body["model"] == "mimo-v2.5"
    assert body["thinking"] is True
    assert body["prompt_tail"] == "用户说: 你好"

import asyncio
import json

from observability.events import EventEmitter


def test_emit_is_noop_without_nats_url():
    """Missing NATS config disables emission without raising."""
    emitter = EventEmitter("edge", nats_url="")

    asyncio.run(emitter.emit_span("t1", "fast_intent"))

    assert emitter._disabled is True


def test_emit_does_not_raise_when_unreachable():
    """An unreachable NATS server must not affect the primary request path."""
    emitter = EventEmitter("edge", nats_url="nats://127.0.0.1:1")

    asyncio.run(
        emitter.emit_state(
            [{"key": "hvac_temp", "old": 24, "new": 26}],
            "T0",
        )
    )

    assert emitter._disabled is True


def test_emit_publishes_payload_when_connected(monkeypatch):
    """Connected emitters publish a structured event to the expected subject."""
    emitter = EventEmitter("cloud", nats_url="nats://example")
    sent = []

    class FakeNats:
        async def publish(self, subject, data):
            sent.append((subject, data))

    async def fake_conn():
        return FakeNats()

    monkeypatch.setattr(emitter, "_conn", fake_conn)

    asyncio.run(
        emitter.emit_span(
            "trace-9",
            "step.agent:navigation",
            status="ok",
            duration_ms=340,
            attrs={"intent": "navigation.search_poi"},
        )
    )

    assert sent and sent[0][0] == "obs.span"
    body = json.loads(sent[0][1])
    assert body["trace_id"] == "trace-9"
    assert body["node"] == "step.agent:navigation"
    assert body["service"] == "cloud"
    assert body["attrs"]["intent"] == "navigation.search_poi"
    assert "ts" in body and "span_id" in body

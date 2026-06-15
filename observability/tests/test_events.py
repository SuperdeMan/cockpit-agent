import asyncio
import json
import sys
import time
from types import SimpleNamespace

from observability.events import EventEmitter


def test_emit_is_noop_without_nats_url():
    """Missing NATS config disables emission without raising."""
    emitter = EventEmitter("edge", nats_url="")

    asyncio.run(emitter.emit_span("t1", "fast_intent"))

    assert emitter._disabled is True


def test_emit_does_not_raise_when_unreachable():
    """An unreachable NATS server must not affect the primary request path."""
    emitter = EventEmitter("edge", nats_url="nats://127.0.0.1:1")

    async def run():
        await emitter.emit_state(
            [{"key": "hvac_temp", "old": 24, "new": 26}],
            "T0",
        )
        await emitter.flush()

    asyncio.run(run())

    assert emitter._disabled is False
    assert emitter._nc is None


def test_emit_abandons_slow_initial_connection(monkeypatch):
    """Observability setup must not stall the primary request path."""

    async def slow_connect(*args, **kwargs):
        await asyncio.sleep(1)
        raise ConnectionError("unreachable")

    monkeypatch.setitem(sys.modules, "nats", SimpleNamespace(connect=slow_connect))
    emitter = EventEmitter("edge", nats_url="nats://example")

    async def run():
        started = time.perf_counter()
        await emitter.emit_span("t1", "fast_intent")
        elapsed = time.perf_counter() - started
        await emitter.flush()
        return elapsed

    assert asyncio.run(run()) < 0.05
    assert emitter._disabled is False
    assert emitter._nc is None


def test_emit_retries_after_initial_connection_failure(monkeypatch):
    attempts = 0
    sent = []

    class FakeNats:
        async def publish(self, subject, data):
            sent.append((subject, data))

    async def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("starting")
        return FakeNats()

    monkeypatch.setitem(sys.modules, "nats", SimpleNamespace(connect=connect))
    emitter = EventEmitter("edge", nats_url="nats://example")

    async def run():
        await emitter.emit_span("first", "route.local")
        await emitter.flush()
        emitter._next_connect_at = 0
        await emitter.emit_span("second", "route.local")
        await emitter.flush()

    asyncio.run(run())

    assert attempts == 2
    assert len(sent) == 1
    assert json.loads(sent[0][1])["trace_id"] == "second"


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

    async def run():
        await emitter.emit_span(
            "trace-9",
            "step.agent:navigation",
            status="ok",
            duration_ms=340,
            attrs={"intent": "navigation.search_poi"},
        )
        await emitter.flush()

    asyncio.run(run())

    assert sent and sent[0][0] == "obs.span"
    body = json.loads(sent[0][1])
    assert body["trace_id"] == "trace-9"
    assert body["node"] == "step.agent:navigation"
    assert body["service"] == "cloud"
    assert body["attrs"]["intent"] == "navigation.search_poi"
    assert "ts" in body and "span_id" in body

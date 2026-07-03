import pytest

from observability.collector import otel_bridge


@pytest.fixture(autouse=True)
def _reset_bridge(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    otel_bridge._tracer = None
    yield
    otel_bridge._tracer = None


def test_init_inactive_without_endpoint():
    assert otel_bridge.init_otel_bridge() is False
    assert otel_bridge._tracer is None


def test_export_span_is_noop_without_active_bridge():
    # Must not raise even with a fully-shaped real span event.
    otel_bridge.export_span(
        {
            "trace_id": "abc123",
            "span_id": "s1",
            "parent_id": "",
            "node": "cloud.planning",
            "status": "ok",
            "duration_ms": 12.3,
            "attrs": {"steps": 2},
            "ts": 1751520000000,
        }
    )


def test_hash_id_deterministic_and_low_collision():
    ids = [otel_bridge._hash_id(f"trace-{i}", nbytes=16) for i in range(200)]
    assert len(set(ids)) == len(ids)
    assert otel_bridge._hash_id("same", nbytes=16) == otel_bridge._hash_id("same", nbytes=16)
    assert all(i != 0 for i in ids)


def test_init_active_with_unreachable_endpoint(monkeypatch):
    pytest.importorskip("opentelemetry", reason="opentelemetry not installed in this env")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")

    assert otel_bridge.init_otel_bridge() is True
    assert otel_bridge._tracer is not None

    # Real shaped event, including the "almost never populated" parent_id path.
    otel_bridge.export_span(
        {
            "trace_id": "abc123",
            "span_id": "s1",
            "parent_id": "s0",
            "node": "cloud.planning",
            "status": "err",
            "duration_ms": 250.0,
            "attrs": {"steps": 3, "ok": True, "tags": ["a", "b"], "nested": {"x": 1}},
            "ts": 1751520000000,
        }
    )

from observability.metrics import MetricsCollector


def test_agent_snapshot_aggregates():
    metrics = MetricsCollector()
    metrics.record_agent_call("navigation", 100, True)
    metrics.record_agent_call("navigation", 200, True)
    metrics.record_agent_call("navigation", 300, False)

    snapshot = metrics.agent_snapshot("navigation")

    assert snapshot["count"] == 3
    assert snapshot["avg_ms"] == 200.0
    assert snapshot["error_rate"] == round(1 / 3, 3)


def test_agent_snapshot_missing_returns_none():
    assert MetricsCollector().agent_snapshot("missing") is None

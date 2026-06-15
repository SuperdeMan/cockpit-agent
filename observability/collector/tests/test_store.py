from observability.collector.store import CollectorStore


def test_apply_state_builds_mirror():
    store = CollectorStore()

    store.apply_state(
        {
            "source": "T0",
            "changes": [
                {"key": "hvac_temp", "old": 24, "new": 26},
                {"key": "hvac_on", "old": False, "new": True},
            ],
        }
    )

    assert store.vehicle_state["hvac_temp"] == 26
    assert store.vehicle_state["hvac_on"] is True


def test_apply_span_groups_by_trace():
    store = CollectorStore()

    store.apply_span({"trace_id": "t1", "node": "fast_intent", "ts": 1})
    store.apply_span({"trace_id": "t1", "node": "val.execute", "ts": 2})

    assert len(store.traces["t1"]["spans"]) == 2
    assert store.traces["t1"]["spans"][1]["node"] == "val.execute"


def test_traces_ring_buffer_evicts_oldest():
    store = CollectorStore(max_traces=2)

    for index in range(3):
        store.apply_span(
            {"trace_id": f"t{index}", "node": "route.local", "ts": index}
        )

    assert "t0" not in store.traces
    assert set(store.traces) == {"t1", "t2"}


def test_apply_health_and_metric_merge():
    store = CollectorStore()

    store.apply_health(
        {
            "agent_id": "navigation",
            "healthy": True,
            "fail_count": 0,
            "last_seen": 1.0,
        }
    )
    store.apply_metric(
        {
            "agent_id": "navigation",
            "count": 12,
            "avg_ms": 230.0,
            "error_rate": 0.0,
        }
    )

    agent = store.agents["navigation"]
    assert agent["healthy"] is True
    assert agent["count"] == 12
    assert agent["avg_ms"] == 230.0

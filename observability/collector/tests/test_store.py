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


def test_empty_store_recovers_full_state_from_snapshot():
    # collector 重启后内存清空；edge 周期 snapshot（source="snapshot"、old=None 的全量
    # changes）必须把车辆镜像从空恢复为全量，且恢复后仍能继续叠加增量。这是 P1-1 自愈路径。
    store = CollectorStore()
    assert store.vehicle_state == {}

    snapshot = {
        "source": "snapshot",
        "changes": [
            {"key": "hvac_on", "old": None, "new": True},
            {"key": "hvac_temp", "old": None, "new": 26},
            {"key": "window", "old": None, "new": "closed"},
            {"key": "gear", "old": None, "new": "P"},
        ],
    }
    store.apply_state(snapshot)

    assert store.vehicle_state == {
        "hvac_on": True,
        "hvac_temp": 26,
        "window": "closed",
        "gear": "P",
    }

    # 恢复后增量仍正常叠加
    store.apply_state(
        {"source": "T0", "changes": [{"key": "hvac_temp", "old": 26, "new": 22}]}
    )
    assert store.vehicle_state["hvac_temp"] == 22

from observability.collector.metrics_export import render_prometheus_metrics
from observability.collector.store import CollectorStore


def _store_with(agents: dict) -> CollectorStore:
    store = CollectorStore()
    store.agents.update(agents)
    return store


def test_renders_help_and_type_lines():
    store = _store_with({"navigation": {"count": 3, "avg_ms": 100.0, "error_rate": 0.0}})
    text = render_prometheus_metrics(store)
    assert "# HELP cockpit_agent_calls_total" in text
    assert "# TYPE cockpit_agent_calls_total counter" in text
    assert "# TYPE cockpit_agent_latency_seconds_avg gauge" in text


def test_agent_with_no_count_is_absent_from_calls_total():
    store = _store_with({"trip-planner": {"healthy": True}})
    text = render_prometheus_metrics(store)
    calls_lines = [
        line for line in text.splitlines()
        if line.startswith("cockpit_agent_calls_total{")
    ]
    assert calls_lines == []


def test_latency_converted_to_seconds():
    store = _store_with({"navigation": {"avg_ms": 230.0}})
    text = render_prometheus_metrics(store)
    line = next(
        l for l in text.splitlines()
        if l.startswith("cockpit_agent_latency_seconds_avg{")
    )
    assert line.endswith(" 0.23")


def test_circuit_state_encoded_as_ordered_gauge():
    store = _store_with({
        "a": {"circuit": "closed"},
        "b": {"circuit": "half_open"},
        "c": {"circuit": "open"},
    })
    text = render_prometheus_metrics(store)
    values = {}
    for line in text.splitlines():
        if line.startswith("cockpit_agent_circuit_state{"):
            agent_id = line.split('agent_id="')[1].split('"')[0]
            values[agent_id] = line.rsplit(" ", 1)[1]
    assert values == {"a": "0", "b": "1", "c": "2"}


def test_labels_include_agent_id_deployment_kind():
    store = _store_with({"navigation": {"count": 1, "deployment": "cloud", "kind": "agent"}})
    text = render_prometheus_metrics(store)
    assert 'agent_id="navigation",deployment="cloud",kind="agent"' in text


def test_missing_deployment_kind_default_to_unknown():
    store = _store_with({"navigation": {"count": 1}})
    text = render_prometheus_metrics(store)
    assert 'deployment="unknown",kind="unknown"' in text


def test_empty_store_still_renders_help_type_headers():
    store = CollectorStore()
    text = render_prometheus_metrics(store)
    assert "# HELP cockpit_agent_calls_total" in text
    assert text.count("cockpit_agent_calls_total{") == 0

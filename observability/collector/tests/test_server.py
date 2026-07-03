from fastapi.testclient import TestClient

from observability.collector.server import create_app


def _client():
    return TestClient(create_app())


def test_vehicle_state_reflects_store():
    client = _client()
    client.app.state.store.apply_state(
        {
            "source": "T0",
            "changes": [{"key": "hvac_temp", "old": 24, "new": 26}],
        }
    )

    response = client.get("/api/vehicle/state")

    assert response.status_code == 200
    assert response.json()["hvac_temp"] == 26


def test_debug_rejects_non_whitelisted_key():
    client = _client()

    response = client.post(
        "/api/debug/vehicle",
        json={"key": "hvac_temp", "value": 30},
    )

    assert response.json()["ok"] is False


def test_debug_allows_environment_key():
    client = _client()

    response = client.post(
        "/api/debug/vehicle",
        json={"key": "speed_kmh", "value": 130},
    )

    assert response.json()["ok"] is True
    assert response.json()["value"] == 130


def test_agents_endpoint():
    client = _client()
    client.app.state.store.apply_health(
        {
            "agent_id": "navigation",
            "healthy": True,
            "fail_count": 0,
            "last_seen": 1.0,
        }
    )

    response = client.get("/api/agents")

    assert response.status_code == 200
    assert "navigation" in response.json()


def test_metrics_endpoint_returns_prometheus_text():
    client = _client()
    client.app.state.store.apply_metric(
        {"agent_id": "navigation", "count": 5, "avg_ms": 120.0, "error_rate": 0.0, "circuit": "closed"}
    )

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "cockpit_agent_calls_total" in response.text
    assert 'agent_id="navigation"' in response.text

from fastapi.testclient import TestClient

from observability.collector.server import create_app
from runtime import obs_access

# collector 的读写接口要运维凭据（runtime/obs_access.py）：测试客户端注入密钥并带上令牌。
_OPERATOR_KEY = obs_access.derive_key("collector-test-secret-" + "x" * 32)


def _operator_client() -> TestClient:
    client = TestClient(create_app(operator_key=_OPERATOR_KEY))
    client.headers.update(obs_access.headers(obs_access.issue(_OPERATOR_KEY)))
    return client


def _client():
    return _operator_client()


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


def test_ingest_loop_subscribes_flushes_requests_snapshot_before_marking_ready(monkeypatch):
    import asyncio
    import sys
    from types import ModuleType
    from observability.collector.server import SUBJECTS, ingest_loop
    from runtime.vehicle_state import SNAPSHOT_REQUEST_SUBJECT

    app = create_app(operator_key=_OPERATOR_KEY)
    events = []
    callbacks = {}
    reconnected_holder = {}

    class FakeConnection:
        async def subscribe(self, subject, cb=None):
            assert app.state.nc is None
            events.append(("subscribe", subject))
            callbacks[subject] = cb

        async def flush(self):
            assert app.state.nc is None
            events.append(("flush", None))

        async def publish(self, subject, data):
            events.append(("publish", subject, data, app.state.nc is not None))

    fake_nc = FakeConnection()
    fake_nats = ModuleType("nats")

    async def fake_connect(url, **kwargs):
        reconnected_holder["cb"] = kwargs.get("reconnected_cb")
        return fake_nc

    fake_nats.connect = fake_connect
    monkeypatch.setenv("NATS_URL", "nats://fake:4222")
    monkeypatch.setitem(sys.modules, "nats", fake_nats)

    asyncio.run(ingest_loop(app))

    assert app.state.nc is fake_nc
    assert [e[1] for e in events[: len(SUBJECTS)]] == list(SUBJECTS)
    assert events[len(SUBJECTS)] == ("flush", None)
    assert events[len(SUBJECTS) + 1] == ("publish", SNAPSHOT_REQUEST_SUBJECT, b"{}", False)
    assert callable(reconnected_holder["cb"])

    asyncio.run(reconnected_holder["cb"]())
    assert events[-1] == ("publish", SNAPSHOT_REQUEST_SUBJECT, b"{}", True)


def test_ingest_loop_self_heals_on_snapshot_required_delta(monkeypatch):
    import asyncio
    import json
    import sys
    from types import ModuleType, SimpleNamespace
    from observability.collector.server import ingest_loop
    from orchestrator.edge.vehicle_driver import SimulatedVehicleDriver
    from runtime.vehicle_state import SNAPSHOT_REQUEST_SUBJECT, STATE_SUBJECT

    app = create_app(operator_key=_OPERATOR_KEY)
    published = []
    callbacks = {}

    class FakeConnection:
        async def subscribe(self, subject, cb=None):
            callbacks[subject] = cb

        async def flush(self):
            pass

        async def publish(self, subject, data):
            published.append((subject, data))

    fake_nats = ModuleType("nats")

    async def fake_connect(url, **kwargs):
        return FakeConnection()

    fake_nats.connect = fake_connect
    monkeypatch.setenv("NATS_URL", "nats://fake:4222")
    monkeypatch.setitem(sys.modules, "nats", fake_nats)

    clock = [100.0]
    monkeypatch.setattr("observability.collector.server.time.monotonic", lambda: clock[0])

    async def run():
        await ingest_loop(app)
        assert published == [(SNAPSHOT_REQUEST_SUBJECT, b"{}")]

        # Edge already emitted its initial seq=1 snapshot before collector restarted.
        driver = SimulatedVehicleDriver("v1")
        _ = driver.observation(snapshot=True)
        driver.state["hvac_on"] = True
        driver.state["hvac_temp"] = 26
        delta = driver.observation(
            changes=[
                {"key": "hvac_on", "old": False, "new": True},
                {"key": "hvac_temp", "old": 24, "new": 26},
            ]
        )

        # Delta arrives after throttle window -> rejected with snapshot_required -> triggers resync.
        clock[0] += 2.0
        await callbacks[STATE_SUBJECT](
            SimpleNamespace(subject=STATE_SUBJECT, data=json.dumps(delta).encode())
        )
        assert app.state.store.vehicle_state == {}
        assert len(published) == 2
        assert published[-1] == (SNAPSHOT_REQUEST_SUBJECT, b"{}")

        # Second immediate delta inside 1s throttle does not spam another request.
        delta2 = driver.observation(changes=[{"key": "hvac_temp", "old": 26, "new": 25}])
        await callbacks[STATE_SUBJECT](
            SimpleNamespace(subject=STATE_SUBJECT, data=json.dumps(delta2).encode())
        )
        assert len(published) == 2

        # Snapshot reply hydrates collector; subsequent deltas are accepted normally.
        snap = driver.observation(snapshot=True)
        await callbacks[STATE_SUBJECT](
            SimpleNamespace(subject=STATE_SUBJECT, data=json.dumps(snap).encode())
        )
        assert app.state.store.vehicle_state["hvac_on"] is True
        assert app.state.store.vehicle_state["hvac_temp"] == 26

        driver.state["hvac_temp"] = 23
        delta3 = driver.observation(changes=[{"key": "hvac_temp", "old": 26, "new": 23}])
        await callbacks[STATE_SUBJECT](
            SimpleNamespace(subject=STATE_SUBJECT, data=json.dumps(delta3).encode())
        )
        assert app.state.store.vehicle_state["hvac_temp"] == 23

    asyncio.run(run())

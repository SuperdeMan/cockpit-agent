from server import EdgeOrchestratorServicer


def test_apply_debug_allows_environment_key(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()

    assert service.apply_debug("speed_kmh", 130) is True
    assert service.val.state["speed_kmh"] == 130


def test_apply_debug_rejects_vehicle_control_key(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()

    assert service.apply_debug("hvac_on", True) is False
    assert service.val.state["hvac_on"] is False


def test_apply_debug_rejects_invalid_environment_value(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()

    assert service.apply_debug("speed_kmh", "fast") is False
    assert service.apply_debug("battery", 101) is False
    assert service.apply_debug("gear", "X") is False

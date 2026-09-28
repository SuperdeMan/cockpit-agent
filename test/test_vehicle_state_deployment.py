"""Consumer wiring and signing-key containment in the actual Compose graph."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
READERS = {
    "cloud-planner", "scene-orchestrator-agent", "info-agent", "road-safety-agent",
    "reminder-agent", "charging-planner-agent", "observability-collector", "proactive",
    "cloud-gateway", "edge-gateway", "edge-orchestrator",
}


def assert_wiring(services):
    for name in READERS:
        assert services[name]["environment"]["VEHICLE_STATE_TRUST"] == "${VEHICLE_STATE_TRUST:-}"
    for name, service in services.items():
        environment = service.get("environment", {})
        for key in ("VEHICLE_STATE_KEY_ID", "VEHICLE_STATE_PRIVATE_KEY"):
            if name == "edge-orchestrator":
                assert environment.get(key) == "${" + key + ":-}"
            else:
                assert key not in environment, name
        # A service-level env_file would silently inject the private key into
        # readers even though their explicit environment mapping is correct.
        assert "env_file" not in service, name


def services():
    return yaml.safe_load((ROOT / "deploy/docker-compose.yaml").read_text(encoding="utf-8"))["services"]


def test_all_state_readers_receive_public_policy_and_only_edge_receives_signing_material():
    assert_wiring(services())


@pytest.mark.parametrize("fault", ["missing_reader", "private_key_leak", "env_file_leak"])
def test_wiring_guard_detects_missing_trust_or_secret_spread(fault):
    graph = deepcopy(services())
    if fault == "missing_reader":
        del graph["observability-collector"]["environment"]["VEHICLE_STATE_TRUST"]
    elif fault == "private_key_leak":
        graph["cloud-gateway"]["environment"]["VEHICLE_STATE_PRIVATE_KEY"] = "${VEHICLE_STATE_PRIVATE_KEY:-}"
    else:
        graph["cloud-planner"]["env_file"] = ".env"
    with pytest.raises((AssertionError, KeyError)):
        assert_wiring(graph)

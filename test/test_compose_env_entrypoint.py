from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_root_compose_entrypoint_pins_root_as_the_environment_project_directory():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "path: deploy/docker-compose.yaml" in compose
    assert "project_directory: deploy" in compose
    assert "env_file: .env" in compose
    assert "COMPOSE := docker compose -f compose.yaml" in makefile


def test_registry_consumers_wait_for_registry_health_before_starting():
    """A started container is not ready: registry restores its DB before serving gRPC."""
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    registry_health = services["registry"].get("healthcheck") or {}
    assert registry_health.get("test")

    consumers = {
        name: service
        for name, service in services.items()
        if "registry" in (service.get("depends_on") or {})
    }
    assert consumers
    for name, service in consumers.items():
        dependency = (service.get("depends_on") or {}).get("registry") or {}
        assert dependency.get("condition") == "service_healthy", name


def test_privacy_delete_responders_wait_for_shared_redis_health():
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    for name in ("cloud-planner", "mcp-bridge"):
        service = services[name]
        assert service["environment"]["REDIS_URL"] == "redis://redis:6379/0"
        assert service["depends_on"]["redis"]["condition"] == "service_healthy"


def test_cloud_planner_image_installs_shared_redis_client():
    requirements = (
        ROOT / "orchestrator" / "cloud" / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()

    assert "redis==5.0.8" in requirements

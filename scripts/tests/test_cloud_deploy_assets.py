from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIR = ROOT / "deploy" / "cloud"
COMPOSE_PATH = CLOUD_DIR / "compose.cloud.yaml"
HMI_VITE_CONFIG_PATH = CLOUD_DIR / "vite.hmi.cloud.config.mjs"
BACKUP_PATH = CLOUD_DIR / "backup.sh"
SERVICE_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.service"
TIMER_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.timer"
RELEASE_SERVICES_PATH = CLOUD_DIR / "release-services.json"
RUNTIME_MODELS_PATH = CLOUD_DIR / "runtime-models.json"

LOOPBACK_PORTS = {
    "llm-gateway": ["127.0.0.1:50059:50059"],
    "observability-collector": ["127.0.0.1:8092:8092"],
    "edge-gateway": ["127.0.0.1:8090:8090"],
    "hmi": ["127.0.0.1:5173:5173"],
    "dashboard": ["127.0.0.1:5174:5174"],
}


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: _ComposeLoader, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node) if node.value else None


_ComposeLoader.add_constructor("!reset", _construct_reset)
_ComposeLoader.add_constructor("!override", _construct_reset)


def _required_text(path: Path) -> str:
    assert path.is_file(), f"required cloud deployment asset missing: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _release_service_rows() -> list[dict[str, str]]:
    payload = json.loads(_required_text(RELEASE_SERVICES_PATH))
    return payload["services"]


SELF_BUILT_SERVICES = {
    item["service"] for item in _release_service_rows()
}


def _cloud_compose() -> tuple[str, dict]:
    text = _required_text(COMPOSE_PATH)
    return text, yaml.load(text, Loader=_ComposeLoader)


def test_release_services_manifest_is_ordered_and_matches_cloud_compose():
    manifest = json.loads(_required_text(RELEASE_SERVICES_PATH))
    services = manifest["services"]
    names = [item["service"] for item in services]

    assert manifest["schema_version"] == 1
    assert len(names) == 26
    assert len(names) == len(set(names))
    assert set(names) == SELF_BUILT_SERVICES
    assert all(
        item["image"] == f"car-agent-release/{item['service']}"
        for item in services
    )


def test_runtime_model_manifest_has_exact_validated_files():
    manifest = json.loads(_required_text(RUNTIME_MODELS_PATH))
    models = manifest["models"]

    assert manifest["schema_version"] == 1
    assert {item["path"] for item in models} == {
        "models/nlu/edge_nlu.onnx",
        "models/nlu/labels.json",
        "models/nlu/vocab.json",
        "models/voiceprint/campplus_zh-cn_16k-common.onnx",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        for item in models
    )


def _service_block_has_reset(text: str, service: str, field: str) -> bool:
    return _service_block_has_tag(text, service, field, "reset")


def _service_block_has_tag(text: str, service: str, field: str, tag: str) -> bool:
    pattern = re.compile(
        rf"(?ms)^  {re.escape(service)}:\s*$"
        rf"(?P<body>.*?)(?=^  [a-z0-9-]+:\s*$|^volumes:\s*$|\Z)"
    )
    match = pattern.search(text)
    return bool(
        match
        and re.search(
            rf"(?m)^    {re.escape(field)}:\s*!{re.escape(tag)}",
            match["body"],
        )
    )


def test_cloud_compose_pins_all_self_built_images_and_disables_builds():
    text, compose = _cloud_compose()
    services = compose["services"]

    assert SELF_BUILT_SERVICES <= services.keys()
    for service in SELF_BUILT_SERVICES:
        spec = services[service]
        assert spec["image"] == (
            f"car-agent-release/{service}:${{RELEASE_SHA:?RELEASE_SHA required}}"
        )
        assert spec["pull_policy"] == "never"
        assert _service_block_has_reset(text, service, "build"), service


def test_cloud_compose_resets_every_base_port_and_only_restores_loopback_ingress():
    text, compose = _cloud_compose()
    base = yaml.safe_load((ROOT / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8"))
    base_with_ports = {
        name for name, spec in base["services"].items() if spec.get("ports")
    }

    for service in base_with_ports:
        expected_tag = "override" if service in LOOPBACK_PORTS else "reset"
        assert _service_block_has_tag(text, service, "ports", expected_tag), service

    published = {
        name: spec.get("ports")
        for name, spec in compose["services"].items()
        if spec.get("ports")
    }
    assert published == LOOPBACK_PORTS
    for ports in published.values():
        assert all(port.startswith("127.0.0.1:") for port in ports)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is unavailable")
def test_cloud_compose_tags_have_expected_real_merge_semantics(tmp_path: Path):
    base_path = tmp_path / "base.yaml"
    base_path.write_text(
        "services:\n"
        + "\n".join(
            f"  {name}:\n    image: busybox\n    ports:\n      - '{index}:80'"
            for index, name in enumerate(
                (
                    "redis", "nats", "postgres", "http-proxy", "registry",
                    "llm-gateway", "memory", "cloud-planner", "payment-gateway",
                    "observability-collector", "prometheus", "grafana",
                    "cloud-gateway", "edge-gateway", "edge-orchestrator", "hmi",
                    "dashboard",
                ),
                start=20000,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "RELEASE_SHA": "4c1f479",
            "TAILNET_FQDN": "car-agent-dev.example.ts.net",
            "VITE_WS_TOKEN": "test-token",
            "PERMISSIONS_FAIL_OPEN": "false",
        }
    )
    result = subprocess.run(
        [
            "docker", "compose", "-f", str(base_path), "-f", str(COMPOSE_PATH),
            "config", "--format", "json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    services = json.loads(result.stdout)["services"]
    published = {
        name: [
            f"{port['host_ip']}:{port['published']}:{port['target']}"
            for port in spec.get("ports") or []
        ]
        for name, spec in services.items()
        if spec.get("ports")
    }
    assert published == LOOPBACK_PORTS


def test_cloud_compose_uses_stable_data_volumes_and_redis_aof():
    _, compose = _cloud_compose()
    services = compose["services"]

    assert services["postgres"]["volumes"] == [
        "postgres-data:/var/lib/postgresql/data"
    ]
    assert services["redis"]["volumes"] == ["redis-data:/data"]
    assert services["observability-collector"]["volumes"] == ["obs-data:/data"]
    assert services["redis"]["command"] == [
        "redis-server",
        "--appendonly",
        "yes",
        "--appendfsync",
        "everysec",
    ]
    assert compose["volumes"] == {
        "postgres-data": {"name": "car-agent-postgres-data"},
        "redis-data": {"name": "car-agent-redis-data"},
        "obs-data": {"name": "car-agent-obs-data"},
    }


def test_cloud_planner_consumes_the_fail_closed_permission_setting():
    _, compose = _cloud_compose()

    assert compose["services"]["cloud-planner"]["environment"] == {
        "PERMISSIONS_FAIL_OPEN": (
            "${PERMISSIONS_FAIL_OPEN:?PERMISSIONS_FAIL_OPEN required}"
        )
    }


def test_cloud_frontends_use_tailnet_https_bases_and_derive_websockets_in_code():
    _, compose = _cloud_compose()
    services = compose["services"]
    hmi = services["hmi"]["environment"]
    dashboard = services["dashboard"]["environment"]

    expected_allowed_host = "${TAILNET_FQDN:?TAILNET_FQDN required}"
    assert hmi["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == expected_allowed_host
    assert dashboard["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == expected_allowed_host

    assert hmi["VITE_EDGE_GATEWAY_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
    )
    assert hmi["VITE_AUDIO_API_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8444"
    )
    assert dashboard["VITE_COLLECTOR_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8446"
    )
    assert dashboard["VITE_EDGE_GATEWAY_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
    )

    hmi_code = (ROOT / "hmi" / "src" / "App.tsx").read_text(encoding="utf-8")
    dashboard_api = (ROOT / "dashboard" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "GATEWAY.replace(/^http/, 'ws') + '/ws'" in hmi_code
    assert "BASE.replace(/^http/, 'ws') + '/stream'" in dashboard_api


def test_cloud_hmi_mounts_a_vite5_compatible_tailnet_host_config():
    _, compose = _cloud_compose()
    hmi = compose["services"]["hmi"]
    config = _required_text(HMI_VITE_CONFIG_PATH)

    assert hmi["volumes"] == [
        "/opt/car-agent/shared/vite.hmi.cloud.config.mjs:/app/vite.cloud.config.mjs:ro"
    ]
    assert hmi["command"] == [
        "npm", "run", "dev", "--", "--config", "vite.cloud.config.mjs"
    ]
    assert "./vite.config.ts" in config
    assert "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS" in config
    assert "allowedHosts" in config


def test_backup_is_non_destructive_and_uses_logical_database_backups():
    backup = _required_text(BACKUP_PATH)
    lowered = backup.lower()

    assert "set -euo pipefail" in backup
    assert "umask 077" in backup
    assert "pg_dump" in backup and "-Fc" in backup
    assert "redis-cli SAVE" in backup
    assert "redis:/data/dump.rdb" in backup
    assert "sqlite3" in backup and "iterdump" in backup
    assert "/data/obs.db" in backup
    assert "gzip" in backup
    assert "-mtime +7" in backup and "-print" in backup
    assert ".env" not in backup
    for forbidden in ("rm ", "unlink", "-delete", "rmdir"):
        assert forbidden not in lowered


def test_backup_targets_the_active_release_compose_project():
    backup = _required_text(BACKUP_PATH)

    assert 'readlink -f "${RELEASE_ROOT}"' in backup
    assert '--project-name "${COMPOSE_PROJECT_NAME}"' in backup
    assert '--project-directory "${RELEASE_DIR}"' in backup


def test_cloud_shell_assets_are_forced_to_lf_for_ubuntu():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/cloud/*.sh text eol=lf" in attributes.splitlines()


def test_backup_systemd_units_are_daily_persistent_and_have_no_cleanup_unit():
    service = _required_text(SERVICE_PATH)
    timer = _required_text(TIMER_PATH)

    assert "Type=oneshot" in service
    assert "ConditionPathExists=/opt/car-agent/current/compose.yaml" in service
    assert "ExecStart=/opt/car-agent/shared/bin/backup.sh" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert not (CLOUD_DIR / "systemd" / "car-agent-cleanup.service").exists()
    assert not (CLOUD_DIR / "systemd" / "car-agent-cleanup.timer").exists()


def test_cloud_runbook_documents_canonical_operations_and_provider_acceptance():
    readme = _required_text(CLOUD_DIR / "README.md")
    provider_guide = _required_text(ROOT / "docs" / "guides" / "provider-integration.md")

    for required in (
        "/opt/car-agent/current/compose.yaml",
        "/opt/car-agent/shared/compose.cloud.yaml",
        "--env-file /opt/car-agent/shared/.env",
        "--no-build --pull never",
        "cleanup-candidates.txt",
        "Tailscale",
        "SSH",
    ):
        assert required in readme
    assert "down -v" in readme
    assert "云端 demo 验收矩阵" in provider_guide
    for provider in ("高德", "和风", "Exa", "Tushare", "API-Football"):
        assert provider in provider_guide
    assert "只读" in provider_guide
    assert "创建订单" in provider_guide
    assert "real/mock" in provider_guide

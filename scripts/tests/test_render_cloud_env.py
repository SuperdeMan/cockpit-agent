from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.render_cloud_env import (
    DEMO_AUTH_SCOPES,
    CloudEnvError,
    render_cloud_env,
)


def _effective_values(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    keys: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        keys.append(key)
        values[key] = value.strip()
    return values, keys


def _valid_source() -> str:
    return """# local runtime values
AUTH_TOKENS=auth-token:u1:v1:navigation.control,location.read,network.external
DEEPSEEK_API_KEY=provider-secret-value
POSTGRES_DSN=postgresql://cockpit:cockpit@localhost:5432/cockpit
CLOUD_CHANNEL_TOKEN=stale-channel
DEPLOY_PROFILE=dev
DEPLOY_PROFILE=stale-duplicate
"""


def test_render_preserves_provider_values_and_sets_fail_closed_cloud_runtime(tmp_path: Path):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(_valid_source(), encoding="utf-8")

    render_cloud_env(
        source=source,
        output=output,
        release_sha="4c1f479",
        tailnet_fqdn="car-agent-dev.example.ts.net",
    )

    text = output.read_text(encoding="utf-8")
    values, keys = _effective_values(output)
    assert "# local runtime values" in text
    assert values["DEEPSEEK_API_KEY"] == "provider-secret-value"
    assert values["RELEASE_SHA"] == "4c1f479"
    assert values["TAILNET_FQDN"] == "car-agent-dev.example.ts.net"
    assert values["DEPLOY_PROFILE"] == "demo"
    assert values["AUTH_REQUIRED"] == "true"
    assert values["PERMISSIONS_FAIL_OPEN"] == "false"
    assert values["DEBUG_VEHICLE_CONTROL"] == "true"
    assert values["OBS_CONTENT_CAPTURE"] == "on"
    assert values["GRPC_TLS"] == "off"
    assert values["VITE_WS_TOKEN"] == "auth-token"
    assert values["CLOUD_CHANNEL_TOKEN"] == values["CLOUD_CHANNEL_TOKENS"]
    assert len(values["CLOUD_CHANNEL_TOKEN"]) >= 48
    assert values["POSTGRES_PASSWORD"] not in {"", "cockpit"}
    assert values["POSTGRES_DSN"] == (
        f"postgresql://cockpit:{values['POSTGRES_PASSWORD']}@postgres:5432/cockpit"
    )
    assert len(keys) == len(set(keys))


def test_render_upgrades_legacy_three_part_auth_with_new_demo_token(tmp_path: Path):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(
        "AUTH_TOKENS=legacy-token:owner-1:vehicle-1\n"
        "DEEPSEEK_API_KEY=provider-secret-value\n",
        encoding="utf-8",
    )

    render_cloud_env(
        source=source,
        output=output,
        release_sha="4c1f479",
        tailnet_fqdn="car-agent-dev.example.ts.net",
    )

    values, _keys = _effective_values(output)
    token, user_id, vehicle_id, scopes = values["AUTH_TOKENS"].split(":", 3)
    assert token != "legacy-token"
    assert len(token) >= 48
    assert user_id == "owner-1"
    assert vehicle_id == "vehicle-1"
    assert scopes.split(",") == list(DEMO_AUTH_SCOPES)
    assert values["VITE_WS_TOKEN"] == token


@pytest.mark.parametrize(
    "auth_line",
    [
        "",
        "AUTH_TOKENS=",
        "AUTH_TOKENS=sample:u1:v1:navigation.control",
        "AUTH_TOKENS=change-me:u1:v1:navigation.control",
        "AUTH_TOKENS=your-token:u1:v1:navigation.control",
        "AUTH_TOKENS=token:u1:v1:",
        "AUTH_TOKENS=malformed",
    ],
)
def test_render_rejects_missing_sample_or_malformed_auth_without_output(
    tmp_path: Path, auth_line: str
):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(f"DEEPSEEK_API_KEY=secret\n{auth_line}\n", encoding="utf-8")

    with pytest.raises(CloudEnvError, match="AUTH_TOKENS"):
        render_cloud_env(
            source=source,
            output=output,
            release_sha="4c1f479",
            tailnet_fqdn="car-agent-dev.example.ts.net",
        )

    assert not output.exists()


@pytest.mark.parametrize(
    "fqdn",
    [
        "car-agent-dev.example.com",
        "other-machine.example.ts.net",
        "car-agent-dev..ts.net",
        "CAR-AGENT-DEV.example.ts.net",
    ],
)
def test_render_rejects_untrusted_tailnet_hostname(tmp_path: Path, fqdn: str):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(_valid_source(), encoding="utf-8")

    with pytest.raises(CloudEnvError, match="TAILNET_FQDN"):
        render_cloud_env(
            source=source,
            output=output,
            release_sha="4c1f479",
            tailnet_fqdn=fqdn,
        )

    assert not output.exists()


def test_render_refuses_to_replace_the_source_file(tmp_path: Path):
    source = tmp_path / "source.env"
    original = _valid_source()
    source.write_text(original, encoding="utf-8")

    with pytest.raises(CloudEnvError, match="different files"):
        render_cloud_env(
            source=source,
            output=source,
            release_sha="4c1f479",
            tailnet_fqdn="car-agent-dev.example.ts.net",
        )

    assert source.read_text(encoding="utf-8") == original


def test_render_cleans_partial_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(_valid_source(), encoding="utf-8")

    def fail_replace(_source: os.PathLike[str], _target: os.PathLike[str]) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        render_cloud_env(
            source=source,
            output=output,
            release_sha="4c1f479",
            tailnet_fqdn="car-agent-dev.example.ts.net",
        )

    assert not output.exists()
    assert list(tmp_path.glob(".cloud.env.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows mode bits do not model POSIX 0600")
def test_render_sets_posix_output_mode_to_0600(tmp_path: Path):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(_valid_source(), encoding="utf-8")

    render_cloud_env(
        source=source,
        output=output,
        release_sha="4c1f479",
        tailnet_fqdn="car-agent-dev.example.ts.net",
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_cli_stdout_is_json_and_does_not_disclose_any_secret(tmp_path: Path):
    source = tmp_path / "source.env"
    output = tmp_path / "cloud.env"
    source.write_text(_valid_source(), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "render_cloud_env.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--output",
            str(output),
            "--release-sha",
            "4c1f479",
            "--tailnet-fqdn",
            "car-agent-dev.example.ts.net",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "output": "cloud.env"}
    for secret in ("provider-secret-value", "auth-token", "stale-channel", "cockpit"):
        assert secret not in result.stdout
        assert secret not in result.stderr

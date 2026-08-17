from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_e2e
from scripts.e2e_contract import load_manifest
from scripts.e2e_target import (
    E2ETarget,
    E2ETargetError,
    endpoint_environment,
    select_for_target,
)
from scripts.dev_stack_lib import LOCAL_ENDPOINTS, cloud_endpoints


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(ROOT / "test" / "e2e_manifest.yaml", repo_root=ROOT)


def parse(text: str):
    return run_e2e._parser().parse_args(text.split())


def test_local_target_keeps_existing_default_selection():
    args = parse("")
    selected, full = select_for_target(MANIFEST, args, target="local")

    assert selected == tuple(case for case in MANIFEST.cases if case.group == "default")
    assert full is True


def test_cloud_default_selects_only_root_remote_safe_cases():
    selected, full = select_for_target(MANIFEST, parse(""), target="cloud")

    assert selected
    assert all(case.remote_safe and case.profile == "root" for case in selected)
    assert tuple(case.id for case in selected) == ("e2e_protocol_smoke",)
    assert full is False


def test_cloud_mutating_requires_exact_id_switch_and_policy():
    base = MANIFEST.by_id["e2e_auth"]
    mutating = replace(base, remote_safe=False, remote_mutating=True, profile="root")
    manifest = SimpleNamespace(cases=(mutating,), by_id={mutating.id: mutating})

    with pytest.raises(E2ETargetError, match="exact --id"):
        select_for_target(manifest, parse("--allow-mutating"), target="cloud")
    with pytest.raises(E2ETargetError, match="allow-mutating"):
        select_for_target(manifest, parse(f"--id {mutating.id}"), target="cloud")
    selected, full = select_for_target(
        manifest,
        parse(f"--id {mutating.id} --allow-mutating"),
        target="cloud",
    )
    assert selected == (mutating,)
    assert full is False


@pytest.mark.parametrize(
    "arguments",
    (
        "--canonical --milestone M-A --lane milestone --full --provider p --model m",
        "--parallel-isolation 2 --milestone M-A --id e2e_memory --id e2e_voiceprint",
        "--full",
        "--profile real",
    ),
)
def test_cloud_rejects_local_only_runner_modes(arguments: str):
    with pytest.raises(E2ETargetError):
        select_for_target(MANIFEST, parse(arguments), target="cloud")


def test_cloud_rejects_signed_fixture_and_unreviewed_exact_cases():
    for case_id in ("e2e_auth", "e2e_voiceprint", "e2e_payment"):
        with pytest.raises(E2ETargetError):
            select_for_target(
                MANIFEST,
                parse(f"--id {case_id}"),
                target="cloud",
            )


def test_endpoint_environment_injects_every_runner_endpoint_and_release():
    target = E2ETarget("cloud", cloud_endpoints("demo.ts.net"), "a" * 40)

    assert endpoint_environment(target) == {
        "WS_URL": "wss://demo.ts.net:8443/ws",
        "EDGE_HTTP_URL": "https://demo.ts.net:8443",
        "AUDIO_API_URL": "https://demo.ts.net:8444",
        "VITE_AUDIO_API_URL": "https://demo.ts.net:8444",
        "E2E_AUDIO_API_ORIGIN": "https://demo.ts.net:8444",
        "COLLECTOR_URL": "https://demo.ts.net:8446",
        "COLLECTOR_WS_URL": "wss://demo.ts.net:8446/stream",
        "HMI_URL": "https://demo.ts.net",
        "DASHBOARD_URL": "https://demo.ts.net:8445",
        "E2E_TARGET": "cloud",
        "E2E_TARGET_RELEASE_SHA": "a" * 40,
    }


def test_local_endpoint_environment_is_explicit():
    env = endpoint_environment(E2ETarget("local", LOCAL_ENDPOINTS, None))
    assert env["WS_URL"] == "ws://localhost:8090/ws"
    assert env["E2E_TARGET"] == "local"
    assert env["E2E_TARGET_RELEASE_SHA"] == ""

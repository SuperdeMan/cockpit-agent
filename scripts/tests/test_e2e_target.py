from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import io
import json

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
from scripts.cloud_release_lib import SshConfig
from scripts.cloud_remote_lock import RemoteCloudLock, RemoteLockError


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
    assert tuple(case.id for case in selected) == (
        "e2e_protocol_smoke", "e2e_remote_safe",
    )
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


class FakeLockProcess:
    def __init__(self, ack: bytes):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(ack)
        self.stderr = io.BytesIO(b"cloud transaction lock held by release\n")
        self.returncode = None
        self.waited = False
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited = True
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = 1


def test_remote_lock_holds_until_context_exit(tmp_path: Path):
    process = FakeLockProcess(b"READY e2e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    calls = []
    ssh = SshConfig("demo.example", "ubuntu", tmp_path / "identity")

    def popen(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return process

    with RemoteCloudLock(
        ssh=ssh,
        run_id="e2e-" + "a" * 32,
        popen=popen,
    ) as lock:
        assert lock.identity == "e2e-" + "a" * 32
        assert process.stdin.closed is False

    assert process.stdin.closed is True
    assert process.waited is True
    assert calls[0][0][-1] == (
        "sudo /opt/car-agent/shared/bin/remote-e2e-lock.sh "
        "hold --run-id e2e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )


def test_remote_lock_rejects_busy_or_wrong_ack(tmp_path: Path):
    process = FakeLockProcess(b"BUSY\n")
    ssh = SshConfig("demo.example", "ubuntu", tmp_path / "identity")

    with pytest.raises(RemoteLockError, match="release"):
        RemoteCloudLock(
            ssh=ssh,
            run_id="e2e-" + "b" * 32,
            popen=lambda *_args, **_kwargs: process,
        ).acquire()
    assert process.terminated


def test_remote_lock_ack_timeout_is_fail_closed(tmp_path: Path, monkeypatch):
    process = FakeLockProcess(b"")
    ssh = SshConfig("demo.example", "ubuntu", tmp_path / "identity")

    class TimedOutFuture:
        def result(self, timeout):
            raise TimeoutError

    class FakeExecutor:
        def __init__(self, max_workers):
            pass
        def submit(self, func):
            return TimedOutFuture()
        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr("scripts.cloud_remote_lock.concurrent.futures.ThreadPoolExecutor", FakeExecutor)
    with pytest.raises(RemoteLockError, match="timed out"):
        RemoteCloudLock(
            ssh=ssh,
            run_id="e2e-" + "c" * 32,
            popen=lambda *_args, **_kwargs: process,
        ).acquire()
    assert process.terminated


def test_cloud_runner_releases_remote_lock_in_finally(tmp_path: Path, monkeypatch):
    identity = tmp_path / "identity"
    identity.write_text("test", encoding="utf-8")
    events = []

    class FakeRemoteLock:
        def __init__(self, **kwargs):
            self.run_id = kwargs["run_id"]
        def acquire(self):
            events.append(("acquire", self.run_id))
            return self
        def release(self):
            events.append(("release", self.run_id))

    monkeypatch.setattr(run_e2e, "RemoteCloudLock", FakeRemoteLock)
    monkeypatch.setattr(run_e2e, "validate_ssh_identity", lambda _path: None)
    monkeypatch.setattr(
        run_e2e,
        "_run_child",
        lambda case, **_kwargs: {"id": case.id, "status": "PASS", "errors": []},
    )
    output = io.StringIO()
    rc = run_e2e.main(
        [
            "--target", "cloud", "--id", "e2e_protocol_smoke",
            "--host", "demo.example", "--identity", str(identity),
        ],
        repo_root=ROOT,
        environ={"TAILNET_FQDN": "demo.ts.net"},
        stdout=output,
        staleness_evaluator=lambda _root: {"stale": False, "reasons": []},
    )

    assert rc == 0
    assert [event[0] for event in events] == ["acquire", "release"]
    summary = json.loads(output.getvalue().splitlines()[-1])
    assert summary["remote_lock"]["kind"] == "e2e"


def test_remote_safe_probe_uses_only_runner_endpoints_and_isolated_identity():
    source = (ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8")
    for name in (
        "HMI_URL", "EDGE_HTTP_URL", "WS_URL", "AUDIO_API_URL",
        "DASHBOARD_URL", "COLLECTOR_URL", "COLLECTOR_WS_URL",
    ):
        assert f'os.environ["{name}"]' in source
    assert "localhost" not in source
    assert '"memory_enabled": False' in source
    assert "docker" not in source.lower()
    assert "subprocess" not in source

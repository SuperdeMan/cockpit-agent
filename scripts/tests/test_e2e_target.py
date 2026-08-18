from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import ast
import io
import json
import re
import subprocess
import sys
import time

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


def test_remote_lock_release_rejects_disconnect_without_release_protocol(tmp_path: Path):
    process = FakeLockProcess(b"READY e2e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")

    class DisconnectedStdin:
        def write(self, payload):
            raise BrokenPipeError

        def close(self):
            pass

    process.stdin = DisconnectedStdin()
    lock = RemoteCloudLock(
        ssh=SshConfig("demo.example", "ubuntu", tmp_path / "identity"),
        run_id="e2e-" + "a" * 32,
        popen=lambda *args, **kwargs: process,
    ).acquire()

    with pytest.raises(RemoteLockError, match="lease was lost"):
        lock.release()

    assert process.terminated is True
    assert lock._process is None


def test_remote_lock_health_rejects_ssh_255_after_ack(tmp_path: Path):
    process = FakeLockProcess(b"READY e2e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    lock = RemoteCloudLock(
        ssh=SshConfig("demo.example", "ubuntu", tmp_path / "identity"),
        run_id="e2e-" + "a" * 32,
        popen=lambda *args, **kwargs: process,
    ).acquire()
    process.returncode = 255

    with pytest.raises(RemoteLockError, match="lease was lost"):
        lock.ensure_held()
    with pytest.raises(RemoteLockError, match="lease was lost"):
        lock.release()

    assert lock._process is None


def test_remote_lock_acquire_rejects_ack_from_already_dead_ssh(tmp_path: Path):
    process = FakeLockProcess(b"READY e2e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    process.returncode = 255

    with pytest.raises(RemoteLockError, match="lease was lost"):
        RemoteCloudLock(
            ssh=SshConfig("demo.example", "ubuntu", tmp_path / "identity"),
            run_id="e2e-" + "a" * 32,
            popen=lambda *args, **kwargs: process,
        ).acquire()


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
        def ensure_held(self):
            events.append(("held", self.run_id))
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
    assert [event[0] for event in events] == [
        "acquire", "held", "held", "release",
    ]
    summary = json.loads(output.getvalue().splitlines()[-1])
    assert summary["remote_lock"]["kind"] == "e2e"


def test_cloud_runner_fails_when_remote_lock_dies_after_green_case(tmp_path: Path, monkeypatch):
    identity = tmp_path / "identity"
    identity.write_text("test", encoding="utf-8")

    class LostRemoteLock:
        def __init__(self, **kwargs):
            self.run_id = kwargs["run_id"]
        def acquire(self):
            return self
        def ensure_held(self):
            return None
        def release(self):
            raise RemoteLockError("remote lock lease was lost")

    monkeypatch.setattr(run_e2e, "RemoteCloudLock", LostRemoteLock)
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

    summary = json.loads(output.getvalue().splitlines()[-1])
    assert rc == 1
    assert "remote_lock" in summary["errors"]
    assert summary["results"][0]["status"] == "FAIL"


def test_cloud_runner_reports_lock_lost_before_first_case(tmp_path: Path, monkeypatch):
    identity = tmp_path / "identity"
    identity.write_text("test", encoding="utf-8")

    class LostBeforeRunLock:
        def __init__(self, **kwargs):
            self.run_id = kwargs["run_id"]
        def acquire(self):
            return self
        def ensure_held(self):
            raise RemoteLockError("remote lock lease was lost")
        def release(self):
            raise RemoteLockError("remote lock lease was lost")

    monkeypatch.setattr(run_e2e, "RemoteCloudLock", LostBeforeRunLock)
    monkeypatch.setattr(run_e2e, "validate_ssh_identity", lambda _path: None)
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

    summary = json.loads(output.getvalue().splitlines()[-1])
    assert rc == 1
    assert summary["errors"] == ["remote_lock"]
    assert summary["results"][0]["status"] == "FAIL"


def test_remote_probe_identity_is_read_from_its_validated_artifact(tmp_path: Path):
    artifact = tmp_path / "remote_provider_catalog.json"
    artifact.write_text(
        json.dumps({"provider": "deepseek", "model": "deepseek-v4-flash"}),
        encoding="utf-8",
    )

    assert run_e2e._remote_probe_identity([{
        "id": "e2e_remote_safe",
        "artifacts": [str(artifact)],
    }]) == ("deepseek", "deepseek-v4-flash")


def test_remote_safe_probe_uses_only_runner_endpoints_and_isolated_identity():
    source = (ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8")
    for name in (
        "HMI_URL", "EDGE_HTTP_URL", "WS_URL", "E2E_AUDIO_API_ORIGIN",
        "DASHBOARD_URL", "COLLECTOR_URL", "COLLECTOR_WS_URL",
    ):
        assert f'os.environ["{name}"]' in source
    assert "localhost" not in source
    assert '"memory_enabled": "false"' in source
    assert "docker" not in source.lower()
    assert "subprocess" not in source


def test_remote_lock_ack_survives_a_login_banner_larger_than_the_stderr_pipe(
    tmp_path: Path,
):
    """The real host greets every ssh session with a multi-KiB banner on stderr.

    A pipe nobody reads holds only 4 KiB on Windows, so the remote end blocks on
    its own banner and never gets to write the ack. This needs a real child
    process: in-memory streams cannot reproduce a full pipe.
    """
    run_id = "e2e-" + "d" * 32
    script = tmp_path / "banner_then_ack.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.buffer.write(b'B' * 262144)\n"
        "sys.stderr.buffer.flush()\n"
        f"sys.stdout.write('READY {run_id}' + chr(10))\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n",
        encoding="utf-8",
    )

    def popen(argv, **kwargs):
        return subprocess.Popen([sys.executable, str(script)], **kwargs)

    started = time.monotonic()
    with RemoteCloudLock(
        ssh=SshConfig("demo.example", "ubuntu", tmp_path / "identity"),
        run_id=run_id,
        popen=popen,
    ) as lock:
        assert lock.identity == run_id
    assert time.monotonic() - started < 15


def test_remote_safe_probe_reads_only_names_the_runner_hands_the_child(
    tmp_path: Path,
):
    """The scan test above only proves the probe *names* runner endpoints.

    AUDIO_API_URL was named there and stripped by _child_environment, so the one
    case cloud verification runs could never survive its own import.
    """
    source = (ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8")
    required = set(re.findall('os[.]environ[[]"([A-Z0-9_]+)"[]]', source))
    assert required

    target = E2ETarget("cloud", cloud_endpoints("demo.ts.net"), "a" * 40)
    declared = {
        line.split("=", 1)[0].strip(): "declared"
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    declared.update(endpoint_environment(target))
    delivered = run_e2e._child_environment(
        declared,
        case=MANIFEST.by_id["e2e_remote_safe"],
        run_id="e2e-" + "f" * 32,
        result_file=tmp_path / "result.json",
        artifact_dir=tmp_path / "artifacts",
        lane=None,
        provider=None,
        model=None,
    )

    assert sorted(required - set(delivered)) == []


def _frame_types_the_probe_reacts_to(source: str) -> set[str]:
    """Every literal the probe compares a frame's ``type`` against."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Attribute)
            and left.func.attr == "get"
            and left.args
            and isinstance(left.args[0], ast.Constant)
            and left.args[0].value == "type"
        ):
            continue
        for item in node.comparators:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.add(item.value)
            elif isinstance(item, (ast.Set, ast.Tuple, ast.List)):
                names.update(
                    element.value
                    for element in item.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
    return names


def test_remote_safe_probe_waits_only_for_frames_the_edge_gateway_emits():
    """hello_ack belongs to the cloud gRPC channel, not the edge WebSocket.

    The probe waited for it as its handshake, so the round trip could not
    complete against any real stack no matter how healthy the deployment was.
    """
    awaited = _frame_types_the_probe_reacts_to(
        (ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8"),
    )
    assert awaited

    gateway = (ROOT / "gateway/edge/main.go").read_text(encoding="utf-8")
    emitted = set(re.findall('"type":[ ]*"([a-z_0-9]+)"', gateway))

    assert sorted(awaited - emitted) == []


_GO_SCALARS = {"string": str, "bool": bool}


def _ws_request_field_types() -> dict[str, str]:
    """Field types of the gateway's wsRequest, keyed by wire name."""
    source = (ROOT / "gateway/edge/main.go").read_text(encoding="utf-8")
    block = source.split("type wsRequest struct {", 1)[1].split("}", 1)[0]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0].startswith("//"):
            continue
        tag = re.search('json:"([a-z_0-9]+)"', line)
        if tag is not None:
            fields[tag.group(1)] = parts[1]
    return fields


def _probe_ws_payload() -> ast.Dict:
    tree = ast.parse((ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "payload"
            and isinstance(node.value, ast.Dict)
        ):
            return node.value
    raise AssertionError("the probe no longer builds a payload literal")


def test_remote_safe_probe_payload_matches_the_gateway_wire_contract():
    """A meta value of the wrong type fails the gateway's whole unmarshal.

    The request is then dropped with no frame back and no log line, which reads
    exactly like a dead stack. Checking the payload's source text cannot see it.
    """
    fields = _ws_request_field_types()
    assert fields["meta"] == "map[string]string"

    payload = _probe_ws_payload()
    for key_node, value_node in zip(payload.keys, payload.values):
        assert isinstance(key_node, ast.Constant)
        key = key_node.value
        declared = fields.get(key)
        assert declared is not None, f"{key} is not a wsRequest field"
        if declared == "map[string]string":
            assert isinstance(value_node, ast.Dict), key
            for item in value_node.values:
                if isinstance(item, ast.Constant):
                    assert isinstance(item.value, str), f"meta.{key}"
        elif isinstance(value_node, ast.Constant):
            expected = _GO_SCALARS.get(declared)
            if expected is not None:
                assert isinstance(value_node.value, expected), key


def test_runner_unit_tests_never_inherit_the_repository_deployment_target():
    """`runner.main` resolves the target from the repo's dev-stack.local.

    Seventeen unit tests turned red the moment the operator switched that file to
    cloud -- nothing about the code under test had changed. Any in-process runner
    invocation with a literal argv has to say which target it means.
    """
    offenders = []
    for name in ("test_e2e_stack_lease", "test_e2e_profiles", "test_run_e2e"):
        source = (Path(__file__).resolve().parent / f"{name}.py").read_text(
            encoding="utf-8",
        )
        for match in re.finditer(r"\brunner\.main\(\s*\[", source):
            closing = source.index("]", match.end())
            if '"--target"' not in source[match.end():closing]:
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{name}.py:{line}")

    assert offenders == []

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from scripts import dev_stack_lib as dev
from scripts import dev_stack as cli
from scripts.cloud_release_lib import (
    CommandResult,
    ReleaseError,
    ReleaseRequest,
    SshConfig,
)


def test_development_stack_cli_exposes_all_six_actions():
    result = subprocess.run(
        [sys.executable, "scripts/dev_stack.py", "--help"],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert all(
        action in result.stdout
        for action in ("target", "status", "deploy", "verify", "hmi", "dashboard")
    )


@contextmanager
def temporary_http_server(handler_type: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def write_stack_target(repo: Path, content: bytes) -> Path:
    target = repo / "dev-stack.local"
    target.write_bytes(content)
    return target


def test_resolve_target_defaults_to_local_when_root_file_is_missing(tmp_path: Path):
    assert dev.resolve_target(tmp_path) == dev.TargetSelection(
        name="local", source="default"
    )


def test_resolve_target_accepts_exact_file_and_explicit_argument(tmp_path: Path):
    write_stack_target(tmp_path, b"target=cloud\n")

    assert dev.resolve_target(tmp_path) == dev.TargetSelection(
        name="cloud", source="file"
    )
    assert dev.resolve_target(tmp_path, target="local") == dev.TargetSelection(
        name="local", source="argument"
    )


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b"target=local\n\n", "local"),
        (b"target=cloud\n\n\n", "cloud"),
    ],
)
def test_resolve_target_accepts_empty_lines_after_the_target(
    tmp_path: Path, content: bytes, name: str
):
    write_stack_target(tmp_path, content)

    assert dev.resolve_target(tmp_path) == dev.TargetSelection(
        name=name, source="file"
    )


@pytest.mark.parametrize(
    "content",
    [
        b"target=remote\n",
        b"target=cloud\ntarget=local\n",
        b"target=cloud\nextra=value\n",
        b"target=cloud+extra=x\n",
        b"target=\n",
        b"\xef\xbb\xbftarget=cloud\n",
    ],
    ids=[
        "unknown-target",
        "duplicate-key",
        "second-nonempty-line",
        "extra-assignment",
        "empty-value",
        "bom",
    ],
)
def test_resolve_target_rejects_malformed_root_file(
    tmp_path: Path, content: bytes
):
    write_stack_target(tmp_path, content)

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


def test_resolve_target_rejects_invalid_utf8(tmp_path: Path):
    write_stack_target(tmp_path, b"target=cloud\n\xff")

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


def test_resolve_target_rejects_target_larger_than_the_declared_limit(tmp_path: Path):
    write_stack_target(
        tmp_path, b"target=cloud\n" + (b"\n" * dev.TARGET_FILE_MAX_BYTES)
    )

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


def test_resolve_target_rejects_windows_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = write_stack_target(tmp_path, b"target=cloud\n")
    real_lstat = os.lstat

    def reparse_lstat(path: os.PathLike[str] | str):
        result = real_lstat(path)
        if Path(path) == target:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_size=result.st_size,
                st_file_attributes=0x400,
            )
        return result

    monkeypatch.setattr(dev.os, "lstat", reparse_lstat)

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


def test_resolve_target_rejects_path_identity_change_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = write_stack_target(tmp_path, b"target=cloud\n")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"target=local\n")
    real_lstat = os.lstat
    calls = 0

    def swap_before_second_lstat(path: os.PathLike[str] | str):
        nonlocal calls
        if Path(path) == target:
            calls += 1
            if calls == 2:
                os.replace(replacement, target)
        return real_lstat(path)

    monkeypatch.setattr(dev.os, "lstat", swap_before_second_lstat)

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


def test_resolve_target_rejects_symbolic_link(tmp_path: Path):
    source = tmp_path / "elsewhere"
    source.write_bytes(b"target=cloud\n")
    target = tmp_path / "dev-stack.local"
    try:
        target.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {type(exc).__name__}")

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="FIFO creation is POSIX-only")
def test_resolve_target_rejects_fifo_without_blocking(tmp_path: Path):
    fifo = tmp_path / "dev-stack.local"
    os.mkfifo(fifo)

    with pytest.raises(dev.DevStackError):
        dev.resolve_target(tmp_path)


@pytest.mark.parametrize("target", ["", "remote", "cloud\n", "local "])
def test_set_target_allows_only_exact_local_or_cloud(tmp_path: Path, target: str):
    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, target)


def test_set_target_atomically_replaces_only_the_target_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    root_env = tmp_path / ".env"
    root_env.write_bytes(b"SECRET_VALUE=must-not-be-read-or-changed\n")
    observed: list[tuple[str, Path]] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(fd: int) -> None:
        observed.append(("fsync", tmp_path / "dev-stack.local.partial"))
        real_fsync(fd)

    def record_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        observed.append(("replace", source_path))
        assert source_path.parent == tmp_path
        assert source_path.name.startswith(".dev-stack.local.")
        assert source_path.name.endswith(".partial")
        assert source_path.read_bytes() == b"target=cloud\n"
        assert destination_path == stack_target
        assert stack_target.read_bytes() == b"target=local\n"
        real_replace(source, destination)

    monkeypatch.setattr(dev.os, "fsync", record_fsync)
    monkeypatch.setattr(dev.os, "replace", record_replace)

    dev.set_target(tmp_path, "cloud")

    assert stack_target.read_bytes() == b"target=cloud\n"
    assert not list(tmp_path.glob(".dev-stack.local.*.partial"))
    assert root_env.read_bytes() == b"SECRET_VALUE=must-not-be-read-or-changed\n"
    assert [event for event, _ in observed] == ["fsync", "replace"]


def test_set_target_leaves_an_unrelated_partial_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    partial = tmp_path / "dev-stack.local.partial"
    partial.write_bytes(b"user-created-partial")

    dev.set_target(tmp_path, "cloud")

    assert stack_target.read_bytes() == b"target=cloud\n"
    assert partial.read_bytes() == b"user-created-partial"


def test_set_target_rejects_replaced_temporary_file_before_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    real_lstat = os.lstat
    replace_called = False
    armed = False

    def foreign_temporary_lstat(path: os.PathLike[str] | str):
        result = real_lstat(path)
        if armed and str(path).endswith(".partial"):
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
                st_size=result.st_size,
                st_file_attributes=getattr(result, "st_file_attributes", 0),
            )
        return result

    real_fsync = os.fsync

    def arm_race(fd: int) -> None:
        nonlocal armed
        real_fsync(fd)
        armed = True

    def record_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        nonlocal replace_called
        replace_called = True

    monkeypatch.setattr(dev.os, "lstat", foreign_temporary_lstat)
    monkeypatch.setattr(dev.os, "fsync", arm_race)
    monkeypatch.setattr(dev.os, "replace", record_replace)

    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, "cloud")

    assert not replace_called
    assert stack_target.read_bytes() == b"target=local\n"


def test_set_target_fails_when_os_replace_receives_a_swapped_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    real_replace = os.replace

    def swap_source_then_replace(
        source: os.PathLike[str], destination: os.PathLike[str]
    ) -> None:
        source_path = Path(source)
        displaced = tmp_path / "displaced-owned-temporary"
        real_replace(source_path, displaced)
        source_path.write_bytes(b"target=local\n")
        real_replace(source_path, destination)

    monkeypatch.setattr(dev.os, "replace", swap_source_then_replace)

    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, "cloud")

    assert stack_target.read_bytes() == b"target=local\n"


@pytest.mark.skipif(os.name == "nt", reason="real temporary replacement requires POSIX unlink semantics")
def test_set_target_rejects_real_temporary_file_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    real_mkstemp = dev.tempfile.mkstemp
    real_fsync = os.fsync
    real_replace = os.replace
    temporary: Path | None = None
    replace_called = False

    def capture_mkstemp(*args, **kwargs):
        nonlocal temporary
        fd, name = real_mkstemp(*args, **kwargs)
        temporary = Path(name)
        return fd, name

    def replace_temporary_after_fsync(fd: int) -> None:
        real_fsync(fd)
        assert temporary is not None
        displaced = tmp_path / "displaced-owned-temporary"
        real_replace(temporary, displaced)
        temporary.write_bytes(b"target=local\n")

    def record_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        nonlocal replace_called
        replace_called = True
        real_replace(source, destination)

    monkeypatch.setattr(dev.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(dev.os, "fsync", replace_temporary_after_fsync)
    monkeypatch.setattr(dev.os, "replace", record_replace)

    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, "cloud")

    assert not replace_called
    assert stack_target.read_bytes() == b"target=local\n"
    assert temporary is not None
    assert temporary.read_bytes() == b"target=local\n"


@pytest.mark.parametrize(
    "fqdn",
    ["car-agent-dev.example.ts.net", "a.ts.net", "with-hyphen.ts.net"],
)
def test_cloud_endpoints_builds_the_single_declared_endpoint_model(fqdn: str):
    assert dev.cloud_endpoints(fqdn) == dev.StackEndpoints(
        hmi=f"https://{fqdn}",
        edge_http=f"https://{fqdn}:8443",
        edge_ws=f"wss://{fqdn}:8443/ws",
        audio=f"https://{fqdn}:8444",
        dashboard=f"https://{fqdn}:8445",
        collector_http=f"https://{fqdn}:8446",
        collector_ws=f"wss://{fqdn}:8446/stream",
    )


@pytest.mark.parametrize(
    "fqdn",
    [
        "",
        "example.com",
        "https://example.ts.net",
        "UPPER.ts.net",
        ".ts.net",
        "a..b.ts.net",
        "a.-b.ts.net",
        "a-.b.ts.net",
        f"{'a' * 64}.ts.net",
    ],
)
def test_cloud_endpoints_rejects_missing_or_invalid_tailnet_fqdn(fqdn: str):
    with pytest.raises(dev.DevStackError, match="TAILNET_FQDN is missing or invalid"):
        dev.cloud_endpoints(fqdn)


def test_local_endpoints_are_the_declared_fixed_values():
    assert dev.LOCAL_ENDPOINTS == dev.StackEndpoints(
        hmi="http://localhost:5173",
        edge_http="http://localhost:8090",
        edge_ws="ws://localhost:8090/ws",
        audio="http://localhost:50059",
        dashboard="http://localhost:5174",
        collector_http="http://localhost:8092",
        collector_ws="ws://localhost:8092/stream",
    )


def test_read_root_env_returns_only_requested_keys(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "TAILNET_FQDN=car-agent-dev.example.ts.net\n"
        "UNREQUESTED_SECRET=must-not-leak\n"
        "QUOTED_VALUE='supported literal value'\n",
        encoding="utf-8",
    )

    assert dev.read_root_env(tmp_path, {"TAILNET_FQDN", "MISSING"}) == {
        "TAILNET_FQDN": "car-agent-dev.example.ts.net"
    }


def test_read_root_env_parses_only_the_safe_nonexecuting_dotenv_subset(
    tmp_path: Path,
):
    (tmp_path / ".env").write_text(
        "# full-line comment\n"
        "DOUBLE=\"double # literal and \\\"quote\\\"\" # comment\n"
        "SINGLE='single # literal and \\'quote\\'' # comment\n"
        "UNQUOTED=value with spaces # comment\n"
        "SPACED=  retained leading and trimmed trailing  # comment\n"
        "SHELL=$(uname)-${HOME}-`id`\n"
        "UNREQUESTED_SECRET=must-not-leak\n",
        encoding="utf-8",
    )

    assert dev.read_root_env(
        tmp_path, {"DOUBLE", "SINGLE", "UNQUOTED", "SPACED", "SHELL"}
    ) == {
        "DOUBLE": 'double # literal and "quote"',
        "SINGLE": "single # literal and 'quote'",
        "UNQUOTED": "value with spaces",
        "SPACED": "retained leading and trimmed trailing",
        "SHELL": "$(uname)-${HOME}-`id`",
    }


def test_read_root_env_distinguishes_unquoted_hashes_from_comments(
    tmp_path: Path,
):
    (tmp_path / ".env").write_text(
        "LITERAL=abc#def\n"
        "COMMENT=abc # comment\n"
        "OUTER_SPACE=  abc  # comment\n",
        encoding="utf-8",
    )

    assert dev.read_root_env(tmp_path, {"LITERAL", "COMMENT", "OUTER_SPACE"}) == {
        "LITERAL": "abc#def",
        "COMMENT": "abc",
        "OUTER_SPACE": "abc",
    }


@pytest.mark.parametrize(
    "content",
    [
        b"VALUE=\"unterminated\n",
        b"VALUE='unterminated\n",
        b"VALUE=\"closed\" trailing\n",
        b"VALUE='closed' trailing\n",
        b"VALUE=unquoted \"quote\"\n",
        b"UNREQUESTED='unterminated\nREQUESTED=ok\n",
    ],
    ids=[
        "unterminated-double",
        "unterminated-single",
        "double-trailing-junk",
        "single-trailing-junk",
        "unquoted-quote",
        "unrequested-malformed",
    ],
)
def test_read_root_env_rejects_dotenv_syntax_even_for_unrequested_keys(
    tmp_path: Path, content: bytes
):
    (tmp_path / ".env").write_bytes(content)

    with pytest.raises(dev.DevStackError):
        dev.read_root_env(tmp_path, {"REQUESTED"})


def test_read_root_env_rejects_bare_string_keys(tmp_path: Path):
    (tmp_path / ".env").write_text("TAILNET_FQDN=a.ts.net\n", encoding="utf-8")

    with pytest.raises(dev.DevStackError):
        dev.read_root_env(tmp_path, "TAILNET_FQDN")


def test_read_root_env_rejects_file_larger_than_the_declared_limit(tmp_path: Path):
    (tmp_path / ".env").write_bytes(
        b"TAILNET_FQDN=a.ts.net\n" + (b"#\n" * dev.ROOT_ENV_MAX_BYTES)
    )

    with pytest.raises(dev.DevStackError):
        dev.read_root_env(tmp_path, {"TAILNET_FQDN"})


@pytest.mark.parametrize(
    "content",
    [
        b"TAILNET_FQDN=a.ts.net\nTAILNET_FQDN=b.ts.net\n",
        b"TAILNET_FQDN\n",
        b"TAILNET_FQDN=a.ts.net\x00\n",
        b"TAILNET_FQDN='unterminated\n",
        b"TAILNET_FQDN=a.ts.net'\n",
    ],
    ids=[
        "duplicate-key",
        "missing-equals",
        "nul",
        "unterminated-quote",
        "trailing-unmatched-quote",
    ],
)
def test_read_root_env_fails_closed_on_malformed_input(
    tmp_path: Path, content: bytes
):
    (tmp_path / ".env").write_bytes(content)

    with pytest.raises(dev.DevStackError) as caught:
        dev.read_root_env(tmp_path, {"TAILNET_FQDN"})

    message = str(caught.value)
    assert "TAILNET_FQDN" not in message
    assert "a.ts.net" not in message


def test_read_root_env_fails_closed_when_file_is_missing(tmp_path: Path):
    with pytest.raises(dev.DevStackError):
        dev.read_root_env(tmp_path, {"TAILNET_FQDN"})


def test_read_root_env_fails_closed_when_path_is_not_a_readable_file(tmp_path: Path):
    (tmp_path / ".env").mkdir()

    with pytest.raises(dev.DevStackError):
        dev.read_root_env(tmp_path, {"TAILNET_FQDN"})


class FakeStatusRunner:
    def __init__(
        self,
        command_results: list[object],
        endpoint_results: dict[str, object],
    ) -> None:
        self.command_results = list(command_results)
        self.endpoint_results = endpoint_results
        self.commands: list[tuple[str, ...]] = []
        self.urls: list[str] = []

    def run(self, argv, *, cwd, **_kwargs):
        self.commands.append(tuple(argv))
        result = self.command_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def get(self, url: str, *, timeout_s: float):
        self.urls.append(url)
        result = self.endpoint_results[url]
        if isinstance(result, BaseException):
            raise result
        return result


def command_result(*argv: str, stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(tuple(argv), returncode, stdout, "")


def test_inspect_local_status_only_reads_compose_and_checks_five_fixed_endpoints(
    tmp_path: Path,
):
    containers = "\n".join(
        json.dumps({"Service": service, "State": "running"})
        for service in (
            "postgres",
            "redis",
            "edge-gateway",
            "llm-gateway",
            "observability-collector",
            "hmi",
            "dashboard",
        )
    )
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [
            command_result("docker", "info"),
            command_result(
                "docker", "compose", "-f", "compose.yaml", "ps", "--all", "--format", "json",
                stdout=containers,
            ),
        ],
        urls,
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)

    assert status.target == "local"
    assert status.release_sha is None
    assert (status.container_total, status.container_running) == (7, 7)
    assert status.healthy_endpoints == 5
    assert not status.warnings
    assert runner.commands == [
        ("docker", "info"),
        ("docker", "compose", "-f", "compose.yaml", "ps", "--all", "--format", "json"),
    ]
    assert runner.urls == list(urls)
    assert all("up" not in command and "build" not in command for command in runner.commands)


def test_inspect_cloud_status_is_read_only_and_serializes_no_sensitive_transport_data(
    tmp_path: Path,
):
    identity = tmp_path / "agent.pem"
    identity.write_text("not-a-real-key\n", encoding="utf-8")
    request = ReleaseRequest(
        repo=tmp_path,
        revision="1" * 40,
        artifact_root=tmp_path / "artifacts",
        ssh=SshConfig(host="server.example.invalid", user="ubuntu", identity=identity),
    )
    remote_state = json.dumps(
        {
            "current_release": "1" * 40,
            "current_path": f"/opt/car-agent/releases/{'1' * 40}",
            "runtime_project_name": "caragent",
            "approved_infrastructure_digest": None,
            "disk_available_bytes": 9,
            "memory_available_bytes": 8,
            "release_lock_available": True,
            "runtime_project_ready": True,
            "shared_scripts_ready": True,
            "shared_models_ready": True,
        }
    )
    endpoints = dev.cloud_endpoints("demo.ts.net")
    urls = {
        "https://demo.ts.net/": dev.HttpResponse(200),
        "https://demo.ts.net:8443/healthz": dev.HttpResponse(200),
        "https://demo.ts.net:8444/api/llm/providers": dev.HttpResponse(200),
        "https://demo.ts.net:8445/": dev.HttpResponse(200),
        "https://demo.ts.net:8446/healthz": dev.HttpResponse(503),
    }
    runner = FakeStatusRunner(
        [command_result("ssh", "remote-preflight", stdout=remote_state)], urls
    )

    status = dev.inspect_cloud_status(request, endpoints, runner)
    serialized = json.dumps(dev.stack_status_to_dict(status))

    assert status.target == "cloud"
    assert status.release_sha == "1" * 40
    assert (status.container_total, status.container_running) == (None, None)
    assert status.healthy_endpoints == 4
    assert runner.commands[0][0] == "ssh"
    assert all(
        forbidden not in " ".join(command)
        for command in runner.commands
        for forbidden in ("deploy", "docker", "tailscale")
    )
    assert "super-secret-token" not in serialized
    assert "postgresql://" not in serialized
    assert "PRIVATE KEY" not in serialized


def test_inspect_status_does_not_store_sensitive_probe_exception_text(tmp_path: Path):
    urls = {
        "http://localhost:5173/": URLError(
            "super-secret-token password=correct-horse "
            "Bearer bearer-secret postgresql://user:pass@db/private "
            "-----BEGIN PRIVATE KEY-----"
        ),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [command_result("docker", "info", returncode=1)], urls
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)
    serialized = json.dumps(dev.stack_status_to_dict(status))

    assert status.endpoint_results[0].status == "network_error"
    assert status.healthy_endpoints == 4
    assert runner.commands == [("docker", "info")]
    assert status.warnings == ("local Docker daemon is unavailable",)
    assert "super-secret-token" not in serialized
    assert "correct-horse" not in serialized
    assert "bearer-secret" not in serialized
    assert "postgresql://" not in serialized
    assert "PRIVATE KEY" not in serialized


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (TimeoutError(), "timeout", None),
        (ssl.SSLError("certificate failed"), "tls_error", None),
        (URLError(socket.gaierror()), "dns_error", None),
        (socket.gaierror(), "dns_error", None),
        (HTTPError("http://localhost/", 503, "unavailable", {}, None), "http_error", 503),
        (URLError("connection refused"), "network_error", None),
    ],
    ids=["timeout", "tls", "url-dns", "direct-dns", "http", "url"],
)
def test_endpoint_probe_classifies_expected_transport_failures(
    failure: BaseException, expected_status: str, expected_code: int | None
):
    runner = FakeStatusRunner([], {"http://localhost:9999/": failure})

    result = dev._endpoint_status("test", "http://localhost:9999", "/", runner)

    assert result.status == expected_status
    assert result.http_status == expected_code


@pytest.mark.parametrize("failure", [AssertionError("bug"), TypeError("bug")])
def test_endpoint_probe_propagates_unexpected_programming_errors(failure: BaseException):
    runner = FakeStatusRunner([], {"http://localhost:9999/": failure})

    with pytest.raises(type(failure)):
        dev._endpoint_status("test", "http://localhost:9999", "/", runner)


def make_cloud_request(tmp_path: Path) -> ReleaseRequest:
    identity = tmp_path / "agent.pem"
    identity.write_text("not-a-real-key\n", encoding="utf-8")
    return ReleaseRequest(
        repo=tmp_path,
        revision="1" * 40,
        artifact_root=tmp_path / "artifacts",
        ssh=SshConfig(host="server.example.invalid", user="ubuntu", identity=identity),
    )


def test_inspect_cloud_status_propagates_unexpected_discovery_errors(tmp_path: Path):
    runner = FakeStatusRunner([TypeError("programmer error")], {})

    with pytest.raises(TypeError, match="programmer error"):
        dev.inspect_cloud_status(
            make_cloud_request(tmp_path),
            dev.cloud_endpoints("demo.ts.net"),
            runner,
        )


def test_default_status_runner_does_not_follow_same_origin_redirect():
    requests: list[str] = []

    class SameOriginRedirect(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/redirected")
            else:
                self.send_response(204)
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    with temporary_http_server(SameOriginRedirect) as server:
        host, port = server.server_address
        result = dev._endpoint_status(
            "test", f"http://{host}:{port}", "/", dev.DefaultStackStatusRunner()
        )

    assert (result.status, result.http_status) == ("http_error", 302)
    assert requests == ["/"]


def test_default_status_runner_does_not_follow_cross_origin_redirect():
    target_requests: list[str] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    with temporary_http_server(Target) as target:
        target_host, target_port = target.server_address

        class CrossOriginRedirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://{target_host}:{target_port}/target")
                self.end_headers()

            def log_message(self, _format, *_args):
                pass

        with temporary_http_server(CrossOriginRedirect) as source:
            source_host, source_port = source.server_address
            result = dev._endpoint_status(
                "test",
                f"http://{source_host}:{source_port}",
                "/",
                dev.DefaultStackStatusRunner(),
            )

    assert (result.status, result.http_status) == ("http_error", 302)
    assert target_requests == []


def test_inspect_local_status_handles_release_error_without_running_compose(
    tmp_path: Path,
):
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [ReleaseError("super-secret-token postgresql://user:pass@db/private")], urls
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)
    serialized = json.dumps(dev.stack_status_to_dict(status))

    assert status.container_total is None
    assert status.warnings == ("local Docker daemon is unavailable",)
    assert runner.commands == [("docker", "info")]
    assert all(
        forbidden not in command
        for command in runner.commands
        for forbidden in ("up", "start", "restart", "build")
    )
    assert "super-secret-token" not in serialized
    assert "postgresql://" not in serialized


def test_inspect_local_status_aggregates_scaled_services_from_json_array(
    tmp_path: Path,
):
    containers = json.dumps(
        [
            {"Service": "postgres", "State": "exited"},
            {"Service": "redis", "State": "running"},
            {"Service": "postgres", "State": "running"},
            {"Service": "redis", "State": "exited"},
            {"Service": "edge-gateway", "State": "exited"},
            {"Service": "edge-gateway", "State": "exited"},
            {"Service": "llm-gateway", "State": "running"},
            {"Service": "observability-collector", "State": "running"},
            {"Service": "hmi", "State": "running"},
            {"Service": "dashboard", "State": "exited"},
            {"Service": "unrelated-current-project-service", "State": "running"},
        ]
    )
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [
            command_result("docker", "info"),
            command_result("docker", "compose", stdout=containers),
        ],
        urls,
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)

    assert (status.container_total, status.container_running) == (11, 6)
    assert status.warnings == (
        "required local service is not running: dashboard",
        "required local service is not running: edge-gateway",
    )
    assert not any("postgres" in warning for warning in status.warnings)
    assert not any("redis" in warning for warning in status.warnings)


def test_inspect_local_status_marks_counts_unknown_when_compose_json_is_malformed(
    tmp_path: Path,
):
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [
            command_result("docker", "info"),
            command_result("docker", "compose", stdout="{not JSON"),
        ],
        urls,
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)

    assert (status.container_total, status.container_running) == (None, None)
    assert status.warnings == ("local Compose status returned invalid JSON",)


def test_inspect_local_status_marks_counts_unknown_when_compose_command_fails(
    tmp_path: Path,
):
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [
            command_result("docker", "info"),
            command_result("docker", "compose", returncode=1),
        ],
        urls,
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)

    assert (status.container_total, status.container_running) == (None, None)
    assert status.warnings == ("local Compose status is unavailable",)


def test_parse_compose_ps_rejects_deeply_nested_json_without_recursion_error():
    payload = "[" * 10_000 + "]" * 10_000

    assert dev._parse_compose_ps(payload) is None


def test_parse_compose_ps_rejects_oversized_and_overrecord_payloads():
    oversized = " " * (dev.COMPOSE_STATUS_MAX_BYTES + 1)
    overrecord = json.dumps(
        [
            {"Service": f"service-{index}", "State": "running"}
            for index in range(dev.COMPOSE_STATUS_MAX_RECORDS + 1)
        ]
    )

    assert dev._parse_compose_ps(oversized) is None
    assert dev._parse_compose_ps(overrecord) is None


@pytest.mark.parametrize(
    "oneoff_labels",
    [
        {"com.docker.compose.oneoff": "True"},
        "com.docker.compose.oneoff=True,com.docker.compose.project=car-agent",
    ],
    ids=["labels-dict", "labels-string"],
)
def test_oneoff_container_does_not_make_stopped_required_service_ready(
    tmp_path: Path, oneoff_labels: object
):
    containers = json.dumps(
        [
            {"Service": "postgres", "State": "exited", "Labels": {}},
            {"Service": "postgres", "State": "running", "Labels": oneoff_labels},
            {"Service": "redis", "State": "running", "Labels": {}},
            {"Service": "edge-gateway", "State": "running", "Labels": {}},
            {"Service": "llm-gateway", "State": "running", "Labels": {}},
            {"Service": "observability-collector", "State": "running", "Labels": {}},
            {"Service": "hmi", "State": "running", "Labels": {}},
            {"Service": "dashboard", "State": "running", "Labels": {}},
        ]
    )
    urls = {
        "http://localhost:5173/": dev.HttpResponse(200),
        "http://localhost:8090/healthz": dev.HttpResponse(200),
        "http://localhost:50059/api/llm/providers": dev.HttpResponse(200),
        "http://localhost:5174/": dev.HttpResponse(200),
        "http://localhost:8092/healthz": dev.HttpResponse(200),
    }
    runner = FakeStatusRunner(
        [
            command_result("docker", "info"),
            command_result("docker", "compose", stdout=containers),
        ],
        urls,
    )

    status = dev.inspect_local_status(tmp_path, dev.LOCAL_ENDPOINTS, runner)

    assert (status.container_total, status.container_running) == (8, 7)
    assert status.warnings == ("required local service is not running: postgres",)


def test_cloud_release_argv_is_a_python_delegate_with_dry_run_and_apply():
    repo = Path("C:/repo")

    assert dev.cloud_release_argv(repo, "deploy", "a" * 40, apply=False) == [
        sys.executable,
        str(repo / "scripts" / "cloud_release.py"),
        "deploy",
        "--sha",
        "a" * 40,
    ]
    assert dev.cloud_release_argv(repo, "deploy", "a" * 40, apply=True)[-1] == "--apply"
    assert dev.cloud_release_argv(repo, "verify", "HEAD", apply=True) == [
        sys.executable,
        str(repo / "scripts" / "cloud_release.py"),
        "verify",
    ]
    with pytest.raises(dev.DevStackError, match="unsupported"):
        dev.cloud_release_argv(repo, "rollback", "HEAD", apply=False)


class FakeCliRunner:
    def __init__(self, result: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.result = result

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        return CommandResult(tuple(argv), self.result, "", "")


def test_cli_target_show_and_set_emit_target_and_source(tmp_path: Path):
    events: list[dict[str, object]] = []

    assert cli.main(["target", "show"], repo=tmp_path, emit=events.append) == 0
    assert events == [{"status": "target", "target": "local", "source": "default"}]
    assert cli.main(["target", "set", "cloud"], repo=tmp_path, emit=events.append) == 0
    assert (tmp_path / "dev-stack.local").read_text(encoding="utf-8") == "target=cloud\n"
    assert events[-1] == {"status": "target", "target": "cloud", "source": "file"}


def test_cli_deploy_rejects_local_and_delegates_cloud_without_echoing_identity(tmp_path: Path):
    runner = FakeCliRunner()
    assert cli.main(["deploy"], repo=tmp_path, release_runner=runner) == 2
    assert runner.calls == []

    dev.set_target(tmp_path, "cloud")
    identity = tmp_path / "actual-secret-identity.pem"
    events: list[dict[str, object]] = []
    arguments = ["--host", "dev.example", "--user", "alice", "--identity", str(identity), "--kex-algorithms", "curve25519-sha256"]
    assert cli.main([*arguments, "deploy", "--sha", "a" * 40], repo=tmp_path, release_runner=runner, emit=events.append) == 0
    dry_run_argv = runner.calls[-1]
    assert dry_run_argv[:11] == (sys.executable, str(tmp_path / "scripts" / "cloud_release.py"), "--host", "dev.example", "--user", "alice", "--identity", str(identity), "--kex-algorithms", "curve25519-sha256", "deploy")
    assert dry_run_argv[-2:] == ("--sha", "a" * 40)
    assert cli.main([*arguments, "deploy", "--sha", "a" * 40, "--apply"], repo=tmp_path, release_runner=runner, emit=events.append) == 0
    assert runner.calls[-1][-1] == "--apply"
    assert events[-1]["target"] == "cloud"
    assert str(identity) not in json.dumps(events)


@pytest.mark.parametrize("command", ("verify", "hmi", "dashboard"))
def test_cli_unimplemented_actions_fail_closed(command: str, tmp_path: Path):
    events: list[dict[str, object]] = []

    assert cli.main([command], repo=tmp_path, emit=events.append) == 2
    assert events == [
        {"status": "configuration_rejected", "target": "local", "source": "default"}
    ]


@pytest.mark.parametrize(
    "argv",
    (
        ("target", "set", "remote"),
        ("unknown-command",),
        ("target", "set"),
    ),
)
def test_cli_parser_errors_are_redacted_json_with_exit_two(
    argv: tuple[str, ...], tmp_path: Path
):
    identity = str(tmp_path / "secret-identity.pem")
    events: list[dict[str, object]] = []

    assert cli.main(["--identity", identity, *argv], repo=tmp_path, emit=events.append) == 2
    assert events == [
        {
            "status": "parse_error",
            "target": None,
            "source": None,
            "error": "invalid command arguments",
        }
    ]
    assert identity not in json.dumps(events)

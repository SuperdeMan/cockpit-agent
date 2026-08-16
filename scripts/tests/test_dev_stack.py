from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import dev_stack_lib as dev
from scripts.cloud_release_lib import (
    CommandResult,
    ReleaseError,
    ReleaseRequest,
    SshConfig,
)


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
                "docker", "compose", "-f", "compose.yaml", "ps", "--format", "json",
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
        ("docker", "compose", "-f", "compose.yaml", "ps", "--format", "json"),
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


def test_inspect_status_redacts_sensitive_probe_exception_text(tmp_path: Path):
    urls = {
        "http://localhost:5173/": RuntimeError(
            "super-secret-token postgresql://user:pass@db/private "
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
    assert "postgresql://" not in serialized
    assert "PRIVATE KEY" not in serialized


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

    assert status.container_total == 0
    assert status.warnings == ("local Docker daemon is unavailable",)
    assert runner.commands == [("docker", "info")]
    assert all(
        forbidden not in command
        for command in runner.commands
        for forbidden in ("up", "start", "restart", "build")
    )
    assert "super-secret-token" not in serialized
    assert "postgresql://" not in serialized

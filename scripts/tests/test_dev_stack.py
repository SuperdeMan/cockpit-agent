from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import dev_stack_lib as dev


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
    "content",
    [
        b"target=remote\n",
        b"target=cloud\ntarget=local\n",
        b"target=cloud\nextra=value\n",
        b"target=cloud+extra=x\n",
        b"target=\n",
        b"\xef\xbb\xbftarget=cloud\n",
        b"target=cloud\n\n",
    ],
    ids=[
        "unknown-target",
        "duplicate-key",
        "second-nonempty-line",
        "extra-assignment",
        "empty-value",
        "bom",
        "second-empty-line",
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
        assert source_path == tmp_path / "dev-stack.local.partial"
        assert source_path.read_bytes() == b"target=cloud\n"
        assert destination_path == stack_target
        assert stack_target.read_bytes() == b"target=local\n"
        real_replace(source, destination)

    monkeypatch.setattr(dev.os, "fsync", record_fsync)
    monkeypatch.setattr(dev.os, "replace", record_replace)

    dev.set_target(tmp_path, "cloud")

    assert stack_target.read_bytes() == b"target=cloud\n"
    assert not (tmp_path / "dev-stack.local.partial").exists()
    assert root_env.read_bytes() == b"SECRET_VALUE=must-not-be-read-or-changed\n"
    assert [event for event, _ in observed] == ["fsync", "replace"]


def test_set_target_removes_only_its_partial_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stack_target = write_stack_target(tmp_path, b"target=local\n")
    partial = tmp_path / "dev-stack.local.partial"

    def fail_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        raise OSError("replace unavailable")

    monkeypatch.setattr(dev.os, "replace", fail_replace)

    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, "cloud")

    assert stack_target.read_bytes() == b"target=local\n"
    assert not partial.exists()


def test_set_target_does_not_delete_a_partial_it_no_longer_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_stack_target(tmp_path, b"target=local\n")
    partial = tmp_path / "dev-stack.local.partial"

    def replace_partial_then_fail(
        source: os.PathLike[str], destination: os.PathLike[str]
    ) -> None:
        Path(source).unlink()
        partial.write_bytes(b"user-created-partial")
        raise OSError("replace unavailable")

    monkeypatch.setattr(dev.os, "replace", replace_partial_then_fail)

    with pytest.raises(dev.DevStackError):
        dev.set_target(tmp_path, "cloud")

    assert partial.read_bytes() == b"user-created-partial"


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
    ["", "example.com", "https://example.ts.net", "UPPER.ts.net", ".ts.net"],
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

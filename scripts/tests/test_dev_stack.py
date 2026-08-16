from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

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
        "SPACED": "  retained leading and trimmed trailing",
        "SHELL": "$(uname)-${HOME}-`id`",
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

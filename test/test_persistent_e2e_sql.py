from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.e2e import CaseRecorder, CleanupFailure


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "test"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

SCRIPT_NAMES = (
    "e2e_memory_graph",
    "e2e_voiceprint",
    "e2e_ledger",
    "e2e_reminder",
    "e2e_geofence",
    "e2e_scene",
)


def load_script(name: str):
    module_name = f"persistent_sql_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        TEST_ROOT / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_sql_source_requires_timeout_and_returncode_check(name):
    source = (TEST_ROOT / f"{name}.py").read_text(encoding="utf-8")
    sql_body = source[source.index("def sql("):source.index("\ndef quoted", source.index("def sql("))]
    assert "timeout=" in sql_body
    assert "returncode" in sql_body


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_sql_nonzero_exit_fails_closed(name, monkeypatch):
    module = load_script(name)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=7,
            stdout="0\n",
            stderr="database unavailable",
        ),
    )
    with pytest.raises(RuntimeError, match="SQL"):
        module.sql("SELECT 1")


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_sql_timeout_fails_closed_as_runtime_error(name, monkeypatch):
    module = load_script(name)

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["psql"], 60)

    monkeypatch.setattr(module.subprocess, "run", timed_out)
    with pytest.raises(RuntimeError, match="timed out"):
        module.sql("SELECT 1")


@pytest.mark.parametrize("raw", ["", "not-an-integer", "1.5", "-1"])
@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_namespace_count_rejects_empty_noninteger_or_negative(
    name,
    raw,
    monkeypatch,
):
    module = load_script(name)
    monkeypatch.setattr(module, "sql", lambda _query: raw)
    count = module.ledger_count if name == "e2e_ledger" else module.namespace_count
    with pytest.raises(RuntimeError, match="count"):
        count("e2e-run-case")


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_cleanup_query_failure_is_not_swallowed(name, monkeypatch):
    module = load_script(name)

    def query_failed(*_args, **_kwargs):
        raise RuntimeError("namespace SQL query failed")

    if name in {"e2e_memory_graph", "e2e_voiceprint"}:
        class Channel:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Stub:
            def ForgetUser(self, *_args, **_kwargs):
                return SimpleNamespace(ok=True)

        monkeypatch.setattr(
            module.grpc,
            "insecure_channel",
            lambda *_args, **_kwargs: Channel(),
        )
        monkeypatch.setattr(
            module.memory_pb2_grpc,
            "MemoryStub",
            lambda _channel: Stub(),
        )
        monkeypatch.setattr(module, "namespace_count", query_failed)
        args = ("e2e-run-case",)
    else:
        monkeypatch.setattr(module, "sql", query_failed)
        if name == "e2e_geofence":
            monkeypatch.setattr(module, "debug_vehicle", lambda *_args: None)
            args = ("e2e-run-case", {"lat": 1, "lng": 2})
        elif name == "e2e_scene":
            args = ("e2e-run-case", {})
        else:
            args = ("e2e-run-case",)

    with pytest.raises(RuntimeError, match="namespace SQL query failed"):
        module.cleanup_namespace(*args)


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_cleanup_query_failure_becomes_case_recorder_isolation_failure(
    name,
    monkeypatch,
    tmp_path,
):
    module = load_script(name)
    user = f"e2e-run-sql-{name}"

    def query_failed(*_args, **_kwargs):
        raise RuntimeError("namespace SQL query failed")

    if name in {"e2e_memory_graph", "e2e_voiceprint"}:
        class CleanupChannel:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Stub:
            def ForgetUser(self, *_args, **_kwargs):
                return SimpleNamespace(ok=True)

        monkeypatch.setattr(
            module.grpc,
            "insecure_channel",
            lambda *_args, **_kwargs: CleanupChannel(),
        )
        monkeypatch.setattr(
            module.memory_pb2_grpc,
            "MemoryStub",
            lambda _channel: Stub(),
        )
        monkeypatch.setattr(module, "namespace_count", query_failed)
        callback = lambda: module.cleanup_namespace(user)
    else:
        monkeypatch.setattr(module, "sql", query_failed)
        if name == "e2e_geofence":
            monkeypatch.setattr(module, "debug_vehicle", lambda *_args: None)
            callback = lambda: module.cleanup_namespace(
                user,
                {"lat": 1, "lng": 2},
            )
        elif name == "e2e_scene":
            callback = lambda: module.cleanup_namespace(user, {})
        else:
            callback = lambda: module.cleanup_namespace(user)

    result_file = tmp_path / f"{name}.json"
    recorder = CaseRecorder(env={
        "E2E_RUN_ID": "e2e-run-sql",
        "E2E_TEST_ID": name,
        "E2E_USER_ID": user,
        "E2E_SESSION_PREFIX": f"{user}-session",
        "E2E_RESULT_FILE": str(result_file),
        "E2E_ARTIFACT_DIR": str(tmp_path / f"{name}-artifacts"),
        "E2E_LANE": "milestone",
        "E2E_PROFILE": "root",
        "E2E_IDENTITY_TOKEN": "e2e.v1.payload.signature",
    })
    with pytest.raises(CleanupFailure):
        with recorder:
            recorder.pass_case("body")
            recorder.register_cleanup(user, callback)

    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["failures"][-1]["code"] == "cleanup_failed"

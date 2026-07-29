from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "test"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))


def load_script(name: str):
    module_name = f"namespace_contract_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        TEST_ROOT / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class Channel:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def install_memory_stub(monkeypatch, module, stub) -> None:
    monkeypatch.setattr(
        module.grpc,
        "insecure_channel",
        lambda *_args, **_kwargs: Channel(),
    )
    monkeypatch.setattr(
        module.memory_pb2_grpc,
        "MemoryStub",
        lambda _channel: stub,
    )


def test_memory_namespace_count_covers_profile_relations_memories_and_sessions(
    monkeypatch,
):
    module = load_script("e2e_memory")

    class Stub:
        def ExportUser(self, *_args, **_kwargs):
            return SimpleNamespace(json=json.dumps({
                "profile": {"places": {"home": "x"}},
                "memories": [{"id": "m1"}],
                "relations": [{"id": "r1"}],
            }))

        def GetSession(self, request, **_kwargs):
            count = {"session-a": 2, "session-b": 1}[request.session_id]
            return SimpleNamespace(turns=[object()] * count)

    install_memory_stub(monkeypatch, module, Stub())
    assert module.namespace_count(
        "e2e-run-case",
        ("session-a", "session-b"),
    ) == 6


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-json",
        "[]",
        '{"profile": [], "memories": [], "relations": []}',
        '{"profile": {}, "memories": {}, "relations": []}',
        '{"profile": {}, "memories": [], "relations": {}}',
    ],
)
def test_memory_namespace_count_rejects_empty_or_invalid_export(
    monkeypatch,
    raw,
):
    module = load_script("e2e_memory")

    class Stub:
        def ExportUser(self, *_args, **_kwargs):
            return SimpleNamespace(json=raw)

    install_memory_stub(monkeypatch, module, Stub())
    with pytest.raises(RuntimeError, match="export"):
        module.namespace_count("e2e-run-case", ())


def test_memory_cleanup_rejects_unsuccessful_forget_response(monkeypatch):
    module = load_script("e2e_memory")

    class Stub:
        def ForgetUser(self, *_args, **_kwargs):
            return SimpleNamespace(ok=False)

    install_memory_stub(monkeypatch, module, Stub())
    with pytest.raises(RuntimeError, match="ForgetUser"):
        module.cleanup_namespace("e2e-run-case", ())


def test_geofence_missing_location_fails_before_any_mutation(monkeypatch):
    module = load_script("e2e_geofence")

    class Recorder:
        failures: list[tuple[str, str, str]] = []
        cleanups: list[tuple[str, object]] = []

        def ws_url(self):
            return "ws://127.0.0.1/ws"

        def user_id(self):
            return "e2e-run-case"

        def session_id(self, _number):
            return "e2e-run-case-session-1"

        def run_id(self):
            return "e2e-run"

        def fail_case(self, case_id, code, detail):
            self.failures.append((case_id, code, detail))

        def register_cleanup(self, owner, callback):
            self.cleanups.append((owner, callback))

    recorder = Recorder()
    monkeypatch.setattr(module, "vehicle_state", lambda: {})

    def continued_after_preflight(_user):
        raise AssertionError("run continued after missing location")

    monkeypatch.setattr(module, "namespace_count", continued_after_preflight)
    asyncio.run(module.run(recorder))

    assert recorder.failures == [(
        "location_snapshot",
        "isolation_precondition",
        "vehicle location snapshot is missing",
    )]
    assert recorder.cleanups == []
